"""Search and retrieval: lexical (SQLite FTS5) + semantic (local vectors) + graph.

`search()`  -> ranked notes for the UI and tools.
`retrieve()` -> ranked chunks for RAG.
`build_context()` -> a compact, token-budgeted context package with citations;
this is the only thing the chat model ever sees from the vault.
"""
from __future__ import annotations

import re
from collections import defaultdict

import numpy as np

from . import db, embeddings, indexer
from .config import get_settings
from .util import estimate_tokens, jloads

HL_START, HL_END = "\x02", "\x03"   # highlight markers; the client escapes text, then styles these
RRF_K = 60
_TERM = re.compile(r"[\wÀ-￿][\wÀ-￿'-]*", re.U)
STOPWORDS = set("""a an and are as at be but by for from has have how i in into is it its me my of on or our so
that the their them then there these they this to was we what when where which who why will with you your
did do does about can could should would""".split())


def fts_query(q: str, *, prefix: bool = True, all_terms: bool = False) -> str | None:
    terms = [t.lower() for t in _TERM.findall(q)]
    useful = [t for t in terms if t not in STOPWORDS] or terms
    if not useful:
        return None
    parts = []
    for t in useful[:16]:
        t = t.replace('"', "")
        parts.append(f'"{t}"*' if prefix and len(t) >= 3 else f'"{t}"')
    return (" AND " if all_terms else " OR ").join(parts)


def _filter_sql(folder: str | None, tag: str | None, alias: str = "n") -> tuple[str, list]:
    sql, args = "", []
    if folder:
        sql += f" AND ({alias}.folder=? OR {alias}.folder LIKE ?)"
        args += [folder, folder.rstrip("/") + "/%"]
    if tag:
        sql += f" AND EXISTS (SELECT 1 FROM note_tags t WHERE t.path={alias}.path AND (t.tag=? OR t.tag LIKE ?))"
        args += [tag.lower().lstrip("#"), tag.lower().lstrip("#") + "/%"]
    return sql, args


def _lexical_notes(conn, q: str, limit: int, folder, tag) -> list[dict]:
    match = fts_query(q)
    if not match:
        return []
    fsql, fargs = _filter_sql(folder, tag)
    try:
        rows = conn.execute(
            f"""SELECT f.path, n.title, n.folder, n.tags, n.updated_at, n.excerpt,
                       snippet(notes_fts, 4, '{HL_START}', '{HL_END}', '…', 16) AS snippet,
                       bm25(notes_fts, 0.0, 10.0, 8.0, 5.0, 1.0) AS rank
                FROM notes_fts f JOIN notes n ON n.path=f.path
                WHERE notes_fts MATCH ? {fsql} ORDER BY rank LIMIT ?""",
            (match, *fargs, limit)).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def search(q: str, *, limit: int = 30, mode: str = "hybrid", folder: str | None = None,
           tag: str | None = None) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    with db.connect() as conn:
        lexical = _lexical_notes(conn, q, limit * 2, folder, tag) if mode in ("hybrid", "keyword") else []
        semantic: list[tuple[str, int, float]] = []
        if mode in ("hybrid", "semantic"):
            best: dict[str, tuple[int, float]] = {}
            for cid, path, score in embeddings.relevant(embeddings.vector_search(q, k=limit * 4)):
                if path not in best or score > best[path][1]:
                    best[path] = (cid, score)
            semantic = sorted(((p, c, s) for p, (c, s) in best.items()), key=lambda x: -x[2])
            if folder or tag:
                fsql, fargs = _filter_sql(folder, tag)
                allowed = {r[0] for r in conn.execute(f"SELECT n.path FROM notes n WHERE 1=1 {fsql}", fargs)}
                semantic = [s for s in semantic if s[0] in allowed]
        scores: dict[str, float] = defaultdict(float)
        for i, r in enumerate(lexical):
            scores[r["path"]] += 1.0 / (RRF_K + i)
        for i, (p, _c, _s) in enumerate(semantic):
            scores[p] += 1.0 / (RRF_K + i)
        lex_by_path = {r["path"]: r for r in lexical}
        sem_by_path = {p: (c, s) for p, c, s in semantic}
        ordered = sorted(scores, key=lambda p: -scores[p])[:limit]
        out = []
        for p in ordered:
            if p in lex_by_path:
                r = lex_by_path[p]
                item = {"path": p, "title": r["title"], "folder": r["folder"], "tags": jloads(r["tags"], []),
                        "updated_at": r["updated_at"], "snippet": r["snippet"] or r["excerpt"]}
            else:
                n = conn.execute("SELECT title, folder, tags, updated_at FROM notes WHERE path=?", (p,)).fetchone()
                if not n:
                    continue
                ch = conn.execute("SELECT heading, text FROM chunks WHERE id=?", (sem_by_path[p][0],)).fetchone()
                snippet = (ch["text"][:240] + "…") if ch and len(ch["text"]) > 240 else (ch["text"] if ch else "")
                item = {"path": p, "title": n["title"], "folder": n["folder"], "tags": jloads(n["tags"], []),
                        "updated_at": n["updated_at"], "snippet": snippet}
            item["score"] = round(scores[p] * 1000, 3)
            item["match"] = ("keyword+semantic" if p in lex_by_path and p in sem_by_path
                             else "keyword" if p in lex_by_path else "semantic")
            out.append(item)
    return out


