"""Incremental, deterministic indexer: notes -> metadata, FTS, links, tags, chunks.

Nothing here calls an AI model. Embeddings for new chunks are produced
afterwards by the background embedding worker.
"""
from __future__ import annotations

import logging
import posixpath
import threading
from collections import deque

from . import db
from .markdown import link_key, parse_note
from .util import jdumps, now, sha256

log = logging.getLogger("kos.indexer")
_lock = threading.RLock()


def _keys_for(path: str, title: str, aliases: list[str]) -> list[tuple[str, str]]:
    stem = posixpath.splitext(posixpath.basename(path))[0]
    keys = [(link_key(path), "path"), (link_key(stem), "name"), (link_key(title), "title")]
    keys += [(link_key(a), "alias") for a in aliases if a.strip()]
    seen, out = set(), []
    for k, kind in keys:
        if k and k not in seen:
            seen.add(k)
            out.append((k, kind))
    return out


_KIND_RANK = {"path": 0, "name": 1, "title": 2, "alias": 3}


def resolve_key(key: str, conn=None) -> list[str]:
    """Paths matching a link key, best match first."""
    def run(c):
        rows = c.execute("SELECT path, kind FROM name_keys WHERE key=?", (key,)).fetchall()
        rows = sorted(rows, key=lambda r: (_KIND_RANK.get(r["kind"], 9), len(r["path"]), r["path"]))
        out: list[str] = []
        for r in rows:
            if r["path"] not in out:
                out.append(r["path"])
        return out
    if conn is not None:
        return run(conn)
    with db.connect() as c:
        return run(c)


def index_path(rel: str, *, force: bool = False) -> bool:
    """(Re)index one note from disk. Returns True if anything changed."""
    from . import vault

    try:
        clean, p = vault.resolve(rel)
    except Exception:
        return False
    if not p.is_file():
        remove_path(rel)
        return True
    content = p.read_text(encoding="utf-8", errors="replace")
    st = p.stat()
    h = sha256(content)
    with _lock, db.connect() as conn:
        row = conn.execute("SELECT content_hash, created_at FROM notes WHERE path=?", (clean,)).fetchone()
        if row and row["content_hash"] == h and not force:
            conn.execute("UPDATE notes SET mtime=?, size=? WHERE path=?", (st.st_mtime, st.st_size, clean))
            return False
        parsed = parse_note(clean, content)
        stamp = now()
        created = (row["created_at"] if row else None) or str(parsed.properties.get("created") or stamp)
        folder = posixpath.dirname(clean)
        with db.transaction(conn):
            conn.execute(
                """INSERT INTO notes(path,title,folder,content_hash,mtime,size,word_count,tags,aliases,properties,
                       headings,excerpt,created_at,updated_at,indexed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET title=excluded.title, folder=excluded.folder,
                       content_hash=excluded.content_hash, mtime=excluded.mtime, size=excluded.size,
                       word_count=excluded.word_count, tags=excluded.tags, aliases=excluded.aliases,
                       properties=excluded.properties, headings=excluded.headings, excerpt=excluded.excerpt,
                       updated_at=excluded.updated_at, indexed_at=excluded.indexed_at""",
                (clean, parsed.title, folder, h, st.st_mtime, st.st_size, parsed.word_count, jdumps(parsed.tags),
                 jdumps(parsed.aliases), jdumps(parsed.properties), jdumps(parsed.headings), parsed.excerpt,
                 created, stamp, stamp),
            )
            conn.execute("DELETE FROM notes_fts WHERE path=?", (clean,))
            conn.execute("INSERT INTO notes_fts(path,title,aliases,tags,body) VALUES(?,?,?,?,?)",
                         (clean, parsed.title, " ".join(parsed.aliases), " ".join(parsed.tags), parsed.body))

            conn.execute("DELETE FROM note_tags WHERE path=?", (clean,))
            conn.executemany("INSERT OR IGNORE INTO note_tags(path,tag) VALUES(?,?)", [(clean, t) for t in parsed.tags])

            conn.execute("DELETE FROM name_keys WHERE path=?", (clean,))
            new_keys = _keys_for(clean, parsed.title, parsed.aliases)
            conn.executemany("INSERT OR IGNORE INTO name_keys(key,path,kind) VALUES(?,?,?)",
                             [(k, clean, kind) for k, kind in new_keys])

            conn.execute("DELETE FROM links WHERE source=?", (clean,))
            for link in parsed.links:
                matches = resolve_key(link.key, conn)
                target = matches[0] if matches else None
                conn.execute("INSERT OR IGNORE INTO links(source,target_key,target_raw,target_path,kind) VALUES(?,?,?,?,?)",
                             (clean, link.key, link.target, target, link.kind))

            # Other notes may have been linking to this note's names before it existed / was renamed.
            for k, _kind in new_keys:
                conn.execute("UPDATE links SET target_path=? WHERE target_key=? AND target_path IS NULL", (clean, k))

            old_ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE path=?", (clean,))]
            if old_ids:
                conn.execute("DELETE FROM chunks WHERE path=?", (clean,))
                conn.execute("DELETE FROM chunks_fts WHERE path=?", (clean,))
            for ch in parsed.chunks:
                cur = conn.execute("INSERT INTO chunks(path,ord,heading,text,hash,tokens) VALUES(?,?,?,?,?,?)",
                                   (clean, ch.ord, ch.heading, ch.text, ch.hash, ch.tokens))
                conn.execute("INSERT INTO chunks_fts(text,heading,title,chunk_id,path) VALUES(?,?,?,?,?)",
                             (ch.text, ch.heading, parsed.title, cur.lastrowid, clean))
            from . import memory
            memory.reindex_facts(conn, clean, content)
            db.set_meta(conn, "index_generation", str(_bump_generation(conn)))
    _notify_embeddings()
    return True


