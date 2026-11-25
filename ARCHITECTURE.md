# Personal AI Knowledge OS — Architecture

## Purpose and boundaries

This service is a private, single-user, local-first knowledge application running independently on the VPS. It owns a portable Markdown vault and treats databases and vector indexes as rebuildable derivatives. It does not modify or depend on `ecomlab-backend`, does not implement SaaS tenancy, billing, public registration or voice calling, and should initially be reachable only through a dedicated private port.

## System overview

```text
Browser
  │ HTTP/JSON
  ▼
Web UI + API (modular monolith)
  ├── Vault service ── Markdown files, attachments, backups
  ├── Parser/indexer ── metadata, tags, wiki links, chunks
  ├── SQLite store ── FTS5, graph edges, memories, usage
  ├── Retrieval ── FTS5 + optional local vectors + graph expansion
  ├── AI gateway ── provider-neutral chat/embed interface
  └── Agent tools ── validated note, memory, search and graph operations
```

The UI calls the API. The API is the only component allowed to mutate the vault. The agent receives narrowly scoped application tools rather than shell or unrestricted filesystem access. The AI gateway is the only component that knows provider-specific protocol details.

## Data ownership and filesystem layout

```text
project/
  app/
    api/          HTTP routes and request schemas
    vault/        safe Markdown and attachment operations
    indexer/      parsing, chunking, FTS and graph updates
    retrieval/    lexical/vector/hybrid retrieval
    ai/           provider interfaces and implementations
    agent/        tool definitions, confirmation policy and orchestration
    db/           SQLite schema, migrations and repositories
  frontend/       responsive single-user UI
  data/
    vault/        Notes, Projects, Resources, Attachments
    .brainbox/    SQLite, indexes, embeddings, configuration, backups
  tests/
  RESEARCH.md
  ARCHITECTURE.md
  README.md
  DEVELOPMENT.md
  .env.example
```

A vault-relative path is normalized and checked against the vault root before every file operation. Hidden operational data under `.brainbox` is not exposed as ordinary note content. Markdown, attachments and front matter remain readable independently of the service.

## Core modules

### Vault service

Provides create, read, update, delete, rename, move, attachment and backup operations. It enforces path traversal protection, filename validation, atomic writes, size limits and safe attachment handling. Delete and destructive agent operations use a confirmation token and create a backup or trash record before removal.

### Parser and indexer

Parses Markdown front matter, headings, tags, wiki links and ordinary internal links deterministically. It maintains note metadata, outgoing edges and backlinks. A file watcher schedules incremental re-indexing, while a full rebuild command can reconstruct all derivatives from the vault.

### SQLite store

SQLite stores note metadata, FTS5 content, graph edges, memories, conversation summaries, embedding references and usage metrics. It is not the source of truth for note content. Transactions keep metadata and index updates consistent, and periodic database snapshots are stored separately from the vault backup.

### Retrieval

The retrieval interface accepts a query and optional filters. The first path performs FTS5 search and metadata filtering. If local embeddings are configured, it also performs vector search and fuses lexical and semantic scores. It can expand context through backlinks, outgoing links, tags and selected memories. The final context builder applies token and character budgets, deduplicates excerpts and returns source identifiers for citations in the UI.

### AI gateway

The gateway exposes provider-neutral operations such as `chat`, `embed`, `supports`, and `estimate_usage`. Configuration selects provider, model, base URL, temperature and context limits. `OpenAIProvider` is the initial implementation and reads `OPENAI_API_KEY` only from the process environment. Future adapters may target OpenRouter, Ollama, Anthropic, Gemini or other OpenAI-compatible endpoints. No provider-specific code is allowed in vault, retrieval or UI modules.

### Agent and controlled tools

The agent can search notes, read notes, create and edit notes, manage intentional memories, create links, find related notes, summarize retrieved content and assemble project context. Each tool uses typed arguments and authorization checks. The agent cannot execute shell commands, install packages, read arbitrary files, access secrets or mutate database tables directly. Destructive actions return a confirmation request that must be approved by the user through the UI.

### Web interface

The UI provides a sidebar for vault navigation, folders, tags, projects, search, graph, AI, memory and settings. The main workspace supports Markdown editing, preview, backlinks, related notes and chat. The interface is responsive and supports light/dark themes if practical, but avoids decorative work until core functionality is reliable.

## API shape

Initial endpoints are grouped under `/api`:

| Area | Examples |
|---|---|
| Notes | `GET /notes`, `GET /notes/{path}`, `POST /notes`, `PUT /notes/{path}`, `DELETE /notes/{path}`, `POST /notes/{path}/move` |
| Search | `GET /search?q=...`, `GET /search/semantic?q=...` |
| Graph | `GET /graph`, `GET /graph/local/{path}`, `GET /notes/{path}/backlinks` |
| Memories | `GET /memories`, `POST /memories`, `PATCH /memories/{id}`, `DELETE /memories/{id}` |
| Agent | `POST /chat`, `POST /agent/confirm` |
| Operations | `POST /reindex`, `POST /backups`, `GET /usage` |

All responses include stable identifiers and useful error messages without leaking filesystem paths or secrets.

## Persistence and recovery

On startup, the service verifies the vault root, opens SQLite, applies migrations and reconciles changed Markdown files. A scheduled or manually triggered backup atomically copies the vault and SQLite database to timestamped backup directories. Recovery can restore Markdown first, then rebuild indexes. A restart test must prove that notes and memories survive.

## Deployment

The service uses a dedicated unoccupied port and binds according to the private-access requirement. Existing Nginx, PM2, Step CA and other services are not modified unless the user explicitly requests integration. No database port is public. The application should be restartable through a documented systemd unit or Docker Compose configuration after the implementation phase determines which is safest for this VPS.

## Security controls

Secrets are environment-only and excluded from logs and version control. All note paths are validated against the vault root. User-supplied Markdown is stored as text and rendered with sanitization. Attachments have size/type limits. Agent tool arguments use strict schemas. Rate and token budgets constrain AI calls. Logs record operation type, duration, success/failure and usage metadata, not secret values or unnecessary note contents.

## Testing strategy

Tests cover note CRUD, rename/move, path traversal rejection, Markdown parsing, wiki links, backlinks, FTS search, graph generation, memory persistence, backup/recovery, provider configuration, context minimization and agent confirmation gates. End-to-end acceptance tests create and edit notes, restart the service, verify persistence, create links and backlinks, search, create memory, retrieve context and rebuild indexes.

## Future voice compatibility

The agent tool interface and AI gateway are transport-neutral. A later voice adapter can map SIP/Asterisk or FreeSWITCH through LiveKit Agents or Pipecat, speech-to-text and text-to-speech into the same agent service. Voice will be a new interface, not a separate knowledge brain.

