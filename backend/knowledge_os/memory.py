"""Intentional memory: small, durable facts kept apart from notes.

Nothing is saved automatically. Memories are created by the user, by an
explicit agent `remember` call, or by reviewing extracted candidates.
Forgetting archives a memory (restorable); purge removes it permanently.
"""
from __future__ import annotations

import re

import numpy as np

from . import db
from .util import KOSError, NotFound, jloads, now, record_event, sha256

CATEGORIES = ["profile", "preference", "project", "business", "technical", "goal", "communication", "general"]
_WORD = re.compile(r"\w+", re.U)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".")


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2}


def _row(r) -> dict:
    return dict(r)


def list_memories(status: str = "active", category: str | None = None, q: str | None = None, limit: int = 500) -> list[dict]:
    sql = "SELECT id,content,category,confidence,source,status,pinned,created_at,updated_at FROM memories WHERE 1=1"
    args: list = []
    if status != "all":
        sql += " AND status=?"
        args.append(status)
    if category:
        sql += " AND category=?"
        args.append(category)
    if q:
        sql += " AND content LIKE ? ESCAPE '\\'"
        args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    sql += " ORDER BY pinned DESC, updated_at DESC LIMIT ?"
    args.append(max(1, min(limit, 2000)))
    with db.connect() as conn:
        return [_row(r) for r in conn.execute(sql, args)]


def get(memory_id: int) -> dict:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
    if not r:
        raise NotFound("Memory not found")
    return _row(r)


def find_similar(content: str, threshold: float = 0.85) -> dict | None:
    """Exact (normalised) duplicate, or a near-duplicate by token overlap."""
    norm = _norm(content)
    toks = _tokens(content)
    with db.connect() as conn:
        exact = conn.execute("SELECT * FROM memories WHERE norm=? AND status='active'", (norm,)).fetchone()
        if exact:
            return _row(exact)
        if len(toks) < 3:
            return None
        for r in conn.execute("SELECT * FROM memories WHERE status='active'"):
            other = _tokens(r["content"])
            if not other:
                continue
            jacc = len(toks & other) / len(toks | other)
            if jacc >= threshold:
                return _row(r)
    return None


def create(content: str, *, category: str = "general", confidence: float = 1.0, source: str = "user",
           actor: str = "web", chat_id: str | None = None) -> dict:
    text = content.strip()
    if not text:
        raise KOSError("Memory content cannot be empty")
    if len(text) > 2000:
        raise KOSError("A memory should be a short fact (max 2000 characters)")
    category = (category or "general").strip().lower()[:40] or "general"
    confidence = max(0.0, min(1.0, float(confidence)))
    dup = find_similar(text)
    if dup:
        return {"created": False, "duplicate_of": dup["id"], "memory": dup}
    t = now()
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO memories(content,category,confidence,created_at,updated_at,source,status,norm) VALUES(?,?,?,?,?,?,?,?)",
            (text, category, confidence, t, t, source[:80], "active", _norm(text)))
        mid = cur.lastrowid
        event = record_event(conn, "memory.create", details={"memory_id": mid, "category": category},
                             actor=actor, chat_id=chat_id)
    return {"created": True, "memory": get(mid), "event": event}


def update(memory_id: int, *, content: str | None = None, category: str | None = None,
           confidence: float | None = None, pinned: bool | None = None, actor: str = "web") -> dict:
    cur = get(memory_id)
    new_content = content.strip() if content is not None else cur["content"]
    if not new_content:
        raise KOSError("Memory content cannot be empty")
    with db.connect() as conn:
        conn.execute(
            "UPDATE memories SET content=?, norm=?, category=?, confidence=?, pinned=?, updated_at=? WHERE id=?",
            (new_content, _norm(new_content), (category or cur["category"])[:40],
             max(0.0, min(1.0, confidence)) if confidence is not None else cur["confidence"],
             int(pinned) if pinned is not None else cur["pinned"], now(), memory_id))
        record_event(conn, "memory.update", details={"memory_id": memory_id}, actor=actor)
    return get(memory_id)


def set_status(memory_id: int, status: str, actor: str = "web", chat_id: str | None = None) -> dict:
    get(memory_id)
    with db.connect() as conn:
        conn.execute("UPDATE memories SET status=?, updated_at=? WHERE id=?", (status, now(), memory_id))
        record_event(conn, "memory.forget" if status == "archived" else "memory.restore",
                     details={"memory_id": memory_id}, actor=actor, chat_id=chat_id)
    return get(memory_id)


def purge(memory_id: int, actor: str = "web") -> dict:
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        if cur.rowcount == 0:
            raise NotFound("Memory not found")
        record_event(conn, "memory.purge", details={"memory_id": memory_id}, actor=actor)
    return {"ok": True}