def _bump_generation(conn) -> int:
    return int(db.get_meta(conn, "index_generation", "0") or 0) + 1


def _notify_embeddings() -> None:
    try:
        from . import embeddings
        embeddings.notify()
    except Exception:  # pragma: no cover - embeddings are optional
        pass


def remove_path(rel: str) -> None:
    from . import vault
    try:
        clean = vault.normalize_rel(rel)
    except Exception:
        return
    with _lock, db.connect() as conn:
        with db.transaction(conn):
            for t in ("notes", "notes_fts", "note_tags", "name_keys", "chunks", "chunks_fts", "facts", "facts_fts"):
                conn.execute(f"DELETE FROM {t} WHERE path=?", (clean,))
            conn.execute("DELETE FROM links WHERE source=?", (clean,))
            dangling = conn.execute("SELECT DISTINCT target_key FROM links WHERE target_path=?", (clean,)).fetchall()
            conn.execute("UPDATE links SET target_path=NULL WHERE target_path=?", (clean,))
            for r in dangling:
                matches = resolve_key(r["target_key"], conn)
                if matches:
                    conn.execute("UPDATE links SET target_path=? WHERE target_key=? AND target_path IS NULL",
                                 (matches[0], r["target_key"]))
            db.set_meta(conn, "index_generation", str(_bump_generation(conn)))


def reconcile(full: bool = False) -> dict:
    """Bring the index in line with the files on disk (handles edits made outside the app)."""
    from . import vault

    stats = {"indexed": 0, "removed": 0, "unchanged": 0}
    with db.connect() as conn:
        known = {r["path"]: (r["mtime"], r["size"]) for r in conn.execute("SELECT path, mtime, size FROM notes")}
    on_disk = set()
    for rel, p in vault.iter_note_files():
        on_disk.add(rel)
        st = p.stat()
        prev = known.get(rel)
        if full or prev is None or abs(prev[0] - st.st_mtime) > 1e-6 or prev[1] != st.st_size:
            if index_path(rel, force=full):
                stats["indexed"] += 1
            else:
                stats["unchanged"] += 1
        else:
            stats["unchanged"] += 1
    for rel in set(known) - on_disk:
        remove_path(rel)
        stats["removed"] += 1
    if full or stats["indexed"] or stats["removed"]:
        _reresolve_all()
    return stats


def _reresolve_all() -> None:
    with _lock, db.connect() as conn, db.transaction(conn):
        for r in conn.execute("SELECT DISTINCT target_key FROM links").fetchall():
            matches = resolve_key(r["target_key"], conn)
            conn.execute("UPDATE links SET target_path=? WHERE target_key=?", (matches[0] if matches else None, r["target_key"]))


def rebuild() -> dict:
    with _lock, db.connect() as conn, db.transaction(conn):
        for t in ("notes", "notes_fts", "note_tags", "name_keys", "links", "chunks", "chunks_fts", "facts", "facts_fts"):
            conn.execute(f"DELETE FROM {t}")
    return reconcile(full=True)


# --------------------------------------------------------------------------- queries

def backlinks(rel: str) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute("SELECT DISTINCT source FROM links WHERE target_path=? AND source<>? ORDER BY source",
                            (rel, rel)).fetchall()
    return [r[0] for r in rows]


