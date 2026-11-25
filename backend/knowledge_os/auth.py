"""Single-user authentication: password, server-side sessions, brute-force protection.

- Password: PBKDF2-SHA256 (compatible with the v1 KNOWLEDGE_OS_GATE_HASH/SALT
  variables). Set it with `python -m knowledge_os set-password`, which stores
  the hash in data/.brainbox/auth.json (preferred over env variables).
- Sessions: random 256-bit tokens; only their SHA-256 is stored. Logout and
  "sign out everywhere" really revoke them.
- Rate limiting is persistent and shared by the web login and the OAuth consent
  page, per client IP and globally.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time

from . import db
from .config import get_settings
from .util import KOSError, now, record_event, sha256

PBKDF2_ITERATIONS = 210_000
SESSION_COOKIE = "kos_session"


class TooManyAttempts(KOSError):
    status = 429


def _stored_credentials() -> tuple[str, str, int]:
    s = get_settings()
    if s.auth_file.is_file():
        try:
            data = json.loads(s.auth_file.read_text("utf-8"))
            return data["hash"], data["salt"], int(data.get("iterations", PBKDF2_ITERATIONS))
        except (ValueError, KeyError):
            pass
    return s.gate_hash, s.gate_salt, PBKDF2_ITERATIONS


def password_configured() -> bool:
    h, salt, _ = _stored_credentials()
    return bool(h and salt)


def _derive(password: str, salt_hex: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), iterations).hex()


def password_matches(password: str) -> bool:
    h, salt, iterations = _stored_credentials()
    if not (h and salt):
        return False
    try:
        return hmac.compare_digest(_derive(password, salt, iterations), h)
    except ValueError:
        return False


def set_password(password: str) -> None:
    if len(password) < 6:
        raise KOSError("Use at least 6 characters (a passphrase is better)")
    salt = secrets.token_hex(16)
    data = {"hash": _derive(password, salt, PBKDF2_ITERATIONS), "salt": salt, "iterations": PBKDF2_ITERATIONS,
            "updated_at": now()}
    path = get_settings().auth_file
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), "utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    revoke_all_sessions()


# --------------------------------------------------------------------------- rate limiting

def _check_and_count(conn, key: str, max_failures: int, window: float, lock: float, record: bool) -> None:
    t = time.time()
    row = conn.execute("SELECT * FROM auth_failures WHERE key=?", (key,)).fetchone()
    if row and row["locked_until"] > t:
        mins = int((row["locked_until"] - t) // 60) + 1
        raise TooManyAttempts(f"Too many failed attempts. Try again in {mins} minute{'s' if mins != 1 else ''}.")
    if not record:
        return
    if not row or t - row["window_start"] > window:
        conn.execute("INSERT OR REPLACE INTO auth_failures(key,count,window_start,locked_until) VALUES(?,?,?,0)",
                     (key, 1, t))
        count = 1
    else:
        count = row["count"] + 1
        conn.execute("UPDATE auth_failures SET count=? WHERE key=?", (count, key))
    if count >= max_failures:
        conn.execute("UPDATE auth_failures SET locked_until=? WHERE key=?", (t + lock, key))


def verify_password_rate_limited(password: str, ip: str, purpose: str = "login") -> None:
    """Raise unless the password is right. Counts failures per IP and globally."""
    s = get_settings()
    lock = s.login_lock_minutes * 60
    with db.connect() as conn:
        _check_and_count(conn, f"ip:{ip}", s.login_max_failures, lock, lock, record=False)
        _check_and_count(conn, "global", s.login_global_max_failures, 3600, lock, record=False)
    if not password_configured():
        raise KOSError("No password is set. Run `python -m knowledge_os set-password` on the server.", status=503)
    if password_matches(password):
        with db.connect() as conn:
            conn.execute("DELETE FROM auth_failures WHERE key=?", (f"ip:{ip}",))
        return
    with db.connect() as conn:
        _check_and_count(conn, f"ip:{ip}", s.login_max_failures, lock, lock, record=True)
        _check_and_count(conn, "global", s.login_global_max_failures, 3600, lock, record=True)
        record_event(conn, f"auth.{purpose}", "failed", {"ip": ip}, actor="anonymous")
    raise KOSError("Incorrect password", status=401)


# --------------------------------------------------------------------------- sessions

def create_session(ip: str, user_agent: str) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    max_age = get_settings().session_days * 86400
    with db.connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        conn.execute("INSERT INTO sessions(token_hash,created_at,expires_at,last_seen,ip,user_agent) VALUES(?,?,?,?,?,?)",
                     (sha256(token), now(), time.time() + max_age, now(), ip, (user_agent or "")[:200]))
        record_event(conn, "auth.login", details={"ip": ip}, actor="web")
    return token, max_age


def validate_session(token: str | None) -> bool:
    if not token:
        return False
    h = sha256(token)
    with db.connect() as conn:
        row = conn.execute("SELECT expires_at, last_seen FROM sessions WHERE token_hash=?", (h,)).fetchone()
        if not row or row["expires_at"] < time.time():
            return False
        # Touch at most once a minute to avoid write amplification.
        if not row["last_seen"] or row["last_seen"][:16] != now()[:16]:
            conn.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now(), h))
    return True


def revoke_session(token: str | None) -> None:
    if token:
        with db.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (sha256(token),))


def revoke_all_sessions() -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM sessions")


def list_sessions(current: str | None = None) -> list[dict]:
    cur = sha256(current) if current else None
    with db.connect() as conn:
        rows = conn.execute("SELECT token_hash, created_at, last_seen, ip, user_agent, expires_at FROM sessions "
                            "WHERE expires_at > ? ORDER BY last_seen DESC", (time.time(),)).fetchall()
    return [{"id": r["token_hash"][:12], "created_at": r["created_at"], "last_seen": r["last_seen"], "ip": r["ip"],
             "user_agent": r["user_agent"], "current": r["token_hash"] == cur} for r in rows]
