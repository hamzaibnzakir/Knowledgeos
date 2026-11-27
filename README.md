# Knowledge OS

A private, local-first knowledge base and **shared memory for your AI agents**: a Markdown vault you own, a knowledge graph, hybrid search, and an MCP server so Hermes, Claude, ChatGPT and scripts all work from the same brain.

> Knowledge OS has no AI of its own and needs no API key. The thinking happens in your agents; this is where they remember.

## What you get

- **Markdown vault as the source of truth** — plain `.md` files with YAML front matter, `[[wiki links]]`, aliases, `#tags`, embeds. Open the folder in Obsidian any time. The database is only an index plus history.
- **Editor** — CodeMirror with `[[` link autocomplete, autosave, conflict detection (two devices or an agent editing at once never silently overwrite each other), reading view, version history, rename/move that rewrites links, trash with restore.
- **Knowledge graph** — global and local graph (Cytoscape), backlinks, outgoing links, unlinked mentions, missing notes, orphans, related notes (links + shared tags + similar content).
- **Search** — SQLite FTS5 keyword search fused with local semantic search (`bge-small`, runs on the VPS, no API cost). Results by words, by meaning, or both.
- **Memory as Markdown** — facts live in `Memory/` as plain list items with an id and who saved them:
  `- Prefers short notes ^f3k9q2 [by:: Hermes] [on:: 2026-11-27] [src:: told by owner]`.
  `Memory/Profile.md` and `Memory/Rules.md` are given to every agent; `Memory/Projects|Clients|People|Topics/*.md` are fetched when relevant. Replaced facts move to a History section, struck through.
- **Trust rules** — you write directly. Agents can only *propose* changes to Profile and Rules (you approve in Review); other facts they save go live but marked unreviewed. Facts from web pages are marked external, and anything that reads like an instruction is flagged and kept out of briefs until you decide.
- **Agent tools** — `brief` (call first: Profile, Rules, your agent note, what's relevant, recent changes, in ~2k tokens), `recall` (search facts and notes together), `remember` / `revise` / `forget`, `log` (each agent's own dated log), plus note, link and skill tools.
- **Review and tidy-up** — a Review queue for what agents saved, a "What agents see" preview of the brief, and a Tidy-up view listing duplicates, stale facts and flagged items (Hermes can work through it with the bundled "Memory tidy-up" skill).
- **Governance** — activity log of every change and who made it, per-agent tokens and scopes, automatic backups, vault export.

## Quick start (Docker)

```bash
git clone https://github.com/hamzaibnzakir/Knowledgeos.git && cd Knowledgeos
cp .env.example .env            # set KOS_MCP_PUBLIC_BASE
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

Memory tools: `brief`, `recall`, `remember`, `revise`, `forget`, `list_facts`, `list_subjects`, `memory_health`, `log`.
Note tools: `search_notes`, `retrieve_context`, `read_note`, `list_notes`, `get_note_context`, `find_related`, `get_backlinks`, `get_graph`, `list_tags`, `create_note`, `update_note`, `append_to_note`, `replace_in_note`, `move_note`, `add_link`, `remove_link`, `set_properties`, `delete_note`, `list_skills`, `read_skill`, `create_skill`, `list_audit_events`.

## Project layout

```
backend/knowledge_os/   Python package (FastAPI + MCP SDK), one process serving both ports
  vault.py              safe paths, atomic writes, versions, trash, move/relink
  markdown.py           front matter, links, tags, headings, chunking
  indexer.py            incremental index, link resolution, graph queries
  embeddings.py         local embeddings, vector cache, background worker
  retrieval.py          hybrid search, RAG context builder, related notes
  memory.py             Markdown memory: facts, trust rules, proposals, brief, recall, logs, migration
  tools.py              the typed tool set agents use over MCP
  auth.py / oauth.py    sessions, rate limits, OAuth provider, agent tokens
  web.py / mcp_app.py   HTTP API and MCP server
backend/tests/          pytest suite (vault, memory, API, OAuth/MCP, migrations)
frontend/               React + Vite + TypeScript UI
docs/                   original brief and research
```

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for how it fits together and **[DEVELOPMENT.md](DEVELOPMENT.md)** to work on it.

## License

MIT
