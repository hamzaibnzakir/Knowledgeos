# Architecture

## Principles

1. **The Markdown vault is the source of truth.** Delete the database and nothing is lost: the index, graph and embeddings rebuild from the files. Memories, chats, history and credentials live in the database and are included in backups.
2. **Deterministic first.** Parsing, links, tags, graph, chunking, keyword search and indexing never call a model. Embeddings run locally by default. A model is called only for chat, tool-using actions and memory extraction.
3. **One tool surface.** The built-in assistant, MCP agents and a future voice interface all call the same typed tools in `tools.py`. There is no shell or raw file access for any agent.
4. **Every write is safe and attributable.** Paths are validated, writes are atomic, the previous version is kept, deletions go to a restorable trash, optimistic concurrency stops lost updates, and each change is logged with its actor.
5. **Provider-neutral.** Only `ai.py` knows how to talk to a model provider.

## Runtime

```
                 ┌──────────────────────── one Python process ─────────────────────────┐
 Browser ──HTTPS─┤ :8786  FastAPI (web.py)  ── sessions, CSRF header, security headers  │
                 │          │                                                          │
 Agents ───HTTPS─┤ :8796  MCP SDK (mcp_app.py) ── OAuth 2.1, agent tokens, scopes,       │
                 │          │                     rate limits, attribution            │
                 │          ▼                                                          │
                 │   tools.py ── agent.py ── ai.py (provider gateway, usage metering)   │
                 │      │                                                              │
                 │   vault.py ── indexer.py ── retrieval.py ── memory.py               │
                 │      │            │             │                                   │
                 │  Markdown files   SQLite (WAL): index, FTS5, links, chunks,          │
                 │  data/vault/      vectors, memories, chats, history, auth, audit     │
                 │                                                                     │
                 │  background threads: reconcile (external edits) · embeddings · backups│
                 └─────────────────────────────────────────────────────────────────────┘
```

Running the web app and MCP server in one process (two uvicorn servers on one event loop) means one embedding model in memory, one vault write lock and no cross-process races. Request handlers are synchronous and run in the thread pool; SQLite runs in WAL mode with a busy timeout.

## Data layout

```
data/
  vault/                 your notes (Notes/, Projects/, Resources/, Skills/, Agents/, Inbox/, Attachments/ …)
  attachments/           files uploaded in chat (staged until you file them)
  backups/               kos-backup-YYYYMMDD-HHMMSS.tar.gz (vault + DB snapshot)
  .brainbox/
    knowledge.db         SQLite
    auth.json            password hash (PBKDF2-SHA256)
    trash/               deleted notes, restorable
    models/              embedding model cache (outside Docker)
```

Hidden paths (starting with `.`) are never reachable through the API or tools.

## Database

| Kind | Tables | Rebuildable |
|---|---|---|
| Index | `notes`, `notes_fts`, `name_keys`, `links`, `note_tags`, `chunks`, `chunks_fts`, `embeddings` | yes, from the vault |
| Your data | `memories`, `chats`, `messages`, `attachments`, `favorites`, `note_versions`, `trash` | no — backed up |
| Security | `sessions`, `auth_failures`, `oauth_clients`, `oauth_codes`, `oauth_pending`, `tokens` (all hashed) | no |
| Operations | `action_events` (audit), `usage`, `agents`, `pending_actions`, `meta` | no |

Schema upgrades are idempotent and run on start. A v1 database upgrades in place: derived tables are rebuilt, memories/chats/audit/agents are kept.

## Indexing

`indexer.index_path` parses a note (YAML front matter, aliases, inline and front-matter tags, wiki/embed/Markdown links outside code, headings) and updates the note row, FTS rows, name keys (path, file name, title, aliases), tags, links and chunks in one transaction. Unchanged files are skipped by content hash.

Links are resolved through `name_keys`, best match first (exact path, file name, title, alias). Creating or renaming a note resolves links that were waiting for that name; deleting one un-resolves them. Moving a note rewrites `[[links]]` in the notes that pointed at it.

A reconcile loop picks up edits made outside the app (Obsidian, git, Syncthing) by comparing size and mtime.

## Search and retrieval

- **Keyword:** FTS5 with Porter stemming, BM25 weighted title > aliases > tags > body.
- **Semantic:** chunk vectors (`bge-small-en-v1.5` via fastembed/ONNX, or OpenAI) held in an in-memory matrix that refreshes when the index changes. Vectors are keyed by chunk content hash, so unchanged text is never re-embedded. Weak matches are dropped by a model-specific similarity floor.
- **Fusion:** Reciprocal Rank Fusion of keyword and semantic rankings.
- **RAG context (`build_context`):** relevant memories, relevant excerpts of chat attachments, then hybrid-retrieved chunks plus one hop of graph neighbours, deduplicated and cut to a token budget (default 5,000), each with a citation number. This package — never the whole vault — is what the model sees.
- **Related notes:** shared links, backlinks, rarity-weighted shared tags, second-degree links and centroid similarity.

## Assistant

`agent.send_message` stores the user turn, builds the context package, then loops (max 6 steps): model → tool calls → `tools.call` → results back to the model. Destructive tools (`delete_note`, `forget_memory`) become pending actions that the UI shows with Approve / Reject. Every answer stores its sources, actions, context size and token usage. Without an API key the assistant returns the most relevant passages instead.

## Security

- **Web:** password (PBKDF2-SHA256, 210k iterations) → random session token in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` over HTTPS); only the hash is stored, so logout and "sign out everywhere" truly revoke. Login is rate-limited per IP and globally, persistently. Mutating API calls require an `X-KOS` header (CSRF defence). Strict CSP; Markdown is rendered with raw HTML disabled and sanitised with DOMPurify; search snippets use control-character markers, not HTML.
- **MCP:** official MCP SDK auth. OAuth codes are single-use and expire in 5 minutes; refresh tokens rotate and replaying an old one revokes the whole token family; consent requires the owner password (same rate limiter) and lets you grant read-only. Tokens of every kind are stored hashed and can be revoked per agent. DNS-rebinding protection limits allowed Host headers.
- **Files:** path normalisation rejects `..`, hidden segments, control and reserved characters, and symlink escapes. Uploads are size-limited and streamed. Link import blocks private, loopback and link-local addresses (SSRF) and re-checks every redirect.
- **Secrets** are never returned by the API (the UI shows whether something is configured, not its value).

## Future voice

A voice interface (SIP → Asterisk/FreeSWITCH → LiveKit Agents/Pipecat → STT → `agent` → TTS) plugs into the same `tools.call` and `ai.py` gateway with its own actor name. No core changes are needed.
