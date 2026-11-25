"""A database and vault from the first prototype must upgrade in place without losing data."""
import sqlite3

from knowledge_os import auth, db, indexer, memory


LEGACY_SCHEMA = """
CREATE TABLE notes(path TEXT PRIMARY KEY, title TEXT, content TEXT, updated_at TEXT, tags TEXT, aliases TEXT, properties TEXT);
CREATE VIRTUAL TABLE notes_fts USING fts5(path UNINDEXED, title, content);
CREATE TABLE edges(source TEXT, target TEXT, PRIMARY KEY(source,target));
CREATE TABLE memories(id INTEGER PRIMARY KEY, content TEXT, category TEXT, confidence REAL, created_at TEXT, updated_at TEXT);
CREATE TABLE usage(id INTEGER PRIMARY KEY, provider TEXT, model TEXT, operation TEXT, input_tokens INTEGER, output_tokens INTEGER, created_at TEXT);
CREATE TABLE action_events(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, action TEXT NOT NULL, status TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE chats(id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, role TEXT, content TEXT, attachment_name TEXT, created_at TEXT);
CREATE TABLE attachments(id TEXT PRIMARY KEY, chat_id TEXT, filename TEXT, mime_type TEXT, path TEXT, extracted_text TEXT, created_at TEXT);
CREATE TABLE agents(id TEXT PRIMARY KEY, client_id TEXT UNIQUE, name TEXT, provider TEXT, model TEXT, first_connected TEXT, last_seen TEXT, actions INTEGER DEFAULT 0, retrievals INTEGER DEFAULT 0, skills_used INTEGER DEFAULT 0);
INSERT INTO memories VALUES(1,'Runs EcomLab','project',0.9,'2026-08-01','2026-08-01');
INSERT INTO chats VALUES('c1','Old chat','2026-08-01','2026-08-01');
INSERT INTO messages(chat_id,role,content,created_at) VALUES('c1','user','hi','2026-08-01');
INSERT INTO action_events(chat_id,action,status,details,created_at) VALUES(NULL,'note.create','completed','{}','2026-08-01');
INSERT INTO agents VALUES('hermes','knowledge-os-write-client','Hermes','MCP client','unknown','2026-08-01','2026-08-01',5,2,1);
"""


def test_upgrade_legacy_db(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / ".brainbox").mkdir(parents=True)
    (data / "vault" / "Notes").mkdir(parents=True)
    (data / "vault" / "Notes" / "Kept.md").write_text("# Kept\nlinks [[Other]]")
    conn = sqlite3.connect(data / ".brainbox" / "knowledge.db")
    conn.executescript(LEGACY_SCHEMA)
    conn.close()

    # v1 gate credentials keep working
    import hashlib
    salt = "00" * 16
    legacy_hash = hashlib.pbkdf2_hmac("sha256", b"123456", bytes.fromhex(salt), 210000).hex()
    monkeypatch.setenv("KOS_DATA_DIR", str(data))
    monkeypatch.setenv("KOS_EMBEDDINGS", "none")
    monkeypatch.setenv("KNOWLEDGE_OS_GATE_HASH", legacy_hash)
    monkeypatch.setenv("KNOWLEDGE_OS_GATE_SALT", salt)
    from knowledge_os import config
    config.reset_settings()
    db.reset_schema_cache()
    db.ensure_schema()
    indexer.reconcile()

    assert memory.list_memories()[0]["content"] == "Runs EcomLab"
    assert indexer.stats()["notes"] == 1
    assert indexer.unresolved_links()[0]["target"] == "Other"
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
        assert c.execute("SELECT actions FROM agents WHERE client_id='knowledge-os-write-client'").fetchone()[0] == 5
    assert auth.password_matches("123456")
    config.reset_settings()
    db.reset_schema_cache()
