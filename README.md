# Knowledge OS

A private, single-user, local-first knowledge system: a Markdown vault you own, a knowledge graph, intentional AI memory, hybrid search with local embeddings, a tool-using AI assistant, and an MCP server so your other agents (Hermes, Claude, ChatGPT, scripts) can use the same brain.

> Obsidian-compatible files, built AI-native. Everything except chat works with no API key at all.

## What you get

- **Markdown vault as the source of truth** — plain `.md` files with YAML front matter, `[[wiki links]]`, aliases, `#tags`, embeds. Open the folder in Obsidian any time. The database is only an index plus history.
- **Editor** — CodeMirror with `[[` link autocomplete, autosave, conflict detection (two devices or an agent editing at once never silently overwrite each other), reading view, version history, rename/move that rewrites links, trash with restore.
- **Knowledge graph** — global and local graph (Cytoscape), backlinks, outgoing links, unlinked mentions, missing notes, orphans, related notes (links + shared tags + similar content).
- **Search** — SQLite FTS5 keyword search fused with local semantic search (`bge-small`, runs on the VPS, no API cost). Results by words, by meaning, or both.
- **AI assistant** — answers grounded in a small, token-budgeted context package with citations; actually performs actions (create, edit, link, organise, remember) through validated tools; deletions wait for your approval. Provider-neutral: OpenAI, OpenRouter, Ollama, Gemini or any OpenAI-compatible endpoint.
- **Memory** — short durable facts, kept apart from notes. Nothing is saved automatically: you add them, ask an agent to remember, or review candidates extracted from a chat. Duplicates are detected; forgetting is reversible.
- **MCP server for agents** — the same tools over Streamable HTTP. OAuth 2.1 (dynamic registration, PKCE, rotating refresh tokens, revocation), per-agent tokens issued in the UI, scopes (`knowledge:read`, `knowledge:write`), per-agent rate limits, and every call attributed in the activity log.
- **Governance** — activity log of every change and who made it, token usage and estimated cost per model/operation, automatic backups, vault export.

## Quick start (Docker)

```bash
git clone https://github.com/hamzaibnzakir/Knowledgeos.git && cd Knowledgeos
cp .env.example .env            # add KOS_AI_API_KEY if you want chat; set KOS_MCP_PUBLIC_BASE
docker compose up -d --build
docker compose exec knowledge-os python -m knowledge_os set-password
```

Open http://localhost:8786 (or tunnel to it: `ssh -L 8786:localhost:8786 your-vps`). MCP is on port 8796 at `/mcp`.

Upgrading the v1 install on the VPS, HTTPS, and running without Docker are covered in **[DEPLOY.md](DEPLOY.md)**.

## Connecting agents

- **OAuth clients (Claude, ChatGPT, MCP Inspector):** add `https://<your-mcp-host>/mcp` as a custom connector. You approve with your password and choose read-only or read + write.
- **Token agents (Hermes, scripts):** *Agents & MCP → New agent token*, then
  ```json
  { "mcpServers": { "knowledge-os": { "type": "http", "url": "https://<your-mcp-host>/mcp",
      "headers": { "Authorization": "Bearer kos_…" } } } }
  ```
  The v1 `MCP_AUTH_TOKEN` / `MCP_READ_TOKEN` and the old tool names (`search_knowledge`, `save_memory`, …) keep working.

Tools: `search_notes`, `retrieve_context`, `read_note`, `list_notes`, `get_note_context`, `find_related`, `get_backlinks`, `get_graph`, `list_tags`, `search_memory`, `list_memories`, `list_skills`, `read_skill`, `list_audit_events`, `create_note`, `update_note`, `append_to_note`, `replace_in_note`, `move_note`, `add_link`, `remove_link`, `set_properties`, `remember`, `update_memory`, `forget_memory`, `delete_note`, `create_skill`.

## Project layout

```
backend/knowledge_os/   Python package (FastAPI + MCP SDK), one process serving both ports
  vault.py              safe paths, atomic writes, versions, trash, move/relink
  markdown.py           front matter, links, tags, headings, chunking
  indexer.py            incremental index, link resolution, graph queries
  embeddings.py         local/OpenAI embeddings, vector cache, background worker
  retrieval.py          hybrid search, RAG context builder, related notes
  memory.py             intentional memory
  ai.py                 provider gateway + usage metering
  tools.py              the tool set shared by the assistant and MCP
  agent.py              chat orchestration
  auth.py / oauth.py    sessions, rate limits, OAuth provider, agent tokens
  web.py / mcp_app.py   HTTP API and MCP server
backend/tests/          pytest suite (vault, API, agent, OAuth/MCP, v1 migration)
frontend/               React + Vite + TypeScript UI
docs/                   original brief and research
```

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for how it fits together and **[DEVELOPMENT.md](DEVELOPMENT.md)** to work on it.

## License

MIT
