"""Memory as Markdown.

Memory lives in the vault under `Memory/`, so it is readable, editable,
linkable and versioned like any note:

    Memory/Profile.md          who the owner is    (given to every agent)
    Memory/Rules.md            how agents must work (given to every agent)
    Memory/Projects/<X>.md     facts per subject, fetched when relevant
    Memory/Clients/<X>.md, Memory/People/<X>.md, Memory/Topics/<X>.md

A fact is one list item with a stable id and inline fields (Dataview style):

    - Prefers OpenAI for the main agent ^f3k9q2 [by:: hermes] [on:: 2026-11-25]

Fields: by (who saved it), on (date), src (where it came from), from:: external
(came from a web page/document), review:: new (not yet reviewed by the owner),
flag:: suspicious (looks like an instruction), and for replaced facts until,
replaced-by and reason. Replaced facts move to a "## History" section struck
through, so nothing is silently lost.

Trust model: the owner writes directly. Agents write subject facts directly but
marked `review:: new`; changes to Profile and Rules from agents become proposals
that only take effect when the owner approves them.
"""
from __future__ import annotations

import posixpath
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np

from . import db, vault
from .config import get_settings
from .util import Conflict, KOSError, NotFound, jdumps, jloads, now, record_event, sha256

PROFILE = "Memory/Profile.md"
RULES = "Memory/Rules.md"
CORE = (PROFILE, RULES)
KIND_FOLDERS = {"project": "Projects", "client": "Clients", "person": "People", "topic": "Topics"}
FOLDER_KINDS = {v: k for k, v in KIND_FOLDERS.items()}
MAX_FACT_CHARS = 600
FIELD_ORDER = ["by", "on", "src", "from", "review", "flag", "until", "replaced-by", "reason"]

FACT_RE = re.compile(r"^(?P<indent>[ \t]*)[-*][ \t]+(?P<body>.*?)[ \t]+\^(?P<id>[A-Za-z0-9][\w-]{1,31})"
                     r"(?P<fields>(?:[ \t]*\[[\w-]+::[^\]\n]*\])*)[ \t]*$")
FIELD_RE = re.compile(r"\[([\w-]+)::[ \t]*([^\]\n]*?)[ \t]*\]")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
SUSPICIOUS_RE = re.compile(
    r"(ignore (all |any |the )?(previous|prior|above|earlier)|disregard (all|any|previous|prior|the)|system prompt|"
    r"jailbreak|you (must|should) (always|never)|from now on,? (you|always|never)|override (your|the|all)|"
    r"do not tell the user|send (it|them|this|the \w+) to|<\s*/?\s*script|begin (system|instructions)|"
    r"exfiltrat|api[_ ]?key|password is)", re.I)
_WORD = re.compile(r"\w+", re.U)

CORE_TEMPLATES = {
    PROFILE: ("Profile", "Who you are. Every agent reads this at the start of a session, so keep it short "
                         "and current. Agents can only propose changes here; you approve them in Review."),
    RULES: ("Rules", "How agents should work for you. Every agent reads this at the start of a session. "
                     "Agents can only propose changes here; you approve them in Review."),
}


# --------------------------------------------------------------------------- parsing

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!")


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2}


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").replace("^", "")).strip()
    text = FIELD_RE.sub("", text).strip()
    if text.startswith("~~") and text.endswith("~~"):
        text = text[2:-2].strip()
    return text


def parse_facts(content: str) -> list[dict]:
    out = []
    in_fence = False
    for i, line in enumerate(content.splitlines()):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = FACT_RE.match(line)
        if not m:
            continue
        body = m.group("body").strip()
        struck = body.startswith("~~") and body.endswith("~~") and len(body) > 4
        text = body[2:-2].strip() if struck else body
        fields = {k.lower(): v.strip() for k, v in FIELD_RE.findall(m.group("fields"))}
        out.append({"id": m.group("id"), "text": text, "fields": fields, "line": i,
                    "current": not (struck or fields.get("until"))})
    return out


def format_fact(fid: str, text: str, fields: dict, *, struck: bool = False) -> str:
    keys = [k for k in FIELD_ORDER if k in fields] + [k for k in fields if k not in FIELD_ORDER]
    parts = [f"[{k}:: {fields[k]}]" for k in keys if fields.get(k) not in (None, "")]
    body = f"~~{text}~~" if struck else text
    return f"- {body} ^{fid}" + (" " + " ".join(parts) if parts else "")


def _subject_of(path: str) -> tuple[str, str]:
    """(subject title, kind) for a memory file path."""
    stem = posixpath.splitext(posixpath.basename(path))[0]
    if path == PROFILE:
        return "Profile", "profile"
    if path == RULES:
        return "Rules", "rules"
    parts = path.split("/")
    kind = FOLDER_KINDS.get(parts[1], "topic") if len(parts) > 2 else "topic"
    return stem, kind


def is_memory_path(path: str) -> bool:
    return path.startswith("Memory/") and path.endswith(".md")


