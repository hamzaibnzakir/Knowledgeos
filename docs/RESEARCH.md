# Personal AI Knowledge OS — Phase 0 Research

**Author:** Manus AI  
**Status:** Initial architecture research before implementation  
**Scope:** Private, single-user, local-first service on the VPS; existing applications remain isolated.

## Executive recommendation

Build a modular monolith around a Markdown vault. Markdown files remain the source of truth; SQLite stores rebuildable metadata, full-text indexes, graph edges, memories, and usage records. Use deterministic local parsing for notes, links, tags, metadata, graph construction, chunking, and exact search. Add semantic retrieval through a local embedding adapter, with SQLite FTS5 as the always-available lexical fallback. Put all LLM access behind an `AIProvider` interface. The first provider is OpenAI, configured only through an environment variable; future providers can implement the same interface without changing the knowledge layer.

## Existing-project comparison

| Project | Useful lessons | Main trade-off | License / reuse decision |
|---|---|---|---|
| Obsidian | Excellent Markdown portability, wiki links, backlinks, graph-oriented UX. | Core application is proprietary, so implementation details cannot be copied. | Do not copy code; reproduce compatible file conventions and interaction patterns independently. |
| Logseq | Privacy-first knowledge management, Markdown/Org support, graph-centric navigation, plugin extensibility. | AGPL-3.0 and a large ClojureScript codebase make direct reuse inappropriate for a small modular monolith. | Learn from UX and data portability; do not copy code unless complying with AGPL obligations. [1] |
| Joplin | Markdown-first notes, attachments, synchronization and export discipline. | Broader synchronization scope than this private first release. | Study workflows; implement only local vault behavior initially. |
| Trilium | Hierarchical notes, rich metadata and self-hosting. | More server-centric and less focused on plain-file ownership. | Learn from hierarchy and metadata; avoid adopting its storage model. |
| SilverBullet | Markdown files, extensible commands and browser-based workflows. | Extension model is powerful but adds complexity. | Learn from command-oriented UX; implement a smaller command palette. |
| AFFiNE / Anytype | Integrated blocks, graph and collaboration experiences. | More complex synchronization and storage assumptions. | Use as product references, not as code dependencies. |
| Open WebUI | Broad provider support and self-hosted chat UX. | A general chat platform is larger than the personal knowledge scope. | Integrate concepts, not application code. |
| AnythingLLM / Khoj | Practical document retrieval and personal AI workflows. | Their deployment and indexing choices may be heavier than the VPS needs. | Evaluate operational ideas; avoid unnecessary platform coupling. |
| Onyx | Mature agentic RAG, connectors, actions, MCP, and provider breadth. The Community Edition is described as MIT licensed and includes core chat, RAG, agents and actions. | Standard deployment includes multiple services and is too heavy for the first private modular monolith. [2] | Reuse ideas and interfaces; do not embed the whole platform. |

## Agent architecture

The OpenAI Agents SDK is Python-first and centers on agents, tools, handoffs, guardrails, sessions and tracing. Its documentation explicitly separates managed agent workflows from direct Responses API use and supports model/provider abstractions and MCP integration. [3] For this project, a thin internal tool layer is preferable initially: controlled application functions expose note, memory, search and graph operations, while the provider adapter owns model calls. An SDK can be added behind this boundary later, but the data and safety model must not depend on unrestricted shell access.

Pydantic validation is appropriate for tool arguments and API schemas. LangGraph is useful if the workflow grows into durable multi-step state machines; it is not required for the initial request/response agent loop. MCP is a future interoperability boundary, not a reason to expose unrestricted filesystem tools.

## Search, vectors and embeddings

SQLite FTS5 is the baseline exact and lexical search engine because it is embedded, persistent, easy to back up, and deterministic. Semantic search should be implemented behind an embedding interface. The first implementation can use a local embedding backend when compatible with VPS resources, with a clear fallback to FTS5 when model packages or memory are unavailable. Qdrant provides mature vector collections, payload filtering, hybrid search, on-device embeddings and a documented self-hosted path, but it would introduce another service and public-port risk for this single-user first release. [4] A Qdrant adapter can be added later without changing the retrieval interface.

