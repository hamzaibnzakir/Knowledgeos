"""The vault: the Markdown files are the source of truth.

All mutations go through this module. It enforces safe paths, writes files
atomically, keeps a version snapshot before every overwrite, moves deletions
to a restorable trash and keeps the index in step.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import uuid
import zlib
from pathlib import Path, PurePosixPath

from . import db
from .config import get_settings
from .util import Conflict, KOSError, NotFound, now, record_event, sha256

_write_lock = threading.RLock()

_BAD_CHARS = re.compile(r'[\x00-\x1f<>:"|?*]')
MAX_NOTE_BYTES = 5 * 1024 * 1024


def normalize_rel(rel: str, *, note: bool = True) -> str:
    """Validate and normalise a vault-relative path. Raises KOSError on anything unsafe."""
    raw = str(rel or "").strip().replace("\\", "/")
    while raw.startswith("/"):
        raw = raw[1:]
    if not raw or raw.endswith("/"):
        raise KOSError("A file name is required")
    parts = [p.strip() for p in raw.split("/")]
    clean: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise KOSError("Paths may not leave the vault")
        if part.startswith("."):
            raise KOSError("Hidden files and folders are not accessible")
        if _BAD_CHARS.search(part):
            raise KOSError(f"Invalid character in path segment '{part[:40]}'")
        clean.append(part)
    if not clean:
        raise KOSError("A file name is required")
    out = "/".join(clean)
    if note and not out.lower().endswith(".md"):
        out += ".md"
    stem = PurePosixPath(out).stem.strip()
    if not stem:
        raise KOSError("A meaningful file name is required")
    if len(out) > 400:
        raise KOSError("Path is too long")
    return out


def normalize_folder(rel: str) -> str:
    raw = str(rel or "").strip().replace("\\", "/").strip("/")
    if not raw:
        return ""
    return normalize_rel(raw, note=False)


def resolve(rel: str, *, note: bool = True) -> tuple[str, Path]:
    clean = normalize_rel(rel, note=note)
    vault = get_settings().vault_dir.resolve()
    p = (vault / clean).resolve()
    if not p.is_relative_to(vault) or p == vault:
        raise KOSError("Paths may not leave the vault")
    return clean, p


def _atomic_write(p: Path, content: str) -> None:
    data = content.encode("utf-8")
    if len(data) > MAX_NOTE_BYTES:
        raise KOSError("Note is larger than the 5 MB limit")
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".kos-", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _snapshot(conn, rel: str, content: str, actor: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO note_versions(path,content,content_hash,actor,reason,created_at) VALUES(?,?,?,?,?,?)",
        (rel, zlib.compress(content.encode("utf-8")), sha256(content), actor, reason, now()),
    )
    keep = get_settings().versions_keep
    conn.execute(
        "DELETE FROM note_versions WHERE path=? AND id NOT IN (SELECT id FROM note_versions WHERE path=? ORDER BY id DESC LIMIT ?)",
        (rel, rel, keep),
    )


def exists(rel: str) -> bool:
    try:
        _, p = resolve(rel)
    except KOSError:
        return False
    return p.is_file()


def read(rel: str) -> dict:
    clean, p = resolve(rel)
    if not p.is_file():
        raise NotFound("Note not found")
    content = p.read_text(encoding="utf-8", errors="replace")
    st = p.stat()
    return {"path": clean, "content": content, "hash": sha256(content), "mtime": st.st_mtime, "size": st.st_size}


def _index(clean: str) -> None:
    from . import indexer
    indexer.index_path(clean)


def create(rel: str, content: str, actor: str, *, chat_id: str | None = None) -> dict:
    clean, p = resolve(rel)
    with _write_lock:
        if p.exists():
            raise Conflict("A note already exists at that path", path=clean)
        _atomic_write(p, content)
        _index(clean)
        with db.connect() as conn:
            event = record_event(conn, "note.create", details={"path": clean}, actor=actor, chat_id=chat_id)
    return {"path": clean, "hash": sha256(content), "event": event}


def write(rel: str, content: str, actor: str, *, expected_hash: str | None = None,
          reason: str = "update", chat_id: str | None = None) -> dict:
    """Overwrite a note. If expected_hash is given and the file changed meanwhile, raise Conflict."""
    clean, p = resolve(rel)
    with _write_lock:
        if not p.is_file():
            raise NotFound("Note not found")
        old = p.read_text(encoding="utf-8", errors="replace")
        old_hash = sha256(old)
        if expected_hash and expected_hash != old_hash:
            raise Conflict("The note changed since you loaded it. Reload and re-apply your edit.",
                           path=clean, current_hash=old_hash)
        if old == content:
            return {"path": clean, "hash": old_hash, "changed": False}
        with db.connect() as conn:
            _snapshot(conn, clean, old, actor, reason)
        _atomic_write(p, content)
        _index(clean)
        with db.connect() as conn:
            event = record_event(conn, f"note.{reason}", details={"path": clean}, actor=actor, chat_id=chat_id)
    return {"path": clean, "hash": sha256(content), "changed": True, "event": event}


def append(rel: str, text: str, actor: str, *, heading: str | None = None, chat_id: str | None = None) -> dict:
    """Append text to the end of a note, or to the end of a named section."""
    with _write_lock:
        cur = read(rel)
        content = cur["content"]
        addition = text.strip("\n")
        if heading:
            lines = content.splitlines()
            target = heading.strip().lstrip("#").strip().lower()
            start = level = None
            for i, line in enumerate(lines):
                m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
                if m and start is None and m.group(2).strip().lower() == target:
                    start, level = i, len(m.group(1))
                elif m and start is not None and len(m.group(1)) <= (level or 6):
                    end = i
                    while end > start + 1 and not lines[end - 1].strip():
                        end -= 1
                    new_lines = lines[:end] + ["", addition] + lines[end:]
                    if new_lines[end + 2: end + 3] and new_lines[end + 2].strip():
                        new_lines.insert(end + 2, "")
                    return write(rel, "\n".join(new_lines) + "\n", actor, expected_hash=cur["hash"], reason="append", chat_id=chat_id)
            if start is None:
                new = content.rstrip("\n") + f"\n\n## {heading.strip().lstrip('#').strip()}\n\n{addition}\n"
                return write(rel, new, actor, expected_hash=cur["hash"], reason="append", chat_id=chat_id)
        new = content.rstrip("\n") + ("\n\n" if content.strip() else "") + addition + "\n"
        return write(rel, new, actor, expected_hash=cur["hash"], reason="append", chat_id=chat_id)


def replace_text(rel: str, find: str, replacement: str, actor: str, *, replace_all: bool = False,
                 chat_id: str | None = None) -> dict:
    if not find:
        raise KOSError("Text to find must not be empty")
    with _write_lock:
        cur = read(rel)
        n = cur["content"].count(find)
        if n == 0:
            raise KOSError("The text to replace was not found in the note")
        if n > 1 and not replace_all:
            raise KOSError(f"The text appears {n} times; include more surrounding text or set replace_all")
        new = cur["content"].replace(find, replacement) if replace_all else cur["content"].replace(find, replacement, 1)
        result = write(rel, new, actor, expected_hash=cur["hash"], reason="edit", chat_id=chat_id)
    result["replacements"] = n if replace_all else 1
    return result


def move(rel: str, new_rel: str, actor: str, *, update_links: bool = True, chat_id: str | None = None) -> dict:
    """Rename or move a note. Optionally rewrites [[links]] in other notes that pointed at it."""
    from . import indexer
    from .markdown import link_key, rewrite_wiki_links

    clean, p = resolve(rel)
    new_clean, np_ = resolve(new_rel)
    if clean == new_clean:
        return {"path": clean, "updated_links": 0}
    with _write_lock:
        if not p.is_file():
            raise NotFound("Note not found")
        if np_.exists() and np_ != p:
            raise Conflict("A note already exists at the destination")
        backlinkers = indexer.backlinks(clean)
        old_keys = {link_key(clean), link_key(PurePosixPath(clean).stem)}
        np_.parent.mkdir(parents=True, exist_ok=True)
        os.replace(p, np_)
        indexer.remove_path(clean)
        indexer.index_path(new_clean)
        _prune_empty_dirs(p.parent)
        updated = 0
        if update_links:
            new_stem = PurePosixPath(new_clean).stem
            # Use the bare name when it is unique, otherwise the full path.
            target = new_stem if len(indexer.resolve_key(link_key(new_stem))) <= 1 else new_clean[:-3]
            for src in backlinkers:
                src_path = new_clean if src == clean else src
                try:
                    cur = read(src_path)
                except NotFound:
                    continue
                new_content, n = rewrite_wiki_links(cur["content"], old_keys, target)
                if n:
                    write(src_path, new_content, actor, expected_hash=cur["hash"], reason="relink", chat_id=chat_id)
                    updated += n
        with db.connect() as conn:
            conn.execute("UPDATE favorites SET path=? WHERE path=?", (new_clean, clean))
            conn.execute("UPDATE note_versions SET path=? WHERE path=?", (new_clean, clean))
            event = record_event(conn, "note.move", details={"from": clean, "to": new_clean, "updated_links": updated},
                                 actor=actor, chat_id=chat_id)
    return {"path": new_clean, "from": clean, "updated_links": updated, "event": event}


def _prune_empty_dirs(folder: Path) -> None:
    vault = get_settings().vault_dir.resolve()
    keep = {vault / n for n in ("Notes", "Projects", "Resources", "Skills", "Agents", "Inbox", "Attachments")}
    folder = folder.resolve()
    while folder != vault and folder.is_relative_to(vault) and folder not in keep:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def delete(rel: str, actor: str, *, chat_id: str | None = None) -> dict:
    """Move a note to the trash (restorable)."""
    from . import indexer

    clean, p = resolve(rel)
    with _write_lock:
        if not p.is_file():
            raise NotFound("Note not found")
        trash_id = uuid.uuid4().hex[:12]
        dest = get_settings().trash_dir / trash_id / clean
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), dest)
        indexer.remove_path(clean)
        _prune_empty_dirs(p.parent)
        with db.connect() as conn:
            conn.execute("INSERT INTO trash(id,path,stored_as,actor,deleted_at) VALUES(?,?,?,?,?)",
                         (trash_id, clean, f"{trash_id}/{clean}", actor, now()))
            conn.execute("DELETE FROM favorites WHERE path=?", (clean,))
            event = record_event(conn, "note.delete", details={"path": clean, "trash_id": trash_id},
                                 actor=actor, chat_id=chat_id)
    return {"path": clean, "trash_id": trash_id, "event": event}


def list_trash() -> list[dict]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT id,path,actor,deleted_at FROM trash ORDER BY deleted_at DESC")]


def restore_trash(trash_id: str, actor: str) -> dict:
    with _write_lock, db.connect() as conn:
        row = conn.execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
        if not row:
            raise NotFound("Trash item not found")
        src = get_settings().trash_dir / row["stored_as"]
        if not src.is_file():
            conn.execute("DELETE FROM trash WHERE id=?", (trash_id,))
            raise NotFound("Trash file is missing")
        target = row["path"]
        clean, p = resolve(target)
        if p.exists():
            stem = PurePosixPath(clean)
            clean, p = resolve(str(stem.with_name(f"{stem.stem} (restored){stem.suffix}")))
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), p)
        shutil.rmtree(get_settings().trash_dir / trash_id, ignore_errors=True)
        conn.execute("DELETE FROM trash WHERE id=?", (trash_id,))
        _index(clean)
        event = record_event(conn, "note.restore", details={"path": clean, "trash_id": trash_id}, actor=actor)
    return {"path": clean, "event": event}


def purge_trash(trash_id: str, actor: str) -> dict:
    with _write_lock, db.connect() as conn:
        row = conn.execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
        if not row:
            raise NotFound("Trash item not found")
        shutil.rmtree(get_settings().trash_dir / trash_id, ignore_errors=True)
        conn.execute("DELETE FROM trash WHERE id=?", (trash_id,))
        record_event(conn, "note.purge", details={"path": row["path"]}, actor=actor)
    return {"ok": True}


def list_versions(rel: str) -> list[dict]:
    clean = normalize_rel(rel)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id,path,content_hash,actor,reason,created_at,length(content) AS stored_bytes "
            "FROM note_versions WHERE path=? ORDER BY id DESC", (clean,)).fetchall()
    return [dict(r) for r in rows]


def get_version(version_id: int) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM note_versions WHERE id=?", (version_id,)).fetchone()
    if not row:
        raise NotFound("Version not found")
    out = dict(row)
    out["content"] = zlib.decompress(row["content"]).decode("utf-8")
    return out


def restore_version(version_id: int, actor: str) -> dict:
    v = get_version(version_id)
    if exists(v["path"]):
        return write(v["path"], v["content"], actor, reason="restore")
    return create(v["path"], v["content"], actor)


def create_folder(rel: str, actor: str) -> dict:
    clean = normalize_folder(rel)
    if not clean:
        raise KOSError("Folder name is required")
    p = (get_settings().vault_dir / clean).resolve()
    if not p.is_relative_to(get_settings().vault_dir.resolve()):
        raise KOSError("Paths may not leave the vault")
    p.mkdir(parents=True, exist_ok=True)
    with db.connect() as conn:
        record_event(conn, "folder.create", details={"path": clean}, actor=actor)
    return {"path": clean}


def folder_tree() -> dict:
    """Nested folder structure with note counts (notes themselves come from the index)."""
    vault = get_settings().vault_dir

    def walk(d: Path) -> list[dict]:
        out = []
        for child in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            out.append({"name": child.name, "path": child.relative_to(vault).as_posix(), "children": walk(child)})
        return out

    return {"name": "", "path": "", "children": walk(vault)}


def iter_note_files():
    vault = get_settings().vault_dir
    for p in vault.rglob("*.md"):
        rel = p.relative_to(vault).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        if p.is_file():
            yield rel, p


def resolve_asset(rel: str) -> Path:
    """Resolve a non-note vault file (e.g. an image under Attachments/) for serving."""
    clean = normalize_rel(rel, note=False)
    vault = get_settings().vault_dir.resolve()
    p = (vault / clean).resolve()
    if not p.is_relative_to(vault) or not p.is_file():
        raise NotFound("File not found")
    return p


def save_asset(rel: str, data: bytes, actor: str) -> str:
    clean = normalize_rel(rel, note=False)
    vault = get_settings().vault_dir.resolve()
    p = (vault / clean).resolve()
    if not p.is_relative_to(vault):
        raise KOSError("Paths may not leave the vault")
    with _write_lock:
        if p.exists():
            stem, suffix = p.stem, p.suffix
            i = 2
            while p.exists():
                p = p.with_name(f"{stem}-{i}{suffix}")
                i += 1
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        clean = p.relative_to(vault).as_posix()
        with db.connect() as conn:
            record_event(conn, "asset.save", details={"path": clean, "bytes": len(data)}, actor=actor)
    return clean
