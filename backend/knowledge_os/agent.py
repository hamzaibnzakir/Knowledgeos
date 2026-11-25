"""Chat agent: retrieval-grounded answers plus real tool use.

Each turn:
1. retrieve a compact, budgeted context package (notes + memories + relevant
   attachment excerpts) — the vault is never sent wholesale;
2. call the agent model with the shared tool set;
3. execute tool calls through `tools.call` (validated, scoped, audited);
   destructive calls become pending actions the user approves in the UI;
4. store the answer with its sources, actions and token usage.
"""
from __future__ import annotations

import json
import logging
import re
import uuid

from . import db, retrieval, tools
from .ai import AIUnavailable, get_gateway
from .config import get_settings
from .util import KOSError, NotFound, estimate_tokens, jdumps, jloads, now, record_event

log = logging.getLogger("kos.agent")

SYSTEM_PROMPT = """You are the assistant inside the user's private Knowledge OS: a Markdown vault (notes, projects, \
resources, skills), a knowledge graph and a store of intentional memories.

Rules:
- Ground answers in the provided context and tool results. Cite sources inline as [1], [2] using the numbers in \
the context. If the context does not contain the answer, say so plainly; do not invent notes, paths or facts.
- You can act on the vault with tools. When the user asks you to create, edit, link, organise or remember \
something, actually call the tools — do not merely describe what could be done.
- Before creating a note, search for existing related notes and link to them with [[Note Title]].
- Prefer append_to_note / replace_in_note over update_note for edits; pass expected_hash when you use update_note.
- Only call `remember` when the user explicitly asks you to remember or save something about them.
- Deleting notes or forgetting memories requires the user's approval: the tool returns needs_confirmation. Tell the \
user it is waiting for their approval; never claim it was done.
- Never claim you saved, changed or verified anything unless a tool result in this turn confirms it.
- Be concise. Use Markdown. Refer to notes by their [[Title]] so the user can click them."""


def _title_from(message: str) -> str:
    text = re.sub(r"\s+", " ", message).strip()
    return (text[:60] + "…") if len(text) > 60 else (text or "New conversation")