## Editor and graph choices

Use a web Markdown editor with a proven text area/editor component and a separate rendered preview. CodeMirror is a strong fit for a lightweight editor; Monaco is powerful but heavier than necessary. Milkdown or TipTap can be considered later if collaborative rich editing is required. For the graph, Cytoscape.js is a good fit for node/edge interaction and local graph filtering; React Flow is better for workflow canvases than dense knowledge graphs. The first graph can be rendered from deterministic SQLite edges and opened back into the underlying note.

## License analysis

Prefer MIT, BSD and Apache-2.0 dependencies for the application core. Logseq is AGPL-3.0 [1], while Onyx CE advertises MIT coverage for its core features [2]. The project will not copy proprietary Obsidian code or AGPL application code. Every dependency must be reviewed before inclusion, and third-party license notices must be retained. This is an architectural reuse decision, not legal advice; a production redistribution review should be performed before commercial distribution.

## Recommended architecture

The service is a private modular monolith with these boundaries:

1. **Vault service:** safe path resolution, Markdown CRUD, folders, attachments, backups and file watching.
2. **Parser/indexer:** front matter, tags, wiki links, backlinks, chunks and rebuildable indexes.
3. **SQLite store:** note metadata, FTS5, graph edges, memories, conversations and usage telemetry.
4. **Retrieval service:** lexical search, optional local vectors, metadata/graph expansion and compact context assembly.
5. **AI gateway:** provider-neutral `chat`, `embed`, capability detection, model settings and usage accounting.
6. **Agent service:** validated tools with confirmation gates for destructive actions; no shell tool.
7. **Web/API layer:** responsive single-user UI and JSON endpoints, bound privately first.

## Security and performance

The API must validate vault-relative paths, reject traversal, restrict attachments to the vault, avoid serving hidden configuration files, and never log secrets. Destructive tool operations require explicit confirmation. The OpenAI key is read from an environment variable and never persisted in the database. The entire vault is never sent to an LLM; only ranked, compact retrieved context is included. File watching and indexing are local and incremental. SQLite backups are atomic and the Markdown vault is copied independently.

## Token-cost strategy

Use deterministic parsing, FTS5, graph construction, chunking, deduplication and local embeddings wherever practical. Limit agent context to the user request, relevant note excerpts, related memory records and selected graph edges. Summarize long conversations locally or incrementally. Record provider, model, input/output tokens and estimated cost per call. Default to a lower-cost model for classification or summarization and allow a separate agent model configuration.

## Future voice strategy

Keep the agent tools transport-neutral. A later voice interface can connect SIP/Asterisk or FreeSWITCH to LiveKit Agents or Pipecat, then speech-to-text and text-to-speech around the same AI gateway and tool service. No voice code belongs in the current release.

## Phased roadmap

| Phase | Deliverable |
|---|---|
| 0 | Research, license review, RESEARCH.md and ARCHITECTURE.md. |
| 1 | Markdown vault, CRUD, folders, tags, links, attachments, FTS5 search, backups and web UI. |
| 2 | Deterministic global/local graph, backlinks, related notes and orphan detection. |
| 3 | Provider abstraction, OpenAI agent, controlled tools, persistent intentional memories and usage metrics. |
| 4 | Local embeddings where practical, hybrid RAG, compact context assembly and AI chat. |

## References

[1]: https://github.com/logseq/logseq "Logseq repository and AGPL-3.0 license"
[2]: https://github.com/onyx-dot-app/onyx "Onyx open-source AI platform and licensing information"
[3]: https://openai.github.io/openai-agents-python/ "OpenAI Agents SDK documentation"
[4]: https://qdrant.tech/documentation/ "Qdrant documentation"

