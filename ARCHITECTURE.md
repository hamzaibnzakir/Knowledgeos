# Architecture

## Principles

1. **The Markdown vault is the source of truth — including memory.** Notes and memory facts are files. Delete the database and nothing you wrote is lost: the index, graph, facts table and embeddings rebuild from the files. Version history, credentials and the audit log live in the database and are included in backups.
2. **No AI inside.** Knowledge OS never calls a language model. Parsing, links, graph, search and indexing are deterministic; the only model is a small local embedding model for search by meaning. The intelligence lives in the agents that connect over MCP.
3. **Memory is a trust problem, not a storage problem.** Research on agent memory (Letta, Mem0, Zep, 2026 poisoning and forgetting studies) shows simple files with good tools work as well as specialised stores; what breaks is stale facts and poisoned writes. So: every fact has an id and provenance, facts are superseded rather than overwritten, the always-loaded files need the owner's approval, and instruction-like text is quarantined.
4. **One tool surface.** MCP agents and the web API call the same typed tools in `tools.py`. There is no shell or raw file access for any agent.
5. **Every write is safe and attributable.** Paths are validated, writes are atomic, the previous version is kept, deletions go to a restorable trash, optimistic concurrency stops lost updates, and each change is logged with its actor.

## Runtime

```
                 ┌──────────────────────── one Python process ─────────────────────────┐
 Browser ──HTTPS─┤ :8786  FastAPI (web.py)  ── sessions, CSRF header, security headers  │
                 │          │                                                          │
 Agents ───HTTPS─┤ :8796  MCP SDK (mcp_app.py) ── OAuth 2.1, agent tokens, scopes,       │
                 │          │                     rate limits, attribution            │
                 │          ▼                                                          │
                 │   tools.py (brief, recall, remember, revise, forget, log, notes…)   │
                 │      │                                                              │
                 │   vault.py ── indexer.py ── retrieval.py ── memory.py               │
                 │      │            │             │                                   │
                 │  Markdown files   SQLite (WAL): index, FTS5, links, chunks, facts,   │
                 │  data/vault/      vectors, proposals, history, auth, audit           │
                 │                                                                     │
                 │  background threads: reconcile (external edits) · embeddings · backups│
                 └─────────────────────────────────────────────────────────────────────┘
```

Running the web app and MCP server in one process (two uvicorn servers on one event loop) means one embedding model in memory, one vault write lock and no cross-process races. Request handlers are synchronous and run in the thread pool; SQLite runs in WAL mode with a busy timeout.

## Data layout

```
data/
  vault/
    Memory/              Profile.md, Rules.md, Projects/, Clients/, People/, Topics/  (facts)
    Agents/<name>/       AGENT.md (the agent's own profile) and Log/YYYY-MM-DD.md
    Notes/ Projects/ Resources/ Skills/ Inbox/ Archive/ Attachments/ …   your knowledge
  attachments/           staging for imported files
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
| Index | `notes`, `notes_fts`, `name_keys`, `links`, `note_tags`, `chunks`, `chunks_fts`, `facts`, `facts_fts`, `embeddings` | yes, from the vault |
| Your data | `proposals`, `favorites`, `note_versions`, `trash` (v2 `memories`/`chats` kept read-only after migration) | no — backed up |
| Security | `sessions`, `auth_failures`, `oauth_clients`, `oauth_codes`, `oauth_pending`, `tokens` (all hashed) | no |
| Operations | `action_events` (audit), `usage`, `agents`, `pending_actions`, `meta` | no |

Schema upgrades are idempotent and run on start. A v1/v2 database upgrades in place: derived tables are rebuilt, old database memories are written into `Memory/` files (ids `m<old id>`, so legacy tools still find them), and old chats are exported to `Archive/Chats/` as Markdown.

## Indexing

`indexer.index_path` parses a note (YAML front matter, aliases, inline and front-matter tags, wiki/embed/Markdown links outside code, headings) and updates the note row, FTS rows, name keys (path, file name, title, aliases), tags, links and chunks in one transaction. Unchanged files are skipped by content hash.

Links are resolved through `name_keys`, best match first (exact path, file name, title, alias). Creating or renaming a note resolves links that were waiting for that name; deleting one un-resolves them. Moving a note rewrites `[[links]]` in the notes that pointed at it.

A reconcile loop picks up edits made outside the app (Obsidian, git, Syncthing) by comparing size and mtime.

## Search and retrieval

- **Keyword:** FTS5 with Porter stemming, BM25 weighted title > aliases > tags > body.
- **Semantic:** chunk vectors (`bge-small-en-v1.5` via fastembed/ONNX, or OpenAI) held in an in-memory matrix that refreshes when the index changes. Vectors are keyed by chunk content hash, so unchanged text is never re-embedded. Weak matches are dropped by a model-specific similarity floor.
- **Fusion:** Reciprocal Rank Fusion of keyword and semantic rankings.
- **Context package (`retrieve_context`):** relevant memory facts, then hybrid-retrieved note chunks plus one hop of graph neighbours, deduplicated and cut to a token budget, each with a citation number — so an agent never needs to read the whole vault.
- **Related notes:** shared links, backlinks, rarity-weighted shared tags, second-degree links and centroid similarity.

## Memory

**Format.** A fact is a Markdown list item with a block id and Dataview-style inline fields, so it reads and edits fine in Obsidian:

```
- Phase 2 adds a sitemap crawler ^f3k9q2 [by:: Hermes] [on:: 2026-11-27] [src:: Projects/EcomLab.md] [review:: new]
```

Fields: `by`, `on`, `src`, `from:: external`, `review:: new`, `flag:: suspicious`; superseded facts carry `until`, `replaced-by`, `reason` and live under `## History`, struck through. Any other text in a memory file is free prose. The indexer parses facts from every `Memory/` file into `facts` / `facts_fts`, so hand edits are picked up like any note edit.