def reindex_facts(conn, path: str, content: str) -> None:
    """Called by the indexer inside its transaction whenever a Memory/ file changes."""
    conn.execute("DELETE FROM facts WHERE path=?", (path,))
    conn.execute("DELETE FROM facts_fts WHERE path=?", (path,))
    if not is_memory_path(path):
        return
    subject, kind = _subject_of(path)
    seen: set[str] = set()
    for f in parse_facts(content):
        if f["id"] in seen:
            continue
        seen.add(f["id"])
        fl = f["fields"]
        conn.execute(
            "INSERT OR REPLACE INTO facts(id,path,subject,kind,text,by_agent,on_date,src,external,review,flag,status,"
            "until,replaced_by,reason,line_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f["id"], path, subject, kind, f["text"], fl.get("by", ""), fl.get("on", ""), fl.get("src", ""),
             int(fl.get("from", "") == "external"), "new" if fl.get("review") == "new" else "ok", fl.get("flag", ""),
             "current" if f["current"] else "superseded", fl.get("until", ""), fl.get("replaced-by", ""),
             fl.get("reason", ""), f["line"]))
        if f["current"]:
            conn.execute("INSERT INTO facts_fts(text,subject,id,path) VALUES(?,?,?,?)",
                         (f["text"], subject, f["id"], path))


# --------------------------------------------------------------------------- file editing

def _new_id() -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return "f" + "".join(secrets.choice(alphabet) for _ in range(6))


def _section_bounds(lines: list[str], name: str) -> tuple[int, int] | None:
    """(start, end) line indexes of a level-2 section's body, or None."""
    start = None
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if not m:
            continue
        if start is None and len(m.group(1)) == 2 and m.group(2).strip().lower() == name.lower():
            start = i + 1
        elif start is not None and len(m.group(1)) <= 2:
            return start, i
    return (start, len(lines)) if start is not None else None