def quick_find(q: str, limit: int = 12) -> list[dict]:
    """Title/alias/path lookup for the command palette and link autocomplete."""
    q = (q or "").strip().lower()
    with db.connect() as conn:
        if not q:
            rows = conn.execute("SELECT path,title FROM notes ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
        like = "%" + q.replace("%", "").replace("_", "") + "%"
        rows = conn.execute(
            """SELECT DISTINCT n.path, n.title,
                      CASE WHEN lower(n.title)=? THEN 0 WHEN lower(n.title) LIKE ? THEN 1
                           WHEN k.key LIKE ? THEN 2 ELSE 3 END AS r
               FROM notes n LEFT JOIN name_keys k ON k.path=n.path
               WHERE lower(n.title) LIKE ? OR k.key LIKE ? OR lower(n.path) LIKE ?
               ORDER BY r, length(n.title) LIMIT ?""",
            (q, q + "%", q + "%", like, like, like, limit)).fetchall()
    seen, out = set(), []
    for r in rows:
        if r["path"] not in seen:
            seen.add(r["path"])
            out.append({"path": r["path"], "title": r["title"]})
    return out


# --------------------------------------------------------------------------- RAG

def retrieve(q: str, *, k: int = 12, folder: str | None = None, expand_graph: bool = True) -> list[dict]:
    """Chunk-level hybrid retrieval with one hop of graph expansion."""
    q = (q or "").strip()
    if not q:
        return []
    with db.connect() as conn:
        lexical: list[int] = []
        match = fts_query(q)
        if match:
            try:
                fsql = ""
                args: list = [match]
                if folder:
                    fsql = " AND (path LIKE ?)"
                    args.append(folder.rstrip("/") + "/%")
                rows = conn.execute(
                    f"SELECT chunk_id, bm25(chunks_fts, 1.0, 2.0, 4.0) AS rank FROM chunks_fts "
                    f"WHERE chunks_fts MATCH ? {fsql} ORDER BY rank LIMIT ?", (*args, k * 4)).fetchall()
                lexical = [int(r["chunk_id"]) for r in rows]
            except Exception:
                lexical = []
        semantic = [(cid, s) for cid, path, s in embeddings.relevant(embeddings.vector_search(q, k=k * 4), gap=0.15)
                    if not folder or path.startswith(folder.rstrip("/") + "/")]
        scores: dict[int, float] = defaultdict(float)
        for i, cid in enumerate(lexical):
            scores[cid] += 1.0 / (RRF_K + i)
        for i, (cid, _s) in enumerate(semantic):
            scores[cid] += 1.0 / (RRF_K + i)
        top = sorted(scores, key=lambda c: -scores[c])[:k]
        hits = _load_chunks(conn, top, scores)

        if expand_graph and hits:
            seed_paths = list(dict.fromkeys(h["path"] for h in hits[:3]))
            neighbours: set[str] = set()
            for p in seed_paths:
                for r in conn.execute("SELECT target_path FROM links WHERE source=? AND target_path IS NOT NULL", (p,)):
                    neighbours.add(r[0])
                for r in conn.execute("SELECT source FROM links WHERE target_path=?", (p,)):
                    neighbours.add(r[0])
            neighbours -= {h["path"] for h in hits}
            if neighbours and match:
                ph = ",".join("?" * len(neighbours))
                try:
                    rows = conn.execute(
                        f"SELECT chunk_id, path, bm25(chunks_fts) AS rank FROM chunks_fts WHERE chunks_fts MATCH ? "
                        f"AND path IN ({ph}) ORDER BY rank LIMIT 4", (match, *neighbours)).fetchall()
                except Exception:
                    rows = []
                extra_scores = {int(r["chunk_id"]): 0.5 / (RRF_K + i) for i, r in enumerate(rows)}
                for h in _load_chunks(conn, list(extra_scores), extra_scores):
                    h["via"] = "graph"
                    hits.append(h)
    return hits


def _load_chunks(conn, ids: list[int], scores: dict[int, float]) -> list[dict]:
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT c.id, c.path, c.heading, c.text, c.tokens, n.title, n.updated_at FROM chunks c "
        f"JOIN notes n ON n.path=c.path WHERE c.id IN ({ph})", ids)}
    out = []
    for cid in ids:
        r = rows.get(cid)
        if r:
            out.append({"chunk_id": cid, "path": r["path"], "title": r["title"], "heading": r["heading"],
                        "text": r["text"], "tokens": r["tokens"], "updated_at": r["updated_at"],
                        "score": round(scores.get(cid, 0) * 1000, 3), "via": "search"})
    return out