**Layers.** `Memory/Profile.md` and `Memory/Rules.md` are always in the brief (capped by `KOS_CORE_MEMORY_MAX_CHARS`). Subject files (`Projects/`, `Clients/`, `People/`, `Topics/`) are pulled in when a topic matches. Notes are found by search. Agent logs (`Agents/<name>/Log/`) are history, never in the brief.

**Trust.**

| Who | Profile / Rules | Other subjects |
|---|---|---|
| Owner (web) | written directly | written directly |
| Agent set to *Reviewed* (default) | becomes a **proposal**; nothing changes until approved | written, marked `review:: new` |
| Agent set to *Trusted* | becomes a **proposal** | written without review (listed under "Saved by trusted agents" in Review) |
| Agent set to *Read only*, or paused | refused | refused (paused agents are refused for every tool) |

Trust is set per agent on the Agents page (`agents.trust`, `agents.status='paused'`) and enforced in the MCP server before any tool runs. Text that reads like an instruction ("ignore previous…", "you must always…", "send the key to…") is written with `flag:: suspicious` and excluded from briefs and context until the owner keeps it. Facts from web pages or documents are marked `from:: external` and labelled as such in briefs.

**Updates.** `remember` first looks for an exact or near-duplicate (token overlap, plus embedding similarity when available). Exact duplicates are a no-op; near-duplicates return `needs_decision` with the candidates so the calling agent chooses `replaces=<id>`, `on_similar='add'` or `'skip'` — the add/update/no-op decision Mem0 makes with its own model, made here by the agent that already has the context. `revise` and `forget` supersede instead of deleting. Only the owner can purge a line.

**Brief.** `brief(topic?)` assembles Profile, Rules, the agent's own `AGENT.md`, the matching subject file, related facts, note pointers, a de-duplicated list of the last week's changes and the review count, cut to `KOS_BRIEF_TOKEN_BUDGET` (default 2,000 tokens).

**Tidy-up.** `memory_health` reports near-duplicates, facts older than `KOS_MEMORY_STALE_DAYS`, flagged facts and how full Profile/Rules are. The bundled `Skills/Memory tidy-up.md` tells an agent (e.g. Hermes on a weekly schedule) how to work through it — the "sleep-time" consolidation Letta describes, done by your own agent.

## Security

- **Web:** password (PBKDF2-SHA256, 210k iterations) → random session token in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` over HTTPS); only the hash is stored, so logout and "sign out everywhere" truly revoke. Login is rate-limited per IP and globally, persistently. Mutating API calls require an `X-KOS` header (CSRF defence). Strict CSP; Markdown is rendered with raw HTML disabled and sanitised with DOMPurify; search snippets use control-character markers, not HTML.
- **MCP:** official MCP SDK auth. OAuth codes are single-use and expire in 5 minutes; refresh tokens rotate and replaying an old one revokes the whole token family; consent requires the owner password (same rate limiter) and lets you grant read-only. Tokens of every kind are stored hashed and can be revoked per agent. DNS-rebinding protection limits allowed Host headers.
- **Files:** path normalisation rejects `..`, hidden segments, control and reserved characters, and symlink escapes. Uploads are size-limited and streamed. Link import blocks private, loopback and link-local addresses (SSRF) and re-checks every redirect.
- **Secrets** are never returned by the API (the UI shows whether something is configured, not its value).

## Extending

A new client (a voice agent, a scheduled job, another MCP host) only needs a token: it gets the same tools, its own name in provenance and its own `Agents/<name>/` folder. Nothing in the core changes.