def _insert_line(content: str, section: str, line: str) -> str:
    lines = content.rstrip("\n").split("\n")
    b = _section_bounds(lines, section)
    if b is None:
        # Facts go before History if History exists; History goes at the end.
        hist = _section_bounds(lines, "History") if section != "History" else None
        block = [f"## {section}", "", line]
        if hist:
            at = hist[0] - 1
            lines[at:at] = block + [""]
        else:
            lines += ["", *block]
        return "\n".join(lines) + "\n"
    start, end = b
    at = end
    while at > start and not lines[at - 1].strip():
        at -= 1
    if at == start:
        lines[at:at] = ["", line] if lines[at - 1].strip() else [line]
    else:
        lines.insert(at, line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.rstrip("\n") + "\n"


def _template(path: str, subject: str, kind: str) -> str:
    if path in CORE_TEMPLATES:
        title, intro = CORE_TEMPLATES[path]
        return f"---\ntype: memory\nkind: {kind}\n---\n\n# {title}\n\n{intro}\n\n## Facts\n"
    return f"---\ntype: memory\nkind: {kind}\n---\n\n# {subject}\n\n## Facts\n"


def _safe_title(title: str) -> str:
    name = re.sub(r'[\x00-\x1f<>:"|?*/\\#^\[\]]+', " ", title.strip())
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return (name or "Untitled")[:100]


def resolve_subject(subject: str, kind: str = "topic") -> str:
    from . import indexer
    from .markdown import link_key

    s = (subject or "").strip()
    if not s:
        raise KOSError("A subject is required, e.g. 'EcomLab', 'Profile' or 'Rules'")
    low = s.lower().removesuffix(".md")
    if low in ("profile", "memory/profile"):
        return PROFILE
    if low in ("rules", "memory/rules", "preferences"):
        return RULES
    if low.startswith("memory/"):
        return vault.normalize_rel(s)
    head, _, rest = s.partition("/")
    if rest and head.strip().title() in FOLDER_KINDS:          # "People/Ada", "Projects/EcomLab"
        return f"Memory/{head.strip().title()}/{_safe_title(rest)}.md"
    with db.connect() as conn:
        for p in indexer.resolve_key(link_key(s), conn):
            if is_memory_path(p):
                return p
    folder = KIND_FOLDERS.get((kind or "topic").lower(), "Topics")
    return f"Memory/{folder}/{_safe_title(s)}.md"


def _ensure_file(path: str, actor: str) -> None:
    if not vault.exists(path):
        subject, kind = _subject_of(path)
        vault.create(path, _template(path, subject, kind), actor)


def _get_fact(fid: str) -> dict:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM facts WHERE id=?", (fid.lstrip("^"),)).fetchone()
    if not r:
        raise NotFound(f"Fact {fid} not found")
    return dict(r)


def _rewrite_fact_line(path: str, fid: str, actor: str, fn, reason: str) -> None:
    """Apply fn(lines, index, parsed_fact) -> new content to the line holding ^fid."""
    with vault._write_lock:
        cur = vault.read(path)
        lines = cur["content"].split("\n")
        for f in parse_facts(cur["content"]):
            if f["id"] == fid:
                new = fn(lines, f["line"], f)
                vault.write(path, new, actor, expected_hash=cur["hash"], reason=reason)
                return
    raise NotFound(f"Fact {fid} not found in {path}")


def add_fact(path: str, text: str, *, by: str, actor: str, src: str = "", external: bool = False,
             review_new: bool = False, flag: str = "", fid: str | None = None) -> str:
    text = _clean_text(text)
    if not text:
        raise KOSError("A fact cannot be empty")
    if len(text) > MAX_FACT_CHARS:
        raise KOSError(f"A fact should be one short statement (max {MAX_FACT_CHARS} characters). "
                       "Put longer material in a note and link it.")
    fid = fid or _new_id()
    fields = {"by": by, "on": date.today().isoformat(), "src": _clean_text(src)[:200],
              "from": "external" if external else "", "review": "new" if review_new else "", "flag": flag}
    with vault._write_lock:
        _ensure_file(path, actor)
        cur = vault.read(path)
        new = _insert_line(cur["content"], "Facts", format_fact(fid, text, fields))
        vault.write(path, new, actor, expected_hash=cur["hash"], reason="remember")
    return fid


def supersede(fid: str, *, actor: str, by: str, replaced_by: str = "", reason: str = "") -> None:
    f = _get_fact(fid)
    if f["status"] != "current":
        raise Conflict(f"Fact {fid} was already replaced or forgotten")

    def fn(lines, i, parsed):
        fields = dict(parsed["fields"])
        fields.pop("review", None)
        fields.update({"until": date.today().isoformat(), "replaced-by": replaced_by,
                       "reason": _clean_text(reason)[:160] or ("replaced" if replaced_by else f"forgotten by {by}")})
        struck = format_fact(fid, parsed["text"], fields, struck=True)
        del lines[i]
        return _insert_line("\n".join(lines), "History", struck)

    _rewrite_fact_line(f["path"], fid, actor, fn, "revise")


def approve_fact(fid: str, actor: str = "web") -> dict:
    f = _get_fact(fid)

    def fn(lines, i, parsed):
        fields = {k: v for k, v in parsed["fields"].items() if k not in ("review", "flag")}
        lines[i] = format_fact(fid, parsed["text"], fields)
        return "\n".join(lines)

    _rewrite_fact_line(f["path"], fid, actor, fn, "review")
    with db.connect() as conn:
        record_event(conn, "memory.approve", details={"fact": fid, "path": f["path"]}, actor=actor)
    return _get_fact(fid)


def edit_fact_text(fid: str, text: str, actor: str = "web") -> dict:
    """Owner-only typo fix in place (no history entry). Agents use revise()."""
    f = _get_fact(fid)
    text = _clean_text(text)
    if not text or len(text) > MAX_FACT_CHARS:
        raise KOSError("Fact text is empty or too long")

    def fn(lines, i, parsed):
        lines[i] = format_fact(fid, text, parsed["fields"], struck=not parsed["current"])
        return "\n".join(lines)

    _rewrite_fact_line(f["path"], fid, actor, fn, "edit")
    return _get_fact(fid)


def purge_fact(fid: str, actor: str = "web") -> dict:
    """Owner-only: remove the line entirely (it stays recoverable in the note's version history)."""
    f = _get_fact(fid)

    def fn(lines, i, parsed):
        del lines[i]
        return "\n".join(lines)

    _rewrite_fact_line(f["path"], fid, actor, fn, "purge")
    with db.connect() as conn:
        record_event(conn, "memory.purge", details={"fact": fid, "path": f["path"]}, actor=actor)
    return {"ok": True}


# --------------------------------------------------------------------------- search

def _fact_row(r) -> dict:
    d = dict(r)
    d["external"] = bool(d.get("external"))
    return d


def list_facts(*, path: str | None = None, review: str | None = None, status: str = "current",
               by: str | None = None, q: str | None = None, kind: str | None = None, limit: int = 500) -> list[dict]:
    sql, args = "SELECT * FROM facts WHERE 1=1", []
    if status != "all":
        sql += " AND status=?"
        args.append(status)
    for col, val in (("path", path), ("review", review), ("by_agent", by), ("kind", kind)):
        if val:
            sql += f" AND {col}=?"
            args.append(val)
    if q:
        sql += " AND text LIKE ? ESCAPE '\\'"
        args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    sql += " ORDER BY on_date DESC, path, line_no LIMIT ?"
    args.append(max(1, min(limit, 5000)))
    with db.connect() as conn:
        return [_fact_row(r) for r in conn.execute(sql, args)]


def _fact_vectors(rows: list[dict]) -> np.ndarray | None:
    from . import embeddings
    emb = embeddings.get_embedder()
    if not emb.available or not rows:
        return None
    if hasattr(emb, "is_ready") and not emb.is_ready():
        emb.warm_async()
        return None
    keys = ["fact:" + sha256(r["text"]) for r in rows]
    found: dict[str, np.ndarray] = {}
    with db.connect() as conn:
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            for r in conn.execute(f"SELECT chunk_hash, vector FROM embeddings WHERE model=? AND chunk_hash IN "
                                  f"({','.join('?' * len(part))})", (emb.name, *part)):
                found[r["chunk_hash"]] = np.frombuffer(r["vector"], dtype=np.float32)
    missing = [(k, r["text"]) for k, r in zip(keys, rows) if k not in found]
    if missing:
        vecs = embeddings.embed_texts([t for _, t in missing])
        if vecs is None:
            return None
        with db.connect() as conn, db.transaction(conn):
            for (k, _), v in zip(missing, vecs):
                conn.execute("INSERT OR REPLACE INTO embeddings(chunk_hash,model,dim,vector) VALUES(?,?,?,?)",
                             (k, emb.name, int(v.shape[0]), v.astype(np.float32).tobytes()))
                found[k] = v
    if len({v.shape[0] for v in found.values()}) != 1:
        return None
    return np.vstack([found[k] for k in keys])


def search_facts(query: str, k: int = 10, *, path: str | None = None, include_flagged: bool = True) -> list[dict]:
    """Hybrid search over current facts: token overlap + meaning (local embeddings)."""
    from . import embeddings
    rows = list_facts(path=path, limit=5000)
    if not include_flagged:
        rows = [r for r in rows if not r["flag"]]
    if not rows or not query.strip():
        return rows[:k]
    qt = _tokens(query)
    lex = np.zeros(len(rows), dtype=np.float32)
    for i, r in enumerate(rows):
        ft = _tokens(r["text"] + " " + r["subject"])
        if qt and ft:
            lex[i] = len(qt & ft) / ((len(qt) ** 0.5) * (len(ft) ** 0.5))
    scores = lex
    vecs = _fact_vectors(rows)
    if vecs is not None:
        qv = embeddings.embed_texts([query])
        if qv is not None and qv.shape[1] == vecs.shape[1]:
            floor = getattr(embeddings.get_embedder(), "sim_floor", 0.4) - 0.1
            sem = np.clip((vecs @ qv[0] - floor) / (1 - floor), 0, 1)
            scores = 0.4 * lex + 0.6 * sem
    order = np.argsort(-scores)
    out = []
    for i in order[:k]:
        if scores[i] <= 0.05:
            break
        item = dict(rows[i])
        item["score"] = round(float(scores[i]), 3)
        out.append(item)
    return out


def find_similar(text: str, path: str | None = None, limit: int = 5) -> list[dict]:
    """Facts that may say the same thing (exact match first)."""
    norm = _norm(text)
    toks = _tokens(text)
    rows = list_facts(limit=5000)
    exact = [r for r in rows if _norm(r["text"]) == norm]
    if exact:
        return [{**exact[0], "similarity": 1.0, "exact": True}]
    cands = []
    for r in rows:
        other = _tokens(r["text"])
        if not toks or not other:
            continue
        j = len(toks & other) / len(toks | other)
        boost = 0.1 if path and r["path"] == path else 0.0
        if j + boost >= 0.45:
            cands.append({**r, "similarity": round(j, 3)})
    vecs = _fact_vectors(rows) if rows else None
    if vecs is not None:
        from . import embeddings
        qv = embeddings.embed_texts([text])
        if qv is not None and qv.shape[1] == vecs.shape[1]:
            sims = vecs @ qv[0]
            have = {c["id"] for c in cands}
            for i, s in enumerate(sims):
                if s >= 0.86 and rows[i]["id"] not in have:
                    cands.append({**rows[i], "similarity": round(float(s), 3)})
    cands.sort(key=lambda c: -c["similarity"])
    return cands[:limit]


# --------------------------------------------------------------------------- high-level operations

def remember(subject: str, text: str, *, agent: str, is_owner: bool, actor: str, trusted: bool = False, kind: str = "topic",
             src: str = "", external: bool = False, replaces: str | None = None,
             on_similar: str = "ask") -> dict:
    """Save a fact. Returns a status the caller can act on:

    saved / proposed (Profile & Rules from agents) / exists / needs_decision / skipped
    """
    text = _clean_text(text)
    if not text:
        raise KOSError("A fact cannot be empty")
    if len(text) > MAX_FACT_CHARS:
        raise KOSError(f"Keep a fact under {MAX_FACT_CHARS} characters; put longer material in a note.")
    path = resolve_subject(subject, kind)
    if replaces:
        old = _get_fact(replaces)
        if old["status"] != "current":
            raise Conflict(f"Fact {replaces} was already replaced or forgotten")
        path = old["path"]
    elif on_similar != "add":
        similar = find_similar(text, path)
        if similar and similar[0].get("exact"):
            return {"status": "exists", "fact": _public(similar[0]),
                    "message": "This fact is already saved. Nothing was changed."}
        if similar and on_similar == "ask":
            return {"status": "needs_decision", "similar": [_public(s) for s in similar],
                    "message": "Similar facts already exist. Call remember again with replaces=<id> to update one, "
                               "on_similar='add' to keep both, or on_similar='skip' to do nothing."}
        if similar and on_similar == "skip":
            return {"status": "skipped", "similar": [_public(s) for s in similar]}
    flag = "suspicious" if SUSPICIOUS_RE.search(text) else ""
    if path in CORE and not is_owner:
        p = create_proposal(path, "replace" if replaces else "add", text=text, fact_id=replaces or "", agent=agent,
                            src=src, external=external, flag=flag, actor=actor)
        return {"status": "proposed", "proposal_id": p["id"], "path": path,
                "message": f"{_subject_of(path)[0]} only changes with the owner's approval. Your proposal is in "
                           "their review queue; do not treat it as saved yet."}
    # Trusted agents skip review, except for text that reads like an instruction.
    needs_review = not is_owner and (not trusted or bool(flag))
    fid = add_fact(path, text, by=agent, actor=actor, src=src, external=external, review_new=needs_review, flag=flag)
    if replaces:
        supersede(replaces, actor=actor, by=agent, replaced_by=fid)
    with db.connect() as conn:
        record_event(conn, "memory.remember", details={"fact": fid, "path": path, "replaces": replaces or None,
                                                       "flag": flag or None}, actor=actor)
    out = {"status": "saved", "fact_id": fid, "path": path, "review": "new" if needs_review else "ok"}
    if flag:
        out["warning"] = "This looks like an instruction rather than a fact, so it was flagged and is hidden from " \
                         "briefs until the owner reviews it."
    return out


def forget(fid: str, *, agent: str, is_owner: bool, actor: str, reason: str = "") -> dict:
    f = _get_fact(fid)
    if f["status"] != "current":
        return {"status": "already_gone", "fact": _public(f)}
    if f["path"] in CORE and not is_owner:
        p = create_proposal(f["path"], "retire", text=f["text"], fact_id=fid, agent=agent, src="", external=False,
                            flag="", actor=actor, reason=reason)
        return {"status": "proposed", "proposal_id": p["id"],
                "message": "Removing a Profile or Rules fact needs the owner's approval; it is in their review queue."}
    supersede(fid, actor=actor, by=agent, reason=reason or f"forgotten by {agent}")
    with db.connect() as conn:
        record_event(conn, "memory.forget", details={"fact": fid, "path": f["path"]}, actor=actor)
    return {"status": "forgotten", "fact_id": fid,
            "message": "Moved to the History section of its file; the owner can still see it."}


def _public(f: dict) -> dict:
    keys = ("id", "text", "subject", "kind", "path", "by_agent", "on_date", "src", "external", "review", "flag",
            "status", "similarity", "score")
    return {k: f[k] for k in keys if k in f}


# --------------------------------------------------------------------------- proposals (Profile / Rules)

def create_proposal(path: str, op: str, *, text: str, fact_id: str, agent: str, src: str, external: bool,
                    flag: str, actor: str, reason: str = "") -> dict:
    pid = "p" + secrets.token_hex(5)
    with db.connect() as conn:
        conn.execute("INSERT INTO proposals(id,path,op,fact_id,text,agent,src,external,flag,reason,status,created_at)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (pid, path, op, fact_id, text, agent, src, int(external), flag, reason, "pending", now()))
        record_event(conn, "memory.propose", "pending", {"proposal": pid, "path": path, "op": op}, actor=actor)
    return {"id": pid}


def list_proposals(status: str = "pending") -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM proposals WHERE status=? ORDER BY created_at", (status,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["external"] = bool(d["external"])
            if d["fact_id"]:
                old = conn.execute("SELECT text, status FROM facts WHERE id=?", (d["fact_id"],)).fetchone()
                d["current_text"] = old["text"] if old else None
            out.append(d)
    return out


def resolve_proposal(pid: str, approve: bool, *, text: str | None = None, actor: str = "web") -> dict:
    with db.connect() as conn:
        p = conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    if not p:
        raise NotFound("Proposal not found")
    if p["status"] != "pending":
        raise Conflict(f"This proposal was already {p['status']}")
    result: dict = {"status": "rejected"}
    if approve:
        body = _clean_text(text or p["text"])
        if p["op"] == "retire":
            supersede(p["fact_id"], actor=actor, by=p["agent"], reason=p["reason"] or f"retired, proposed by {p['agent']}")
            result = {"status": "approved"}
        else:
            fid = add_fact(p["path"], body, by=p["agent"], actor=actor, src=p["src"], external=bool(p["external"]))
            if p["op"] == "replace" and p["fact_id"]:
                try:
                    supersede(p["fact_id"], actor=actor, by=p["agent"], replaced_by=fid)
                except (NotFound, Conflict):
                    pass
            result = {"status": "approved", "fact_id": fid}
    with db.connect() as conn:
        conn.execute("UPDATE proposals SET status=?, resolved_at=? WHERE id=?",
                     ("approved" if approve else "rejected", now(), pid))
        record_event(conn, "memory.proposal", "approved" if approve else "rejected", {"proposal": pid}, actor=actor)
    return result


def review_queue() -> dict:
    facts = list_facts(review="new")
    return {"facts": facts, "proposals": list_proposals(), "count": len(facts) + len(list_proposals())}


def trusted_recent(days: int = 7, limit: int = 60) -> list[dict]:
    """Facts agents saved without review in the last few days (trusted agents), so the owner can still glance."""
    since = (date.today() - timedelta(days=days)).isoformat()
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM facts WHERE status='current' AND review='ok' AND by_agent NOT IN ('', 'you') "
                            "AND on_date >= ? AND path NOT IN (?, ?) ORDER BY on_date DESC, path, line_no LIMIT ?",
                            (since, PROFILE, RULES, limit)).fetchall()
    return [_fact_row(r) for r in rows]


def agent_week(name: str, days: int = 7) -> dict:
    """What one agent did to memory recently: saved, kept, waiting, rejected."""
    since = (date.today() - timedelta(days=days)).isoformat()
    with db.connect() as conn:
        def n(sql: str, *a) -> int:
            return conn.execute(sql, (name, *a)).fetchone()[0]
        return {
            "saved": n("SELECT count(*) FROM facts WHERE by_agent=? AND on_date>=?", since),
            "waiting": n("SELECT count(*) FROM facts WHERE by_agent=? AND status='current' AND review='new'"),
            "kept": n("SELECT count(*) FROM facts WHERE by_agent=? AND status='current' AND review='ok' AND on_date>=?", since),
            "rejected": n("SELECT count(*) FROM facts WHERE by_agent=? AND status<>'current' AND reason='rejected in review' "
                          "AND until>=?", since),
            "proposals": n("SELECT count(*) FROM proposals WHERE agent=? AND status='pending'"),
        }


def reject_fact(fid: str, actor: str = "web") -> dict:
    supersede(fid, actor=actor, by="you", reason="rejected in review")
    with db.connect() as conn:
        record_event(conn, "memory.reject", details={"fact": fid}, actor=actor)
    return {"ok": True}


# --------------------------------------------------------------------------- overview & health

def ensure_core() -> None:
    """Profile and Rules always exist, so the owner and agents always know where they go."""
    for core in CORE:
        if not vault.exists(core):
            subject, kind = _subject_of(core)
            try:
                vault.create(core, _template(core, subject, kind), "system")
            except Conflict:
                pass


def subjects() -> list[dict]:
    ensure_core()
    with db.connect() as conn:
        notes = conn.execute("SELECT path, title, updated_at, aliases FROM notes WHERE path LIKE 'Memory/%' "
                             "ORDER BY title").fetchall()
        counts = {r["path"]: (r["cur"], r["new"]) for r in conn.execute(
            "SELECT path, sum(status='current') cur, sum(status='current' AND review='new') new FROM facts GROUP BY path")}
    out = []
    for n in notes:
        subject, kind = _subject_of(n["path"])
        cur, new = counts.get(n["path"], (0, 0))
        out.append({"path": n["path"], "title": n["title"], "subject": subject, "kind": kind,
                    "facts": cur or 0, "unreviewed": new or 0, "updated_at": n["updated_at"],
                    "aliases": jloads(n["aliases"], [])})
    order = {"profile": 0, "rules": 1, "project": 2, "client": 3, "person": 4, "topic": 5}
    return sorted(out, key=lambda s: (order.get(s["kind"], 9), s["subject"].lower()))


def core_size(path: str) -> int:
    return sum(len(f["text"]) + 3 for f in list_facts(path=path))


def health() -> dict:
    s = get_settings()
    facts = list_facts(limit=5000)
    dupes = []
    for i, a in enumerate(facts):
        ta = _tokens(a["text"])
        for b in facts[i + 1:]:
            if _norm(a["text"]) == _norm(b["text"]):
                dupes.append({"a": _public(a), "b": _public(b), "similarity": 1.0})
                continue
            tb = _tokens(b["text"])
            if ta and tb:
                j = len(ta & tb) / len(ta | tb)
                if j >= 0.75:
                    dupes.append({"a": _public(a), "b": _public(b), "similarity": round(j, 2)})
    cutoff = (date.today() - timedelta(days=s.memory_stale_days)).isoformat()
    stale = [_public(f) for f in facts if f["on_date"] and f["on_date"] < cutoff and f["kind"] not in ("profile", "rules")]
    return {
        "facts": len(facts),
        "unreviewed": sum(1 for f in facts if f["review"] == "new"),
        "flagged": [_public(f) for f in facts if f["flag"]],
        "duplicates": dupes[:50],
        "stale": stale[:100],
        "stale_days": s.memory_stale_days,
        "core": {p: {"chars": core_size(p), "limit": s.core_memory_max_chars} for p in CORE},
        "proposals": len(list_proposals()),
    }


# --------------------------------------------------------------------------- brief (session start)

def _fact_line_for_brief(f: dict) -> str:
    notes = []
    if f["review"] == "new":
        notes.append(f"unreviewed, saved by {f['by_agent'] or 'an agent'}")
    if f["external"]:
        notes.append("from an external source")
    return f"- {f['text']}" + (f" ({'; '.join(notes)})" if notes else "") + f" [{f['id']}]"


def _cap(lines: list[str], max_chars: int) -> tuple[list[str], bool]:
    out, used = [], 0
    for line in lines:
        if used + len(line) + 1 > max_chars:
            return out, True
        out.append(line)
        used += len(line) + 1
    return out, False


def brief(agent: str, topic: str | None = None, max_tokens: int | None = None) -> dict:
    """What an agent should know at the start of a session, within a small budget."""
    from . import retrieval, tools
    s = get_settings()
    budget_chars = (max_tokens or s.brief_token_budget) * 4
    ensure_core()
    core_cap = s.core_memory_max_chars
    parts: list[str] = [f"# Brief for {agent} — {date.today().isoformat()}"]
    truncated = False

    for path, title in ((PROFILE, "Profile"), (RULES, "Rules")):
        facts = [f for f in list_facts(path=path) if not f["flag"]]
        lines, cut = _cap([_fact_line_for_brief(f) for f in facts], core_cap)
        truncated |= cut
        parts.append(f"## {title}\n" + ("\n".join(lines) if lines else "_Nothing saved yet._"))

    agent_note = f"Agents/{_safe_title(agent)}/AGENT.md"
    if vault.exists(agent_note):
        body = vault.read(agent_note)["content"]
        from .markdown import split_frontmatter
        body = split_frontmatter(body)[1].strip()
        if body:
            parts.append(f"## Your agent note ({agent_note})\n" + body[:1500] + ("…" if len(body) > 1500 else ""))

    if topic and topic.strip():
        rel_lines: list[str] = []
        path = None
        try:
            guess = resolve_subject(topic)
            path = guess if vault.exists(guess) else None
        except KOSError:
            path = None
        if path and path not in CORE:
            rel_lines.append(f"### {_subject_of(path)[0]} ({path})")
            rel_lines += [_fact_line_for_brief(f) for f in list_facts(path=path) if not f["flag"]]
        hits = [f for f in search_facts(topic, k=12, include_flagged=False)
                if f["path"] != path and f["path"] not in CORE]
        if hits:
            rel_lines.append("### Related facts")
            rel_lines += [_fact_line_for_brief(f) + f" — {f['subject']}" for f in hits]
        notes = [n for n in retrieval.search(topic, limit=8) if not n["path"].startswith("Memory/")][:6]
        if notes:
            rel_lines.append("### Notes to read if needed")
            rel_lines += [f"- [[{n['title']}]] ({n['path']})" for n in notes]
        parts.append(f"## About “{topic.strip()}”\n" + ("\n".join(rel_lines) if rel_lines else "_Nothing found._"))

    events = tools.audit_events(limit=80)
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    verbs = {"note.create": "created", "note.update": "edited", "note.edit": "edited", "note.append": "added to",
             "note.move": "moved", "note.delete": "deleted", "memory.remember": "saved a fact in",
             "memory.forget": "retired a fact in", "memory.approve": "approved a fact", "memory.propose": "proposed a change to"}
    recent, seen = [], set()
    for e in events:
        if e["created_at"] < since or e["action"] not in verbs:
            continue
        what = e["details"].get("path") or e["details"].get("to") or ""
        if e["action"].startswith("note.") and what.startswith(("Memory/", "Agents/")):
            continue  # memory edits are reported as memory.* events; agent logs are noise here
        who = _actor_name(e["actor"])
        key = (who, e["action"], what)
        if key in seen:
            continue
        seen.add(key)
        recent.append(f"- {e['created_at'][:10]} {who} {verbs[e['action']]} {what}".rstrip())
        if len(recent) >= 8:
            break
    if recent:
        parts.append("## Changed in the last 7 days\n" + "\n".join(recent))

    q = review_queue()
    if q["count"]:
        parts.append(f"_{q['count']} memory item(s) are waiting for the owner's review. Facts marked unreviewed "
                     "may be wrong; prefer reviewed facts when they conflict._")

    text = "\n\n".join(parts)
    if len(text) > budget_chars:
        text = text[:budget_chars].rsplit("\n", 1)[0] + "\n…(brief truncated; use recall for more)"
        truncated = True
    return {"brief": text, "tokens": len(text) // 4, "truncated": truncated}


def _actor_name(actor: str) -> str:
    if actor == "web":
        return "Owner"
    if actor.startswith("mcp:"):
        from . import oauth
        return oauth.agent_name(actor[4:])
    return actor


def recall(query: str, *, limit: int = 10, scope: str = "all") -> dict:
    from . import retrieval
    out: dict = {"query": query}
    if scope in ("all", "memory"):
        out["facts"] = [_public(f) for f in search_facts(query, k=limit)]
    if scope in ("all", "notes"):
        hits = [h for h in retrieval.search(query, limit=limit + 5) if not h["path"].startswith("Memory/")][:limit]
        for h in hits:
            h["snippet"] = h["snippet"].replace(retrieval.HL_START, "**").replace(retrieval.HL_END, "**")
        out["notes"] = hits
    return out


# --------------------------------------------------------------------------- agent logs

def log(agent: str, text: str, actor: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise KOSError("Log text is empty")
    stamp = datetime.now().strftime("%H:%M")
    rel = f"Agents/{_safe_title(agent)}/Log/{date.today().isoformat()}.md"
    entry = "\n".join(f"- {stamp} {line}" if i == 0 else f"  {line}" for i, line in enumerate(text.splitlines()))
    with vault._write_lock:
        if vault.exists(rel):
            vault.append(rel, entry, actor)
        else:
            vault.create(rel, f"# {agent} — {date.today().isoformat()}\n\n{entry}\n", actor)
    return {"path": rel}


# --------------------------------------------------------------------------- migration from v2

LEGACY_TARGET = {"profile": PROFILE, "goal": PROFILE, "preference": RULES, "communication": RULES,
                 "project": "Memory/Topics/Projects.md", "business": "Memory/Topics/Business.md",
                 "technical": "Memory/Topics/Technical.md", "general": "Memory/Topics/General.md"}

TIDY_SKILL = """---
title: Memory tidy-up
category: skill
status: active
---

# Memory tidy-up

A weekly routine for an agent (e.g. Hermes) to keep Knowledge OS memory clean. Never edits Profile or Rules
directly — changes there are proposals the owner approves.

1. Call `memory_health`. It lists duplicate facts, stale facts (not updated in months), flagged facts and
   how full Profile and Rules are.
2. Duplicates: keep the clearer fact; `forget` the other with reason "duplicate of <id>".
3. Contradictions you notice while reading subjects: `remember` the correct version with `replaces=<old id>`
   and give `src` (where you learned it).
4. Stale facts: if you can confirm one is still true from recent notes or logs, re-save it with `replaces`;
   if it is clearly outdated, `forget` it with a reason. Otherwise leave it for the owner.
5. Write a short summary of what you changed with `log`.
"""


def migrate_legacy() -> dict:
    """One-time: move v2 database memories into Memory/ files and export old chats to Markdown."""
    stats = {"memories": 0, "chats": 0}
    with db.connect() as conn:
        done_mem = db.get_meta(conn, "memories_migrated")
        done_chat = db.get_meta(conn, "chats_exported")
        has_mem = conn.execute("SELECT name FROM sqlite_master WHERE name='memories'").fetchone()
        rows = conn.execute("SELECT * FROM memories ORDER BY id").fetchall() if has_mem and not done_mem else []
    ensure_core()
    if not done_mem:
        by_path: dict[str, list[str]] = {}
        hist: dict[str, list[str]] = {}
        for r in rows:
            text = _clean_text(r["content"] or "")[:MAX_FACT_CHARS]
            if not text:
                continue
            target = LEGACY_TARGET.get((r["category"] or "general").lower(), "Memory/Topics/General.md")
            src = r["source"] if "source" in r.keys() else "user"
            fields = {"by": "you" if src in (None, "", "user", "review", "web") else src.replace("mcp:", ""),
                      "on": (r["created_at"] or now())[:10], "src": "migrated from v2"}
            status = r["status"] if "status" in r.keys() else "active"
            if status == "archived":
                fields.update({"until": (r["updated_at"] or now())[:10], "reason": "forgotten before v3"})
                hist.setdefault(target, []).append(format_fact(f"m{r['id']}", text, fields, struck=True))
            else:
                by_path.setdefault(target, []).append(format_fact(f"m{r['id']}", text, fields))
            stats["memories"] += 1
        for target in set(by_path) | set(hist):
            with vault._write_lock:
                _ensure_file(target, "system")
                cur = vault.read(target)
                content = cur["content"]
                for line in by_path.get(target, []):
                    content = _insert_line(content, "Facts", line)
                for line in hist.get(target, []):
                    content = _insert_line(content, "History", line)
                vault.write(target, content, "system", expected_hash=cur["hash"], reason="migrate")
        if not vault.exists("Skills/Memory tidy-up.md"):
            vault.create("Skills/Memory tidy-up.md", TIDY_SKILL, "system")
        with db.connect() as conn:
            db.set_meta(conn, "memories_migrated", now())
            if stats["memories"]:
                record_event(conn, "memory.migrate", details={"facts": stats["memories"]}, actor="system")
    if not done_chat:
        stats["chats"] = _export_chats()
        with db.connect() as conn:
            db.set_meta(conn, "chats_exported", now())
    return stats


def _export_chats() -> int:
    with db.connect() as conn:
        if not conn.execute("SELECT name FROM sqlite_master WHERE name='chats'").fetchone():
            return 0
        chats = conn.execute("SELECT * FROM chats ORDER BY created_at").fetchall()
        n = 0
        for c in chats:
            msgs = conn.execute("SELECT role, content, created_at FROM messages WHERE chat_id=? ORDER BY id",
                                (c["id"],)).fetchall()
            if not msgs:
                continue
            day = (c["created_at"] or now())[:10]
            title = _safe_title(c["title"] or "Conversation")[:70]
            rel = f"Archive/Chats/{day} {title}.md"
            i = 2
            while vault.exists(rel):
                rel = f"Archive/Chats/{day} {title} {i}.md"
                i += 1
            body = [f"---\ntype: chat-archive\ncreated: {day}\n---\n", f"# {c['title'] or 'Conversation'}", ""]
            for m in msgs:
                who = "You" if m["role"] == "user" else "Assistant"
                body += [f"**{who}** · {(m['created_at'] or '')[:16].replace('T', ' ')}", "", (m["content"] or "").strip(), ""]
            vault.create(rel, "\n".join(body).rstrip() + "\n", "system")
            n += 1
    return n


__all__ = ["remember", "forget", "brief", "recall", "log", "search_facts", "list_facts", "review_queue",
           "approve_fact", "reject_fact", "resolve_proposal", "health", "subjects", "migrate_legacy",
           "parse_facts", "reindex_facts", "time", "jdumps"]