def search(query: str, k: int = 8) -> list[dict]:
    """Hybrid memory search: token overlap + embedding similarity when available."""
    from . import embeddings

    rows = list_memories(limit=2000)
    if not rows or not query.strip():
        return rows[:k]
    qt = _tokens(query)
    scores = np.zeros(len(rows), dtype=np.float32)
    for i, r in enumerate(rows):
        mt = _tokens(r["content"] + " " + r["category"])
        if qt and mt:
            scores[i] = len(qt & mt) / (len(qt) ** 0.5 * len(mt) ** 0.5)
    vecs = _memory_vectors(rows)
    if vecs is not None:
        qv = embeddings.embed_texts([query])
        if qv is not None and qv.shape[1] == vecs.shape[1]:
            floor = min(0.9, getattr(embeddings.get_embedder(), "sim_floor", 0.4) - 0.1)
            sims = np.clip((vecs @ qv[0] - floor) / (1 - floor), 0, 1)
            scores = 0.4 * scores + 0.6 * sims
    order = np.argsort(-scores)
    out = []
    for i in order[:k]:
        if scores[i] <= 0.05:
            continue
        item = dict(rows[i])
        item["score"] = round(float(scores[i]), 4)
        out.append(item)
    pinned = [r for r in rows if r["pinned"] and r["id"] not in {o["id"] for o in out}]
    return pinned + out


def _memory_vectors(rows: list[dict]) -> np.ndarray | None:
    """Embed memories, caching vectors in the embeddings table keyed by content hash."""
    from . import embeddings

    emb = embeddings.get_embedder()
    if not emb.available:
        return None
    keys = ["mem:" + sha256(r["content"]) for r in rows]
    with db.connect() as conn:
        found = {r["chunk_hash"]: np.frombuffer(r["vector"], dtype=np.float32)
                 for r in conn.execute(
                     f"SELECT chunk_hash, vector FROM embeddings WHERE model=? AND chunk_hash IN ({','.join('?' * len(keys))})",
                     (emb.name, *keys))}
    missing = [(k, r["content"]) for k, r in zip(keys, rows) if k not in found]
    if missing:
        vecs = embeddings.embed_texts([c for _, c in missing])
        if vecs is None:
            return None
        with db.connect() as conn, db.transaction(conn):
            for (k, _), v in zip(missing, vecs):
                conn.execute("INSERT OR REPLACE INTO embeddings(chunk_hash,model,dim,vector) VALUES(?,?,?,?)",
                             (k, emb.name, int(v.shape[0]), v.astype(np.float32).tobytes()))
                found[k] = v
    dims = {v.shape[0] for v in found.values()}
    if len(dims) != 1:
        return None
    return np.vstack([found[k] for k in keys])


EXTRACT_PROMPT = (
    "Extract durable, user-specific facts that the USER explicitly stated in the messages below. "
    "Ignore assistant text, instructions, questions, small talk and anything speculative. "
    "Each fact must be short, self-contained and phrased in the third person (e.g. 'Prefers OpenAI for the main agent'). "
    "Return JSON only: {\"memories\":[{\"content\":str,\"category\":one of "
    + "|".join(CATEGORIES) + ",\"confidence\":0..1}]}. Return at most 25 items; return an empty list if nothing qualifies."
)


def extract_candidates(chat_id: str, actor: str = "web") -> list[dict]:
    """Ask the utility model for candidate memories from the user's messages. Nothing is saved."""
    from .ai import get_gateway
    from .config import get_settings

    with db.connect() as conn:
        rows = conn.execute("SELECT content FROM messages WHERE chat_id=? AND role='user' ORDER BY id DESC LIMIT 40",
                            (chat_id,)).fetchall()
    if not rows:
        return []
    source = "\n\n".join(f"USER: {r['content'][:4000]}" for r in reversed(rows))
    result = get_gateway().chat(
        [{"role": "system", "content": EXTRACT_PROMPT}, {"role": "user", "content": source[:40000]}],
        model=get_settings().resolved_utility_model, json_mode=True, temperature=0.1,
        operation="memory.extract", actor=actor)
    data = jloads(result.content, {})
    items = data.get("memories", []) if isinstance(data, dict) else []
    out = []
    for it in items[:25]:
        if not isinstance(it, dict):
            continue
        content = str(it.get("content", "")).strip()
        if not content:
            continue
        cat = str(it.get("category", "general")).lower()
        dup = find_similar(content)
        out.append({"content": content, "category": cat if cat in CATEGORIES else "general",
                    "confidence": max(0.0, min(1.0, float(it.get("confidence", 0.8) or 0.8))),
                    "duplicate_of": dup["id"] if dup else None})
    return out
