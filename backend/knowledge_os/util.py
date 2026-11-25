from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from typing import Any


class KOSError(Exception):
    """Domain error with an HTTP-ish status. Messages never contain filesystem paths or secrets."""

    status = 400

    def __init__(self, message: str, status: int | None = None, **extra: Any):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status
        self.extra = extra


class NotFound(KOSError):
    status = 404


class Conflict(KOSError):
    status = 409


class Forbidden(KOSError):
    status = 403


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def slugify(value: str, max_len: int = 80) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value[:max_len] or "untitled"


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def jloads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def jdumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def record_event(
    conn: sqlite3.Connection,
    action: str,
    status: str = "completed",
    details: dict | None = None,
    actor: str = "system",
    chat_id: str | None = None,
) -> dict:
    stamp = now()
    payload = details or {}
    cur = conn.execute(
        "INSERT INTO action_events(chat_id,action,status,details,created_at,actor) VALUES(?,?,?,?,?,?)",
        (chat_id, action, status, jdumps(payload), stamp, actor),
    )
    return {"id": cur.lastrowid, "action": action, "status": status, "details": payload, "actor": actor, "created_at": stamp}