def create_chat(title: str | None = None) -> dict:
    cid = str(uuid.uuid4())
    t = now()
    with db.connect() as conn:
        conn.execute("INSERT INTO chats(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                     (cid, title or "New conversation", t, t))
    return {"id": cid, "title": title or "New conversation", "created_at": t, "updated_at": t, "message_count": 0}


def list_chats() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT c.id,c.title,c.created_at,c.updated_at,(SELECT count(*) FROM messages m WHERE m.chat_id=c.id) "
            "AS message_count FROM chats c ORDER BY c.updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_messages(chat_id: str) -> list[dict]:
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone():
            raise NotFound("Chat not found")
        rows = conn.execute("SELECT id,role,content,attachment_name,meta,created_at FROM messages WHERE chat_id=? "
                            "ORDER BY id", (chat_id,)).fetchall()
    return [{**dict(r), "meta": jloads(r["meta"], {})} for r in rows]


def rename_chat(chat_id: str, title: str) -> dict:
    with db.connect() as conn:
        cur = conn.execute("UPDATE chats SET title=? WHERE id=?", (title.strip()[:120] or "Untitled", chat_id))
        if not cur.rowcount:
            raise NotFound("Chat not found")
    return {"ok": True}


def delete_chat(chat_id: str) -> dict:
    from . import attachments
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone():
            raise NotFound("Chat not found")
        atts = conn.execute("SELECT id FROM attachments WHERE chat_id=?", (chat_id,)).fetchall()
    for a in atts:
        attachments.delete(a["id"])
    with db.connect() as conn, db.transaction(conn):
        conn.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
        conn.execute("DELETE FROM pending_actions WHERE chat_id=? AND status='pending'", (chat_id,))
        conn.execute("DELETE FROM chats WHERE id=?", (chat_id,))
    return {"ok": True}


def _attachment_blocks(chat_id: str, query: str, budget_chars: int = 9000) -> list[dict]:
    """Pick the most relevant paragraphs of this chat's attachments (not the whole files)."""
    with db.connect() as conn:
        rows = conn.execute("SELECT id, filename, extracted_text FROM attachments WHERE chat_id=? ORDER BY created_at DESC",
                            (chat_id,)).fetchall()
    if not rows:
        return []
    q_terms = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 2}
    blocks = []
    per_file = max(1500, budget_chars // len(rows))
    for r in rows:
        text = r["extracted_text"] or ""
        if len(text) <= per_file:
            blocks.append({"label": f"Attachment: {r['filename']}", "text": text, "kind": "attachment"})
            continue
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        scored = sorted(enumerate(paras), key=lambda ip: (-len(q_terms & set(re.findall(r"\w+", ip[1].lower()))), ip[0]))
        chosen, size = [], 0
        for idx, p in scored:
            if size + len(p) > per_file:
                continue
            chosen.append((idx, p))
            size += len(p)
        chosen.sort()
        excerpt = "\n\n".join(p for _, p in chosen) or text[:per_file]
        blocks.append({"label": f"Attachment: {r['filename']} (relevant excerpts)", "text": excerpt, "kind": "attachment"})
    return blocks


def _history(chat_id: str, limit: int) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("SELECT role, content FROM messages WHERE chat_id=? AND role IN ('user','assistant') "
                            "ORDER BY id DESC LIMIT ?", (chat_id, limit)).fetchall()
    out = []
    for r in reversed(rows):
        content = r["content"]
        if len(content) > 2000:
            content = content[:2000] + "…"
        out.append({"role": r["role"], "content": content})
    return out


def _tool_result_text(result) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text if len(text) <= 6000 else text[:6000] + "…(truncated)"


def send_message(chat_id: str, message: str, *, model: str | None = None, use_tools: bool = True) -> dict:
    message = (message or "").strip()
    if not message:
        raise KOSError("Message is empty")
    s = get_settings()
    with db.connect() as conn:
        chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if not chat:
            raise NotFound("Chat not found")
        history = _history(chat_id, s.history_messages)
        conn.execute("INSERT INTO messages(chat_id,role,content,created_at,meta) VALUES(?,?,?,?,?)",
                     (chat_id, "user", message, now(), "{}"))
        if chat["title"] in (None, "", "New conversation"):
            conn.execute("UPDATE chats SET title=? WHERE id=?", (_title_from(message), chat_id))

    # Retrieval query: include the previous user turn so follow-ups ("and the second one?") still retrieve well.
    prev_user = next((h["content"] for h in reversed(history) if h["role"] == "user"), "")
    rq = message if len(message) > 80 or not prev_user else f"{prev_user[:300]}\n{message}"
    pkg = retrieval.build_context(rq, extra_blocks=_attachment_blocks(chat_id, rq))

    gw = get_gateway()
    actions: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    if not gw.configured:
        answer = _offline_answer(pkg)
        model_used = None
    else:
        model_used = model or s.resolved_agent_model
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history,
                    {"role": "user", "content": f"{message}\n\n<context>\n{pkg['text'] or '(no relevant notes found)'}\n</context>"}]
        tool_schemas = [t.openai_schema() for t in tools.REGISTRY.values()] if use_tools else None
        ctx = tools.ToolContext(actor="agent", chat_id=chat_id, confirm_destructive=True,
                                scopes={tools.READ, tools.WRITE})
        answer = ""
        try:
            for step in range(s.agent_max_steps):
                res = gw.chat(messages, model=model_used, tools=tool_schemas, operation="agent.chat", actor="agent")
                usage["input_tokens"] += res.input_tokens
                usage["output_tokens"] += res.output_tokens
                usage["calls"] += 1
                if not res.tool_calls:
                    answer = res.content
                    break
                messages.append({"role": "assistant", "content": res.content or None, "tool_calls": res.tool_calls})
                for tc in res.tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    try:
                        result = tools.call(name, args, ctx)
                        status = result.get("status", "completed") if isinstance(result, dict) else "completed"
                        actions.append({"tool": name, "args": _short_args(args), "status": status,
                                        **({"pending_id": result["pending_id"], "summary": result["summary"]}
                                           if status == "needs_confirmation" else {}),
                                        **({"path": result.get("path")} if isinstance(result, dict) and result.get("path") else {})})
                    except KOSError as e:
                        result = {"error": e.message}
                        actions.append({"tool": name, "args": _short_args(args), "status": "failed", "error": e.message})
                    except Exception as e:  # tool bug: report to the model, keep the conversation alive
                        log.exception("tool %s failed", name)
                        result = {"error": f"{type(e).__name__}"}
                        actions.append({"tool": name, "args": _short_args(args), "status": "failed"})
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": _tool_result_text(result)})
            else:
                answer = answer or "I stopped after the maximum number of tool steps. Here is what I did so far."
        except AIUnavailable:
            answer = _offline_answer(pkg)
        except KOSError as e:
            answer = f"⚠️ The AI request failed: {e.message}"
            with db.connect() as conn:
                record_event(conn, "chat.answer", "failed", {"error": e.message}, actor="agent", chat_id=chat_id)

    meta = {"sources": pkg["sources"], "context_tokens": pkg["tokens"], "actions": actions, "usage": usage,
            "model": model_used, "memories_used": [m["id"] for m in pkg.get("memories", [])]}
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO messages(chat_id,role,content,created_at,meta) VALUES(?,?,?,?,?)",
                           (chat_id, "assistant", answer, now(), jdumps(meta)))
        conn.execute("UPDATE chats SET updated_at=? WHERE id=?", (now(), chat_id))
        record_event(conn, "chat.answer", "completed",
                     {"actions": len(actions), "sources": len(pkg["sources"]), "context_tokens": pkg["tokens"]},
                     actor="agent", chat_id=chat_id)
        mid = cur.lastrowid
    return {"id": mid, "role": "assistant", "content": answer, "meta": meta, "chat_id": chat_id}


