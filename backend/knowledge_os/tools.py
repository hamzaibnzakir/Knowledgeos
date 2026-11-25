"""The single, typed tool surface shared by the built-in agent, the MCP server
and (later) voice. Every tool validates its arguments, checks scope, and goes
through the vault/memory services; there is no shell or raw filesystem access.
"""
from __future__ import annotations

import posixpath
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from . import db, indexer, memory, retrieval, vault
from .markdown import link_key, set_frontmatter, split_frontmatter
from .util import Forbidden, KOSError, jdumps, jloads, now, record_event

READ, WRITE = "knowledge:read", "knowledge:write"
FULL = "knowledge:full"


@dataclass
class ToolContext:
    actor: str = "web"
    scopes: set[str] = field(default_factory=lambda: {READ, WRITE})
    chat_id: str | None = None
    confirm_destructive: bool = False   # web agent: destructive tools become pending actions

    def allows(self, scope: str) -> bool:
        return scope in self.scopes or FULL in self.scopes or (scope == READ and WRITE in self.scopes)


@dataclass
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    handler: Callable[[ToolContext, Any], Any]
    scope: str = READ
    destructive: bool = False
    summarize: Callable[[Any], str] | None = None

    def openai_schema(self) -> dict:
        schema = self.params.model_json_schema()
        schema.pop("title", None)
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": schema}}


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, params: type[BaseModel], *, scope: str = READ, destructive: bool = False,
         summarize: Callable[[Any], str] | None = None):
    def deco(fn):
        REGISTRY[name] = Tool(name, description, params, fn, scope, destructive, summarize)
        return fn
    return deco


def call(name: str, args: dict | None, ctx: ToolContext) -> Any:
    t = REGISTRY.get(name)
    if not t:
        raise KOSError(f"Unknown tool '{name}'")
    if not ctx.allows(t.scope):
        raise Forbidden(f"'{name}' requires the {t.scope} scope")
    try:
        parsed = t.params.model_validate(args or {})
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
        raise KOSError(f"Invalid arguments for {name}: {msgs}") from e
    if t.destructive and ctx.confirm_destructive:
        return _queue_pending(t, parsed, ctx)
    return t.handler(ctx, parsed)


def _queue_pending(t: Tool, parsed: BaseModel, ctx: ToolContext) -> dict:
    pid = uuid.uuid4().hex[:16]
    summary = t.summarize(parsed) if t.summarize else f"{t.name}({parsed.model_dump_json()})"
    with db.connect() as conn:
        conn.execute("INSERT INTO pending_actions(id,chat_id,tool,args,summary,status,created_at) VALUES(?,?,?,?,?,?,?)",
                     (pid, ctx.chat_id, t.name, parsed.model_dump_json(), summary, "pending", now()))
        record_event(conn, f"{t.name}.requested", "pending", {"pending_id": pid, "summary": summary},
                     actor=ctx.actor, chat_id=ctx.chat_id)
    return {"status": "needs_confirmation", "pending_id": pid, "summary": summary,
            "message": "This action is waiting for the user to approve it in the app. It has NOT been performed."}


def resolve_pending(pending_id: str, approve: bool, actor: str = "web") -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM pending_actions WHERE id=?", (pending_id,)).fetchone()
        if not row:
            raise KOSError("Pending action not found", status=404)
        if row["status"] != "pending":
            raise KOSError(f"This action was already {row['status']}", status=409)
        conn.execute("UPDATE pending_actions SET status=?, resolved_at=? WHERE id=?",
                     ("approved" if approve else "rejected", now(), pending_id))
    if not approve:
        with db.connect() as conn:
            record_event(conn, f"{row['tool']}.rejected", "rejected", {"pending_id": pending_id}, actor=actor,
                         chat_id=row["chat_id"])
        return {"status": "rejected"}
    t = REGISTRY[row["tool"]]
    ctx = ToolContext(actor=actor, chat_id=row["chat_id"], scopes={FULL})
    result = t.handler(ctx, t.params.model_validate(jloads(row["args"], {})))
    return {"status": "approved", "result": result}