def build_context(q: str, *, budget: int | None = None, include_memories: bool = True,
                  extra_blocks: list[dict] | None = None, folder: str | None = None) -> dict:
    """Assemble a compact context package: ranked excerpts + relevant memories, within a token budget."""
    from . import memory

    budget = budget or get_settings().context_token_budget
    blocks: list[str] = []
    sources: list[dict] = []
    used = 0

    mems = memory.search_facts(q, k=8, include_flagged=False) if include_memories else []
    if mems:
        lines = [f"- {m['subject']}: {m['text']} [{m['id']}]" + (" (unreviewed)" if m["review"] == "new" else "")
                 for m in mems]
        text = "Relevant memory facts:\n" + "\n".join(lines)
        used += estimate_tokens(text)
        blocks.append(text)

    for b in extra_blocks or []:
        t = estimate_tokens(b["text"])
        if used + t > budget * 0.6:
            b = {**b, "text": b["text"][: int((budget * 0.6 - used) * 4)]}
            t = estimate_tokens(b["text"])
        if t <= 0 or used + t > budget:
            continue
        n = len(sources) + 1
        sources.append({"n": n, "label": b["label"], "kind": b.get("kind", "attachment")})
        blocks.append(f"[{n}] {b['label']}\n{b['text']}")
        used += t

    seen_text: set[str] = set()
    for h in retrieve(q, k=14, folder=folder):
        if h["path"].startswith("Memory/") and include_memories:
            continue  # facts were already included above
        key = h["text"][:200]
        if key in seen_text:
            continue
        seen_text.add(key)
        text = h["text"]
        t = estimate_tokens(text)
        if used + t > budget:
            remaining = budget - used
            if remaining < 120:
                break
            text = text[: remaining * 4] + "…"
            t = remaining
        n = len(sources) + 1
        heading = f" › {h['heading']}" if h["heading"] else ""
        sources.append({"n": n, "path": h["path"], "title": h["title"], "heading": h["heading"], "kind": "note",
                        "via": h.get("via", "search")})
        blocks.append(f"[{n}] {h['title']}{heading} ({h['path']})\n{text}")
        used += t
    return {"text": "\n\n".join(blocks), "sources": sources, "tokens": used, "memories": mems}