def outgoing(rel: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("SELECT target_raw, target_path, kind FROM links WHERE source=? ORDER BY target_raw",
                            (rel,)).fetchall()
    return [dict(r) for r in rows]


def unlinked_mentions(rel: str, limit: int = 20) -> list[dict]:
    """Notes that mention this note's title in plain text without linking to it."""
    with db.connect() as conn:
        row = conn.execute("SELECT title FROM notes WHERE path=?", (rel,)).fetchone()
        if not row or len(row["title"]) < 3:
            return []
        phrase = '"' + row["title"].replace('"', " ") + '"'
        try:
            rows = conn.execute(
                "SELECT path, snippet(notes_fts, 4, '[', ']', '…', 12) AS snippet FROM notes_fts "
                "WHERE notes_fts MATCH ? AND path<>? LIMIT ?", (f"body:{phrase}", rel, limit * 2)).fetchall()
        except Exception:
            return []
        linked = set(backlinks(rel))
        return [dict(r) for r in rows if r["path"] not in linked][:limit]


def graph(include_tags: bool = False, include_unresolved: bool = False, folder: str | None = None) -> dict:
    with db.connect() as conn:
        q = "SELECT path,title,folder,tags,word_count,updated_at FROM notes"
        args: tuple = ()
        if folder:
            q += " WHERE folder=? OR folder LIKE ?"
            args = (folder, folder + "/%")
        notes = conn.execute(q, args).fetchall()
        paths = {n["path"] for n in notes}
        links = conn.execute("SELECT source,target_path,target_key,target_raw,kind FROM links").fetchall()
    nodes = []
    for n in notes:
        nodes.append({"id": n["path"], "type": "note", "label": n["title"], "folder": n["folder"],
                      "tags": _tags(n["tags"]), "words": n["word_count"], "updated_at": n["updated_at"]})
    edges = []
    ghost: dict[str, str] = {}
    for l in links:
        if l["source"] not in paths:
            continue
        if l["target_path"]:
            if l["target_path"] in paths and l["target_path"] != l["source"]:
                edges.append({"source": l["source"], "target": l["target_path"], "type": "link"})
        elif include_unresolved:
            gid = "missing:" + l["target_key"]
            ghost[gid] = l["target_raw"]
            edges.append({"source": l["source"], "target": gid, "type": "unresolved"})
    for gid, label in ghost.items():
        nodes.append({"id": gid, "type": "missing", "label": label})
    if include_tags:
        tag_ids = set()
        for n in notes:
            for t in _tags(n["tags"]):
                tid = "tag:" + t
                if tid not in tag_ids:
                    tag_ids.add(tid)
                    nodes.append({"id": tid, "type": "tag", "label": "#" + t})
                edges.append({"source": n["path"], "target": tid, "type": "tag"})
    # de-duplicate edges
    seen, uniq = set(), []
    for e in edges:
        k = (e["source"], e["target"], e["type"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    degree: dict[str, int] = {}
    for e in uniq:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    for n in nodes:
        n["degree"] = degree.get(n["id"], 0)
    return {"nodes": nodes, "edges": uniq}


def local_graph(rel: str, depth: int = 1, include_tags: bool = False) -> dict:
    full = graph(include_tags=include_tags, include_unresolved=True)
    adj: dict[str, set[str]] = {}
    for e in full["edges"]:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])
    keep = {rel}
    frontier = deque([(rel, 0)])
    while frontier:
        node, d = frontier.popleft()
        if d >= depth:
            continue
        for nxt in adj.get(node, ()):
            if nxt not in keep:
                keep.add(nxt)
                frontier.append((nxt, d + 1))
    return {"nodes": [n for n in full["nodes"] if n["id"] in keep],
            "edges": [e for e in full["edges"] if e["source"] in keep and e["target"] in keep],
            "center": rel}


def orphans() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT path,title,updated_at FROM notes n
               WHERE NOT EXISTS (SELECT 1 FROM links l WHERE l.source=n.path AND l.target_path IS NOT NULL AND l.target_path<>n.path)
                 AND NOT EXISTS (SELECT 1 FROM links l WHERE l.target_path=n.path AND l.source<>n.path)
               ORDER BY updated_at DESC""").fetchall()
    return [dict(r) for r in rows]


def unresolved_links() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT target_raw, count(*) AS refs, group_concat(source, '\n') AS sources FROM links "
            "WHERE target_path IS NULL GROUP BY target_key ORDER BY refs DESC").fetchall()
    return [{"target": r["target_raw"], "refs": r["refs"], "sources": r["sources"].split("\n")} for r in rows]


def tags() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("SELECT tag, count(*) AS count FROM note_tags GROUP BY tag ORDER BY count DESC, tag").fetchall()
    return [dict(r) for r in rows]


def _tags(raw: str) -> list[str]:
    from .util import jloads
    return jloads(raw, [])


def stats() -> dict:
    with db.connect() as conn:
        def one(q: str) -> int:
            return conn.execute(q).fetchone()[0]
        return {
            "notes": one("SELECT count(*) FROM notes"),
            "words": one("SELECT coalesce(sum(word_count),0) FROM notes"),
            "links": one("SELECT count(*) FROM links WHERE target_path IS NOT NULL"),
            "unresolved_links": one("SELECT count(*) FROM links WHERE target_path IS NULL"),
            "tags": one("SELECT count(DISTINCT tag) FROM note_tags"),
            "chunks": one("SELECT count(*) FROM chunks"),
            "memories": one("SELECT count(*) FROM facts WHERE status='current'"),
            "unreviewed": one("SELECT count(*) FROM facts WHERE status='current' AND review='new'")
            + one("SELECT count(*) FROM proposals WHERE status='pending'"),
            "skills": one("SELECT count(*) FROM notes WHERE folder='Skills' OR folder LIKE 'Skills/%'"),
        }

