"""SQLite access and schema migrations.

The database holds derived indexes (rebuildable from the vault) plus
operational state that is not note content: memories, chats, auth sessions,
OAuth clients/tokens, audit events, usage and note version history.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from .config import get_settings

SCHEMA_VERSION = 3

_init_lock = threading.Lock()
_initialized_for: set[str] = set()


def _connect() -> sqlite3.Connection:
    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Autocommit connection. Use `transaction(conn)` for multi-statement atomicity."""
    ensure_schema()
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


DERIVED_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes(
    path TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    folder TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    mtime REAL NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT 0,
    word_count INTEGER NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '[]',
    aliases TEXT NOT NULL DEFAULT '[]',
    properties TEXT NOT NULL DEFAULT '{}',
    headings TEXT NOT NULL DEFAULT '[]',
    excerpt TEXT NOT NULL DEFAULT '',
    created_at TEXT,
    updated_at TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    path UNINDEXED, title, aliases, tags, body, tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS name_keys(
    key TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY(key, path)
);
CREATE INDEX IF NOT EXISTS idx_name_keys_path ON name_keys(path);
CREATE TABLE IF NOT EXISTS links(
    source TEXT NOT NULL,
    target_key TEXT NOT NULL,
    target_raw TEXT NOT NULL,
    target_path TEXT,
    kind TEXT NOT NULL DEFAULT 'wiki',
    PRIMARY KEY(source, target_key, kind)
);
CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_path);
CREATE INDEX IF NOT EXISTS idx_links_key ON links(target_key);
CREATE TABLE IF NOT EXISTS note_tags(path TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY(path, tag));
CREATE INDEX IF NOT EXISTS idx_note_tags_tag ON note_tags(tag);
CREATE TABLE IF NOT EXISTS chunks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    ord INTEGER NOT NULL,
    heading TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    hash TEXT NOT NULL,
    tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, heading, title, chunk_id UNINDEXED, path UNINDEXED,
    tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS facts(
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    subject TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    by_agent TEXT NOT NULL DEFAULT '',
    on_date TEXT NOT NULL DEFAULT '',
    src TEXT NOT NULL DEFAULT '',
    external INTEGER NOT NULL DEFAULT 0,
    review TEXT NOT NULL DEFAULT 'ok',
    flag TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'current',
    until TEXT NOT NULL DEFAULT '',
    replaced_by TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    line_no INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_facts_path ON facts(path);
CREATE INDEX IF NOT EXISTS idx_facts_review ON facts(review, status);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    text, subject, id UNINDEXED, path UNINDEXED, tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS embeddings(
    chunk_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY(chunk_hash, model)
);
"""

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS favorites(path TEXT PRIMARY KEY, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS note_versions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    content BLOB NOT NULL,
    content_hash TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_path ON note_versions(path, id DESC);
CREATE TABLE IF NOT EXISTS trash(
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    stored_as TEXT NOT NULL,
    actor TEXT NOT NULL,
    deleted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories(
    id INTEGER PRIMARY KEY,
    content TEXT,
    category TEXT,
    confidence REAL,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS usage(
    id INTEGER PRIMARY KEY,
    provider TEXT, model TEXT, operation TEXT,
    input_tokens INTEGER, output_tokens INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS action_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT, action TEXT NOT NULL, status TEXT NOT NULL,
    details TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, role TEXT, content TEXT,
    attachment_name TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS attachments(
    id TEXT PRIMARY KEY, chat_id TEXT, filename TEXT, mime_type TEXT, path TEXT,
    extracted_text TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS agents(
    id TEXT PRIMARY KEY, client_id TEXT UNIQUE, name TEXT, provider TEXT, model TEXT,
    first_connected TEXT, last_seen TEXT, actions INTEGER DEFAULT 0,
    retrievals INTEGER DEFAULT 0, skills_used INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pending_actions(
    id TEXT PRIMARY KEY, chat_id TEXT, tool TEXT NOT NULL, args TEXT NOT NULL,
    summary TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS proposals(
    id TEXT PRIMARY KEY, path TEXT NOT NULL, op TEXT NOT NULL, fact_id TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '', agent TEXT NOT NULL, src TEXT NOT NULL DEFAULT '', external INTEGER NOT NULL DEFAULT 0,
    flag TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions(
    token_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at REAL NOT NULL,
    last_seen TEXT, ip TEXT, user_agent TEXT
);
CREATE TABLE IF NOT EXISTS auth_failures(
    key TEXT PRIMARY KEY, count INTEGER NOT NULL, window_start REAL NOT NULL, locked_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS oauth_clients(
    client_id TEXT PRIMARY KEY, info TEXT NOT NULL, label TEXT, created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS oauth_codes(
    code_hash TEXT PRIMARY KEY, data TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_pending(
    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, data TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens(
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                 -- access | refresh | pat
    client_id TEXT NOT NULL,
    scopes TEXT NOT NULL,
    label TEXT,
    family TEXT,
    resource TEXT,
    expires_at REAL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_family ON tokens(family);
CREATE INDEX IF NOT EXISTS idx_tokens_client ON tokens(client_id);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    # Every step is idempotent, so an interrupted migration simply re-runs.
    if version < 2:
        # v0 = first prototype. Derived tables changed shape: drop and rebuild them
        # (they are regenerated from the Markdown vault on startup).
        notes_cols = _columns(conn, "notes")
        if notes_cols and "content_hash" not in notes_cols:
            for t in ("notes", "notes_fts", "edges"):
                conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.executescript(STATE_SCHEMA)
    conn.executescript(DERIVED_SCHEMA)

    # Upgrade operational tables in place (keeps existing memories, chats, audit).
    _add_column(conn, "memories", "source", "TEXT NOT NULL DEFAULT 'user'")
    _add_column(conn, "memories", "status", "TEXT NOT NULL DEFAULT 'active'")
    _add_column(conn, "memories", "norm", "TEXT")
    _add_column(conn, "memories", "pinned", "INTEGER NOT NULL DEFAULT 0")
    conn.execute("UPDATE memories SET norm=lower(trim(content)) WHERE norm IS NULL")
    conn.execute("UPDATE memories SET category='general' WHERE category IS NULL OR category=''")
    conn.execute("UPDATE memories SET confidence=1.0 WHERE confidence IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_norm ON memories(norm)")

    _add_column(conn, "usage", "cost_usd", "REAL")
    _add_column(conn, "usage", "latency_ms", "INTEGER")
    _add_column(conn, "usage", "actor", "TEXT")

    _add_column(conn, "action_events", "actor", "TEXT NOT NULL DEFAULT 'system'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_id ON action_events(id DESC)")

    _add_column(conn, "messages", "meta", "TEXT NOT NULL DEFAULT '{}'")
    _add_column(conn, "attachments", "size", "INTEGER")
    _add_column(conn, "attachments", "sha256", "TEXT")
    _add_column(conn, "attachments", "note_path", "TEXT")

    _add_column(conn, "agents", "label", "TEXT")
    _add_column(conn, "agents", "status", "TEXT NOT NULL DEFAULT 'active'")
    _add_column(conn, "agents", "writes", "INTEGER NOT NULL DEFAULT 0")

    if 0 < version < 3 and "path" in _columns(conn, "notes"):
        # v3 indexes memory facts from Markdown: force every note to be re-read once.
        conn.execute("DELETE FROM notes")
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def ensure_schema() -> None:
    key = str(get_settings().db_path)
    if key in _initialized_for:
        return
    with _init_lock:
        if key in _initialized_for:
            return
        conn = _connect()
        try:
            _migrate(conn)
        finally:
            conn.close()
        _initialized_for.add(key)


def reset_schema_cache() -> None:
    _initialized_for.clear()


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
