# Personal AI Knowledge OS

A private, single-user, local-first AI-native knowledge management system. Combines a Markdown vault, SQLite-backed semantic search, a knowledge graph, persistent AI memory, and an MCP server for agentic access — all behind a clean web UI.

> Inspired by Obsidian, built significantly more AI-native.

---

## Features

- **Markdown Vault** — Portable, readable, and editor-independent. Notes, projects, resources, and skills stored as plain `.md` files with YAML frontmatter.
- **Full-Text Search** — SQLite FTS5 for instant lexical search across all notes.
- **Knowledge Graph** — Wiki links (`[[Note Title]]`) and tags (`#tag`) create a traversable graph with backlinks and auto-detected relations.
- **Semantic Search (RAG)** — Optional vector embeddings via OpenAI for hybrid FTS + semantic retrieval, with token-budgeted context assembly.
- **Persistent AI Memory** — Structured memory entries that persist across sessions, enabling the AI to recall facts, preferences, and decisions.
- **Agent Tool Access** — MCP server (Model Context Protocol) exposes vault operations as typed tools: search, read, create, edit, link, and summarize.
- **Provider-Neutral AI Gateway** — Chat and embedding operations abstracted behind a provider interface. Ships with OpenAI; trivial to add OpenRouter, Ollama, Anthropic, or Gemini.
- **Password Gate** — HMAC‑based single‑password authentication with rate limiting and session cookies.
- **OAuth 2.1 for MCP** — Full OAuth authorization flow for MCP clients, with configurable read/write/full scopes.
- **Local First** — No SaaS, no cloud dependency. The vault is the source of truth. Everything else (indexes, embeddings, graph) is a rebuildable derivative.

---

## Architecture

```
Browser
  │ HTTP/JSON
  ▼
Web UI + API (modular monolith)
  ├── Vault service ── Markdown files, attachments, backups
  ├── Parser/indexer ── metadata, tags, wiki links, chunks
  ├── SQLite store ── FTS5, graph edges, memories, usage
  ├── Retrieval ── FTS5 + optional vector + graph expansion
  ├── AI gateway ── provider-neutral chat/embed interface
  └── Agent tools ── validated note, memory, search and graph ops

MCP Server (port 8796)
  └── OAuth 2.1 protected agent access to above tools
```

### Data layout

```
data/
  vault/
    Notes/        ← Daily notes, reference docs
    Projects/     ← Long-running project notes
    Resources/    ← External references, links
    Skills/       ← Reusable procedures for agents
    Agents/       ← Agent profiles and logs
    Inbox/        ← Quick captures
    Attachments/  ← Uploaded files
  .brainbox/
    knowledge.db  ← SQLite: FTS5, graph, memories, embeddings
  attachments/    ← Uploaded file storage
```

---

## Quick Start

### Prerequisites
- Python 3.11+
- OpenAI API key (or another provider)

### Install

```bash
git clone https://github.com/hamzaibnzakir/Knowledgeos.git
cd Knowledgeos
python3 -m venv .venv
source .venv/bin/activate
pip install -r knowledge_os_requirements.txt
pip install mcp
```

### Configure

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | Your OpenAI API key |
| `OPENAI_MODEL` | No | Model name (default: `gpt-4o-mini`) |
| `MCP_AUTH_TOKEN` | MCP only | Token MCP clients use to authenticate |
| `MCP_PUBLIC_BASE` | MCP only | Public URL of the MCP server (e.g. `https://mcp.example.com`) |
| `KNOWLEDGE_OS_GATE_HASH` | No | SHA‑256 hex of your gate password + salt |
| `KNOWLEDGE_OS_GATE_SALT` | No | Random salt for gate password |
| `KNOWLEDGE_OS_GATE_SECRET` | No | HMAC secret for session cookies |
| `KNOWLEDGE_OS_ROOT` | No | Override the project root path |

> Generate the gate hash: `echo -n "${SALT}${PASSWORD}" | sha256sum`

### Run

**Web app** (port 8786):
```bash
uvicorn knowledge_os_app:app --host 0.0.0.0 --port 8786
```

**MCP server** (port 8796):
```bash
uvicorn mcp_server:app --host 0.0.0.0 --port 8796
```

### systemd (production)

```ini
# /etc/systemd/system/knowledge-os-web.service
[Unit]
Description=Knowledge OS Web App
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/knowledge-os
Environment=KNOWLEDGE_OS_ROOT=/opt/knowledge-os
EnvironmentFile=/opt/knowledge-os/.env
ExecStart=/opt/knowledge-os/.venv/bin/uvicorn knowledge_os_app:app --host 0.0.0.0 --port 8786
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
```

---

## API

### Web routes (port 8786)

| Route | Method | Auth | Description |
|---|---|---|---|
| `/` | GET | Cookie | Main web UI |
| `/gate` | GET | None | Password gate page |
| `/api/auth/verify` | POST | None | Submit gate password |
| `/list` | GET | Cookie | List vault directory |
| `/read` | GET | Cookie | Read a note by path |
| `/api/notes` | GET | Cookie | List notes with metadata |
| `/api/notes/<id>` | GET | Cookie | Get note content |
| `/api/search` | GET | Cookie | FTS search notes |
| `/api/memory` | GET/POST | Cookie | Read/write AI memory |

### MCP routes (port 8796)

| Route | Method | Auth | Description |
|---|---|---|---|
| `/health` | GET | None | Health check |
| `/mcp` | POST | Bearer | MCP tool execution |
| `/.well-known/oauth-*` | GET | None | OAuth 2.1 metadata |
| `/oauth/authorize` | GET/POST | None | OAuth authorization |
| `/oauth/token` | POST | Client | OAuth token exchange |

---

## MCP Tools

When connected as an MCP client, the server exposes:

| Tool | Description |
|---|---|
| `search_knowledge` | Full-text search across notes, headers, content |
| `read_note` | Read a note by its vault path |
| `create_note` | Create a new note (path + content) |
| `update_note` | Update an existing note |
| `delete_note` | Delete a note (backup created first) |
| `list_skills` | List notes in the Skills directory |
| `read_memory` | Read stored AI memories |
| `write_memory` | Write a persistent AI memory |
| `find_related` | Find notes linked by wiki links or tags |
| `summarize_context` | Assemble and summarize context around a topic |

All write operations require `knowledge:write` or `knowledge:full` OAuth scope.

---

## Project Structure

```
knowledge_os_app.py      # FastAPI monolith — vault, search, AI, UI
mcp_server.py            # MCP + OAuth 2.1 server
frontend.html            # Web UI (single-file HTML+JS)
knowledge_os_requirements.txt  # Python dependencies
knowledge_os.env.example # Environment config template
ARCHITECTURE.md          # Detailed system architecture
BRIEF.md                 # Project brief and design rationale
RESEARCH.md              # Technical research log
```

---

## Roadmap

- [ ] Local embedding models (Ollama, sentence-transformers)
- [ ] File watcher for live re-indexing
- [ ] Vector search tunable per query
- [ ] Graph visualization in the UI
- [ ] Voice interface (future phase)
- [ ] Multi-provider AI gateway (OpenRouter, Anthropic, Gemini)

---

## License

MIT