def _short_args(args: dict) -> dict:
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and len(v) > 160:
            v = v[:160] + "…"
        out[k] = v
    return out


def _offline_answer(pkg: dict) -> str:
    if not pkg["sources"]:
        return ("AI chat is not configured (set KOS_AI_API_KEY), and I found no notes matching your message. "
                "Search, notes, graph and memory all work without AI.")
    lines = ["AI chat is not configured (set KOS_AI_API_KEY), so here are the most relevant passages I found:"]
    for s in pkg["sources"][:6]:
        if s.get("path"):
            lines.append(f"- [{s['n']}] [[{s['title']}]]" + (f" › {s['heading']}" if s.get("heading") else ""))
        else:
            lines.append(f"- [{s['n']}] {s['label']}")
    return "\n".join(lines)


def ask(question: str, *, model: str | None = None) -> dict:
    """One-shot grounded answer without tools (used by the quick 'Ask' box)."""
    s = get_settings()
    pkg = retrieval.build_context(question)
    gw = get_gateway()
    if not gw.configured:
        return {"answer": _offline_answer(pkg), "sources": pkg["sources"], "context_tokens": pkg["tokens"]}
    res = gw.chat([{"role": "system", "content": SYSTEM_PROMPT},
                   {"role": "user", "content": f"{question}\n\n<context>\n{pkg['text'] or '(no relevant notes found)'}\n</context>"}],
                  model=model or s.chat_model, operation="ask", actor="web")
    return {"answer": res.content, "sources": pkg["sources"], "context_tokens": pkg["tokens"],
            "usage": {"input_tokens": res.input_tokens, "output_tokens": res.output_tokens}}


__all__ = ["create_chat", "list_chats", "get_messages", "send_message", "delete_chat", "rename_chat", "ask",
           "estimate_tokens"]