def related_notes(rel: str, limit: int = 10) -> list[dict]:
    """Notes related by links, shared tags and semantic similarity."""
    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, set[str]] = defaultdict(set)
    with db.connect() as conn:
        for r in conn.execute("SELECT target_path FROM links WHERE source=? AND target_path IS NOT NULL", (rel,)):
            scores[r[0]] += 3
            reasons[r[0]].add("linked")
        for r in conn.execute("SELECT source FROM links WHERE target_path=?", (rel,)):
            scores[r[0]] += 3
            reasons[r[0]].add("backlink")
        tag_rows = conn.execute(
            """SELECT t2.path, t1.tag, (SELECT count(*) FROM note_tags x WHERE x.tag=t1.tag) AS freq
               FROM note_tags t1 JOIN note_tags t2 ON t1.tag=t2.tag AND t2.path<>t1.path WHERE t1.path=?""",
            (rel,)).fetchall()
        for r in tag_rows:
            scores[r["path"]] += 1.5 / max(1, np.log2(1 + r["freq"]))
            reasons[r["path"]].add("#" + r["tag"])
        # second-degree: notes that share link targets with this one
        for r in conn.execute(
                """SELECT l2.source AS path, count(*) AS n FROM links l1 JOIN links l2
                   ON l1.target_path=l2.target_path AND l2.source<>l1.source
                   WHERE l1.source=? AND l1.target_path IS NOT NULL GROUP BY l2.source""", (rel,)):
            scores[r["path"]] += 0.8 * r["n"]
            reasons[r["path"]].add("shared links")
        try:
            ids, paths, matrix = embeddings._cache.get()
        except Exception:
            _ids, paths, matrix = [], [], None
        if matrix is not None and matrix.shape[0] and embeddings.get_embedder().available:
            idx = [i for i, p in enumerate(paths) if p == rel]
            if idx:
                centroid = matrix[idx].mean(axis=0)
                centroid /= (np.linalg.norm(centroid) or 1)
                sims = matrix @ centroid
                best: dict[str, float] = {}
                for i, s in enumerate(sims):
                    p = paths[i]
                    if p != rel and s > best.get(p, -1):
                        best[p] = float(s)
                floor = getattr(embeddings.get_embedder(), "sim_floor", 0.45) + 0.1
                for p, s in best.items():
                    if s >= floor:
                        scores[p] += 4 * (s - 0.5)
                        reasons[p].add("similar content")
        scores.pop(rel, None)
        top = sorted(scores, key=lambda p: -scores[p])[:limit]
        titles = {r["path"]: r["title"] for r in conn.execute(
            f"SELECT path,title FROM notes WHERE path IN ({','.join('?' * len(top))})", top)} if top else {}
    return [{"path": p, "title": titles.get(p, p), "score": round(scores[p], 3), "reasons": sorted(reasons[p])}
            for p in top if p in titles]


def note_context(rel: str) -> dict:
    """Everything an agent needs about one note, without the full text of its neighbours."""
    with db.connect() as conn:
        n = conn.execute("SELECT * FROM notes WHERE path=?", (rel,)).fetchone()
    if not n:
        return {}
    return {
        "path": rel, "title": n["title"], "tags": jloads(n["tags"], []), "aliases": jloads(n["aliases"], []),
        "properties": jloads(n["properties"], {}), "headings": jloads(n["headings"], []),
        "word_count": n["word_count"], "updated_at": n["updated_at"],
        "outgoing": indexer.outgoing(rel), "backlinks": indexer.backlinks(rel),
        "related": related_notes(rel, limit=8),
    }