def list_pending(chat_id: str | None = None) -> list[dict]:
    with db.connect() as conn:
        if chat_id:
            rows = conn.execute("SELECT * FROM pending_actions WHERE chat_id=? AND status='pending' ORDER BY created_at",
                                (chat_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM pending_actions WHERE status='pending' ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- helpers

def _note_meta(path: str) -> dict:
    with db.connect() as conn:
        r = conn.execute("SELECT title,tags,aliases,properties,updated_at,word_count FROM notes WHERE path=?", (path,)).fetchone()
    if not r:
        return {}
    return {"title": r["title"], "tags": jloads(r["tags"], []), "aliases": jloads(r["aliases"], []),
            "properties": jloads(r["properties"], {}), "updated_at": r["updated_at"], "word_count": r["word_count"]}


def _strip(result: dict) -> dict:
    result = dict(result)
    result.pop("event", None)
    return result


# --------------------------------------------------------------------------- read tools

class SearchArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="What to look for")
    limit: int = Field(10, ge=1, le=50)
    mode: Literal["hybrid", "keyword", "semantic"] = "hybrid"
    folder: str | None = Field(None, description="Restrict to a folder, e.g. 'Projects'")
    tag: str | None = Field(None, description="Restrict to a tag, e.g. 'meta-ads'")


@tool("search_notes", "Search the vault (keyword + semantic). Returns paths, titles and short snippets; "
      "read_note only the results you need.", SearchArgs)
def search_notes(ctx: ToolContext, a: SearchArgs):
    hits = retrieval.search(a.query, limit=a.limit, mode=a.mode, folder=a.folder, tag=a.tag)
    for h in hits:
        h["snippet"] = h["snippet"].replace(retrieval.HL_START, "**").replace(retrieval.HL_END, "**")
    return hits


class ContextArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    max_tokens: int = Field(2500, ge=200, le=8000, description="Token budget for the returned context")
    folder: str | None = None
    include_memories: bool = True


@tool("retrieve_context", "Get a compact, ranked context package (relevant excerpts from notes plus relevant "
      "memories) for a question, within a token budget. Cheaper than reading whole notes.", ContextArgs)
def retrieve_context(ctx: ToolContext, a: ContextArgs):
    pkg = retrieval.build_context(a.query, budget=a.max_tokens, include_memories=a.include_memories, folder=a.folder)
    return {"context": pkg["text"], "sources": pkg["sources"], "tokens": pkg["tokens"]}


class PathArgs(BaseModel):
    path: str = Field(..., description="Vault-relative path, e.g. 'Projects/EcomLab.md'")


class ReadArgs(PathArgs):
    max_chars: int | None = Field(None, ge=200, le=200000, description="Truncate long notes")


@tool("read_note", "Read one note: Markdown content, content hash (pass it back as expected_hash when "
      "editing) and parsed metadata.", ReadArgs)
def read_note(ctx: ToolContext, a: ReadArgs):
    n = vault.read(a.path)
    fm, _, _ = split_frontmatter(n["content"])
    content = n["content"]
    truncated = bool(a.max_chars and len(content) > a.max_chars)
    if truncated:
        content = content[: a.max_chars]
    return {"path": n["path"], "content": content, "truncated": truncated, "hash": n["hash"],
            "frontmatter": fm, **{k: v for k, v in _note_meta(n["path"]).items() if k != "properties"}}


class ListArgs(BaseModel):
    folder: str | None = None
    tag: str | None = None
    limit: int = Field(50, ge=1, le=500)
    sort: Literal["updated", "title", "created"] = "updated"


@tool("list_notes", "List notes (path, title, tags, updated time), optionally filtered by folder or tag.", ListArgs)
def list_notes(ctx: ToolContext, a: ListArgs):
    return query_notes(folder=a.folder, tag=a.tag, limit=a.limit, sort=a.sort)


def query_notes(folder: str | None = None, tag: str | None = None, limit: int = 200, sort: str = "updated",
                offset: int = 0) -> list[dict]:
    fsql, fargs = retrieval._filter_sql(folder, tag)
    order = {"updated": "updated_at DESC", "title": "lower(title)", "created": "created_at DESC"}.get(sort, "updated_at DESC")
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT n.path,n.title,n.folder,n.tags,n.aliases,n.updated_at,n.created_at,n.word_count,n.excerpt,"
            f" EXISTS(SELECT 1 FROM favorites f WHERE f.path=n.path) AS favorite FROM notes n WHERE 1=1 {fsql}"
            f" ORDER BY {order} LIMIT ? OFFSET ?", (*fargs, limit, offset)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = jloads(d["tags"], [])
        d["aliases"] = jloads(d["aliases"], [])
        d["favorite"] = bool(d["favorite"])
        out.append(d)
    return out


@tool("get_note_context", "Metadata, headings, outgoing links, backlinks and related notes for one note "
      "(no full text).", PathArgs)
def get_note_context(ctx: ToolContext, a: PathArgs):
    clean = vault.normalize_rel(a.path)
    out = retrieval.note_context(clean)
    if not out:
        raise KOSError("Note not found", status=404)
    return out


class RelatedArgs(PathArgs):
    limit: int = Field(10, ge=1, le=30)


@tool("find_related", "Notes related to a note by links, shared tags and similar content.", RelatedArgs)
def find_related(ctx: ToolContext, a: RelatedArgs):
    return retrieval.related_notes(vault.normalize_rel(a.path), limit=a.limit)


@tool("get_backlinks", "Notes that link to the given note.", PathArgs)
def get_backlinks(ctx: ToolContext, a: PathArgs):
    return [{"path": p} for p in indexer.backlinks(vault.normalize_rel(a.path))]


class GraphArgs(BaseModel):
    path: str | None = Field(None, description="Center note for a local graph. Omit for the whole vault.")
    depth: int = Field(1, ge=1, le=3)
    include_tags: bool = False


@tool("get_graph", "Knowledge graph nodes and edges: a local graph around one note, or the whole vault.", GraphArgs)
def get_graph(ctx: ToolContext, a: GraphArgs):
    if a.path:
        return indexer.local_graph(vault.normalize_rel(a.path), depth=a.depth, include_tags=a.include_tags)
    return indexer.graph(include_tags=a.include_tags)


class Empty(BaseModel):
    pass


@tool("list_tags", "All tags with note counts.", Empty)
def list_tags(ctx: ToolContext, a: Empty):
    return indexer.tags()


class MemSearchArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(8, ge=1, le=50)


@tool("search_memory", "Search saved memories (durable facts about the user) by meaning.", MemSearchArgs)
def search_memory(ctx: ToolContext, a: MemSearchArgs):
    return memory.search(a.query, k=a.limit)


class MemListArgs(BaseModel):
    category: str | None = None
    limit: int = Field(100, ge=1, le=500)


@tool("list_memories", "List saved memories, newest first. Only explicitly saved memories appear here.", MemListArgs)
def list_memories(ctx: ToolContext, a: MemListArgs):
    return memory.list_memories(category=a.category, limit=a.limit)


class SkillListArgs(BaseModel):
    limit: int = Field(100, ge=1, le=500)


@tool("list_skills", "List reusable procedures stored in the Skills folder (title, path, description).", SkillListArgs)
def list_skills(ctx: ToolContext, a: SkillListArgs):
    rows = query_notes(folder="Skills", limit=a.limit)
    return [{"path": r["path"], "title": r["title"], "description": r["excerpt"], "tags": r["tags"],
             "updated_at": r["updated_at"]} for r in rows]


class SkillReadArgs(BaseModel):
    name: str = Field(..., description="Skill title or path")


@tool("read_skill", "Read one skill by title or path.", SkillReadArgs)
def read_skill(ctx: ToolContext, a: SkillReadArgs):
    candidates = [a.name] if a.name.lower().startswith("skills/") else [f"Skills/{a.name}"]
    for c in candidates:
        if vault.exists(c):
            return read_note(ctx, ReadArgs(path=c))
    matches = [p for p in indexer.resolve_key(link_key(a.name)) if p.startswith("Skills/")]
    if not matches:
        raise KOSError("Skill not found", status=404)
    return read_note(ctx, ReadArgs(path=matches[0]))


class AuditArgs(BaseModel):
    limit: int = Field(50, ge=1, le=500)
    actor: str | None = None


@tool("list_audit_events", "Recent recorded actions (who changed what, when). Use it to verify your own writes.",
      AuditArgs)
def list_audit_events(ctx: ToolContext, a: AuditArgs):
    return audit_events(limit=a.limit, actor=a.actor)


def audit_events(limit: int = 100, actor: str | None = None, chat_id: str | None = None,
                 before_id: int | None = None) -> list[dict]:
    sql, args = "SELECT id,chat_id,action,status,details,actor,created_at FROM action_events WHERE 1=1", []
    if actor:
        sql += " AND actor=?"
        args.append(actor)
    if chat_id:
        sql += " AND chat_id=?"
        args.append(chat_id)
    if before_id:
        sql += " AND id<?"
        args.append(before_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    with db.connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [{**dict(r), "details": jloads(r["details"], {})} for r in rows]


# --------------------------------------------------------------------------- write tools

class CreateArgs(BaseModel):
    path: str = Field(..., description="Vault-relative path; '.md' is added if missing. Folder is created.")
    content: str = Field("", max_length=2_000_000)
    tags: list[str] | None = Field(None, description="Optional tags to put in front matter")


@tool("create_note", "Create a new Markdown note. Fails if a note already exists at the path. Use [[Note Title]] "
      "to link to other notes.", CreateArgs, scope=WRITE)
def create_note(ctx: ToolContext, a: CreateArgs):
    content = a.content
    if a.tags:
        content = set_frontmatter(content, {"tags": sorted({t.lstrip('#') for t in a.tags})})
    return _strip(vault.create(a.path, content, ctx.actor, chat_id=ctx.chat_id))


class UpdateArgs(BaseModel):
    path: str
    content: str = Field(..., max_length=2_000_000, description="The complete new Markdown content")
    expected_hash: str | None = Field(None, description="Hash from read_note; the update is refused if the note "
                                                         "changed since. Strongly recommended.")


@tool("update_note", "Replace the full content of an existing note (previous version is kept in history). "
      "Prefer append_to_note or replace_in_note for small edits.", UpdateArgs, scope=WRITE)
def update_note(ctx: ToolContext, a: UpdateArgs):
    return _strip(vault.write(a.path, a.content, ctx.actor, expected_hash=a.expected_hash, chat_id=ctx.chat_id))


class AppendArgs(BaseModel):
    path: str
    text: str = Field(..., min_length=1, max_length=200_000)
    heading: str | None = Field(None, description="Append at the end of this section (created if missing)")
    create_if_missing: bool = Field(False, description="Create the note if it does not exist")


@tool("append_to_note", "Append text to a note, or to the end of a named section. Safe for logs and journals.",
      AppendArgs, scope=WRITE)
def append_to_note(ctx: ToolContext, a: AppendArgs):
    if not vault.exists(a.path):
        if not a.create_if_missing:
            raise KOSError("Note not found", status=404)
        title = posixpath.splitext(posixpath.basename(vault.normalize_rel(a.path)))[0]
        body = f"# {title}\n\n" + (f"## {a.heading}\n\n" if a.heading else "") + a.text.strip() + "\n"
        return _strip(vault.create(a.path, body, ctx.actor, chat_id=ctx.chat_id))
    return _strip(vault.append(a.path, a.text, ctx.actor, heading=a.heading, chat_id=ctx.chat_id))


class ReplaceArgs(BaseModel):
    path: str
    find: str = Field(..., min_length=1, description="Exact text to find")
    replace: str = Field(..., description="Replacement text")
    replace_all: bool = False


@tool("replace_in_note", "Replace an exact piece of text in a note. Fails if the text is missing or ambiguous.",
      ReplaceArgs, scope=WRITE)
def replace_in_note(ctx: ToolContext, a: ReplaceArgs):
    return _strip(vault.replace_text(a.path, a.find, a.replace, ctx.actor, replace_all=a.replace_all,
                                     chat_id=ctx.chat_id))


class MoveArgs(BaseModel):
    path: str
    new_path: str
    update_links: bool = Field(True, description="Rewrite [[links]] in other notes to the new name")


@tool("move_note", "Rename or move a note; links in other notes are updated.", MoveArgs, scope=WRITE)
def move_note(ctx: ToolContext, a: MoveArgs):
    return _strip(vault.move(a.path, a.new_path, ctx.actor, update_links=a.update_links, chat_id=ctx.chat_id))


class LinkArgs(BaseModel):
    source: str = Field(..., description="Note that gets the link")
    target: str = Field(..., description="Note to link to (title or path)")
    heading: str = Field("Related", description="Section to put the link under")
    context: str | None = Field(None, description="Optional short reason shown after the link")


@tool("add_link", "Add a [[wiki link]] from one note to another under a section (default 'Related'). "
      "Does nothing if the link already exists.", LinkArgs, scope=WRITE)
def add_link(ctx: ToolContext, a: LinkArgs):
    src = vault.normalize_rel(a.source)
    target_name = a.target[:-3] if a.target.lower().endswith(".md") else a.target
    matches = indexer.resolve_key(link_key(target_name))
    if not matches:
        raise KOSError(f"No note found for '{a.target}'", status=404)
    target_path = matches[0]
    for l in indexer.outgoing(src):
        if l["target_path"] == target_path:
            return {"path": src, "target": target_path, "changed": False, "message": "Link already exists"}
    stem = posixpath.splitext(posixpath.basename(target_path))[0]
    label = stem if len(indexer.resolve_key(link_key(stem))) <= 1 else target_path[:-3]
    line = f"- [[{label}]]" + (f" — {a.context.strip()}" if a.context else "")
    res = vault.append(src, line, ctx.actor, heading=a.heading, chat_id=ctx.chat_id)
    return {**_strip(res), "target": target_path}


class RemoveLinkArgs(BaseModel):
    source: str
    target: str


@tool("remove_link", "Remove [[links]] to a target from a note (the link text is kept as plain text).",
      RemoveLinkArgs, scope=WRITE)
def remove_link(ctx: ToolContext, a: RemoveLinkArgs):
    import re
    from .markdown import WIKI_RE
    n = vault.read(a.source)
    key = link_key(a.target)
    keys = {key} | {link_key(p) for p in indexer.resolve_key(key)} | {
        link_key(posixpath.splitext(posixpath.basename(p))[0]) for p in indexer.resolve_key(key)}
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        if link_key(m.group(2)) in keys:
            count += 1
            return m.group(4) or m.group(2)
        return m.group(0)

    new = WIKI_RE.sub(repl, n["content"])
    if not count:
        return {"path": n["path"], "changed": False, "removed": 0}
    res = vault.write(a.source, new, ctx.actor, expected_hash=n["hash"], reason="unlink", chat_id=ctx.chat_id)
    return {**_strip(res), "removed": count}


class PropsArgs(BaseModel):
    path: str
    properties: dict[str, Any] = Field(..., description="Front matter keys to set; null removes a key")


@tool("set_properties", "Set or remove front matter properties (tags, status, aliases, ...) on a note.",
      PropsArgs, scope=WRITE)
def set_properties(ctx: ToolContext, a: PropsArgs):
    n = vault.read(a.path)
    return _strip(vault.write(a.path, set_frontmatter(n["content"], a.properties), ctx.actor,
                              expected_hash=n["hash"], reason="properties", chat_id=ctx.chat_id))


class RememberArgs(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000, description="One short, durable fact")
    category: str = Field("general", description="|".join(memory.CATEGORIES))
    confidence: float = Field(1.0, ge=0, le=1)


@tool("remember", "Save one durable fact about the user to memory. Only when the user explicitly asks to "
      "remember something. Duplicates are detected and not saved twice.", RememberArgs, scope=WRITE)
def remember(ctx: ToolContext, a: RememberArgs):
    return _strip(memory.create(a.content, category=a.category, confidence=a.confidence, source=ctx.actor,
                                actor=ctx.actor, chat_id=ctx.chat_id))


class MemUpdateArgs(BaseModel):
    memory_id: int
    content: str | None = None
    category: str | None = None


@tool("update_memory", "Correct or recategorise an existing memory.", MemUpdateArgs, scope=WRITE)
def update_memory(ctx: ToolContext, a: MemUpdateArgs):
    return memory.update(a.memory_id, content=a.content, category=a.category, actor=ctx.actor)


class ForgetArgs(BaseModel):
    memory_id: int


@tool("forget_memory", "Forget a memory (archived; the user can restore it from the Memory page).", ForgetArgs,
      scope=WRITE, destructive=True, summarize=lambda a: f"Forget memory #{a.memory_id}")
def forget_memory(ctx: ToolContext, a: ForgetArgs):
    return memory.set_status(a.memory_id, "archived", actor=ctx.actor, chat_id=ctx.chat_id)


@tool("delete_note", "Delete a note. It goes to the trash and can be restored from Settings › Trash.", PathArgs,
      scope=WRITE, destructive=True, summarize=lambda a: f"Delete note '{a.path}'")
def delete_note(ctx: ToolContext, a: PathArgs):
    return _strip(vault.delete(a.path, ctx.actor, chat_id=ctx.chat_id))


class SkillArgs(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    content: str = Field(..., min_length=1, max_length=500_000)
    service: str = Field("", description="Optional service/tool the skill is for")


@tool("create_skill", "Save a reusable procedure in the Skills folder with standard front matter.", SkillArgs,
      scope=WRITE)
def create_skill(ctx: ToolContext, a: SkillArgs):
    return _strip(vault.create(f"Skills/{_safe_title(a.title)}.md", _skill_content(a.title, a.content, a.service),
                               ctx.actor, chat_id=ctx.chat_id))


def _safe_title(title: str) -> str:
    import re
    name = re.sub(r'[\x00-\x1f<>:"|?*/\\]+', "-", title.strip()).strip(" .-")
    return (name or "Untitled")[:120]


def _skill_content(title: str, content: str, service: str = "") -> str:
    if content.lstrip().startswith("---"):
        return content
    props = {"title": title, "category": "skill", "status": "active"}
    if service:
        props["service"] = service
    body = content if content.lstrip().startswith("#") else f"# {title}\n\n{content}"
    return set_frontmatter(body, props)


def tool_catalog() -> list[dict]:
    return [{"name": t.name, "description": t.description, "scope": t.scope, "destructive": t.destructive}
            for t in REGISTRY.values()]


__all__ = ["REGISTRY", "ToolContext", "call", "resolve_pending", "list_pending", "query_notes", "audit_events",
           "tool_catalog", "READ", "WRITE", "FULL", "jdumps"]
