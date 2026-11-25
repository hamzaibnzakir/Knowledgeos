"""Backups: consistent SQLite snapshot + the whole Markdown vault in one tar.gz."""
from __future__ import annotations

import io
import re
import sqlite3
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import get_settings
from .util import NotFound, record_event

NAME_RE = re.compile(r"^kos-backup-\d{8}-\d{6}\.tar\.gz$")


def create_backup(actor: str = "system") -> dict:
    s = get_settings()
    s.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"kos-backup-{stamp}.tar.gz"
    dest = s.backups_dir / name
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "knowledge.db"
        src = sqlite3.connect(s.db_path)
        out = sqlite3.connect(snap)
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
        partial = dest.with_suffix(".partial")
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(s.vault_dir, arcname="vault", filter=_skip_hidden)
            tar.add(snap, arcname=".brainbox/knowledge.db")
            if s.auth_file.is_file():
                tar.add(s.auth_file, arcname=".brainbox/auth.json")
        partial.replace(dest)
    _prune(s.backups_dir, s.backup_keep)
    with db.connect() as conn:
        record_event(conn, "backup.create", details={"name": name, "bytes": dest.stat().st_size}, actor=actor)
        db.set_meta(conn, "last_backup", stamp)
    return {"name": name, "bytes": dest.stat().st_size}


def _skip_hidden(info: tarfile.TarInfo):
    parts = Path(info.name).parts
    if any(p.startswith(".") for p in parts[1:]):
        return None
    return info


def _prune(folder: Path, keep: int) -> None:
    backups = sorted(p for p in folder.iterdir() if NAME_RE.match(p.name))
    for old in backups[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)


def list_backups() -> list[dict]:
    folder = get_settings().backups_dir
    if not folder.exists():
        return []
    return [{"name": p.name, "bytes": p.stat().st_size,
             "created_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}
            for p in sorted(folder.iterdir(), reverse=True) if NAME_RE.match(p.name)]


def backup_path(name: str) -> Path:
    if not NAME_RE.match(name):
        raise NotFound("Backup not found")
    p = get_settings().backups_dir / name
    if not p.is_file():
        raise NotFound("Backup not found")
    return p


def due(hours: int) -> bool:
    if hours <= 0:
        return False
    with db.connect() as conn:
        last = db.get_meta(conn, "last_backup")
    if not last:
        return True
    then = datetime.strptime(last, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() >= hours * 3600


def export_vault_zip() -> bytes:
    """The Markdown vault as a zip (what you would open in Obsidian)."""
    vault = get_settings().vault_dir
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in vault.rglob("*"):
            rel = p.relative_to(vault)
            if p.is_file() and not any(part.startswith(".") for part in rel.parts):
                z.write(p, arcname=str(Path("vault") / rel))
    return buf.getvalue()
