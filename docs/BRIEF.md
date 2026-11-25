Personal AI Knowledge OS

You are the principal software architect, researcher, and implementation engineer responsible for building a production quality personal AI Knowledge OS directly on this VPS.

You already have terminal access to the VPS and are expected to inspect the machine, existing software, available resources, network configuration, and filesystem before making architectural decisions.

This is initially a private, single user project for my own use. It must be accessible through the VPS IP and a configurable port. Do NOT expose it publicly, configure unnecessary public infrastructure, or build SaaS functionality yet.

The goal is to build something inspired by Obsidian, but significantly more AI native.

The final system should combine:

1. A local Markdown based knowledge base
2. Persistent AI memory
3. A knowledge graph
4. Semantic search and RAG
5. An AI agent capable of manipulating the knowledge base
6. A provider independent AI architecture
7. A clean web interface
8. Local first operation
9. Open source components wherever practical
10. Extremely efficient API usage

Only the OpenAI API key should be required initially.

Do not assume that OpenAI must power every part of the system.

The system should use deterministic local software for deterministic operations and reserve OpenAI calls for tasks that actually require intelligence.

The project will initially cover Phases 1 through 4.

Do NOT implement the telephone/SIP/voice calling system yet. Design the architecture so voice can be added later without restructuring the core.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PHASE 0: DEEP RESEARCH BEFORE CODING

THIS IS MANDATORY.

Do NOT begin implementation immediately.

First perform deep technical research into existing open source projects and technologies that solve parts of this problem.

The objective is to avoid reinventing existing solutions or building the system through blind trial and error.

Research at minimum:

Knowledge management / Obsidian alternatives:

• Obsidian
• Logseq
• AFFiNE
• Anytype
• Joplin
• Trilium
• SilverBullet

AI knowledge / RAG systems:

• Open WebUI
• AnythingLLM
• Khoj
• Other mature open source AI knowledge systems you discover

Agent frameworks:

• OpenAI Agents SDK
• Pydantic AI
• LangGraph
• MCP
• Other relevant mature open source frameworks

Search and vector technologies:

• SQLite FTS5
• LanceDB
• Chroma
• Qdrant
• FAISS
• sqlite vector extensions
• Local embedding models

Editor technologies:

• CodeMirror
• Monaco
• Milkdown
• TipTap
• Other mature Markdown editors

Graph technologies:

• Cytoscape.js
• React Flow
• D3
• Other appropriate graph libraries

Later voice architecture:

• LiveKit Agents
• Pipecat
• Asterisk
• FreeSWITCH

Voice is not part of the current implementation, but research it enough to ensure the current architecture won’t block future integration.

For each relevant project determine:

• What problem it solves
• Architecture
• Technology stack
• Database approach
• Search approach
• AI architecture
• Plugin/tool architecture
• Strengths
• Weaknesses
• License
• Whether components can legally be reused
• Whether it should be integrated, adapted, or avoided
• What we can learn from it

Do not blindly copy proprietary code.

Pay close attention to licenses.

Prefer permissively licensed open source components.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PHASE 0 RESEARCH DELIVERABLE

Before coding, create:

RESEARCH.md

It must contain:

1. Existing project comparison
2. Technology comparison
3. License analysis
4. Recommended architecture
5. Components to reuse
6. Components to build ourselves
7. Components explicitly rejected and why
8. Security considerations
9. Performance considerations
10. Token cost optimization strategy
11. Future voice integration strategy
12. Implementation roadmap

Also create:

ARCHITECTURE.md

This must describe the final architecture in enough detail that another engineer could understand the entire system without reading the source code.

Only after completing this research and architecture phase should implementation begin.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CORE DESIGN PRINCIPLE

The most important architectural rule is:

THE KNOWLEDGE SYSTEM MUST NOT DEPEND ON A SINGLE AI PROVIDER.

OpenAI is the initial agent provider because I will provide an OpenAI API key.

However, build an AI provider abstraction.

The architecture should eventually support:

• OpenAI
• OpenRouter
• Ollama
• Anthropic
• Gemini
• Custom OpenAI compatible endpoints
• Local models

The knowledge base must remain completely independent of the selected model.

For example:

Agent Model = OpenAI

Chat Model = Qwen

Embedding Model = local model

Vision Model = Gemini

The system must be capable of this architecture without requiring a rewrite.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PHASE 1: CORE KNOWLEDGE SYSTEM

Build the foundational knowledge application.

The primary source of truth must be Markdown files.

The user owns the data.

Do not create a proprietary storage format that makes migration difficult.

Recommended conceptual structure:

vault/

Notes/
Projects/
Resources/
Attachments/
.brainbox/
    database
    indexes
    embeddings
    configuration
    backups

The exact structure can change if research reveals a superior architecture.

The application must support:

• Create notes
• Edit notes
• Delete notes
• Rename notes
• Move notes
• Folders
• Tags
• Markdown
• Markdown preview
• Wiki links
• Backlinks
• Internal links
• Attachments
• Search
• Recent notes
• Favorites
• Note metadata
• Command palette
• Keyboard shortcuts
• Import/export
• Automatic saving
• Recovery from crashes

The Markdown files must remain readable outside the application.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PHASE 2: KNOWLEDGE GRAPH

Build a proper knowledge graph.

Explicit relationships such as:

[[Meta Ads]]

[[Creative Testing]]

[[Scaling]]

must automatically produce relationships.

Also allow AI assisted relationship discovery.

The graph should represent:

• Notes
• Tags
• Concepts
• Projects
• Relationships

The graph must be interactive.

Clicking a node should open its underlying note.

Provide:

• Global graph
• Local note graph
• Related notes
• Backlinks
• Outgoing links
• Orphan notes

Do not make graph generation dependent on OpenAI for basic explicit relationships.

Those should be generated deterministically from Markdown links and metadata.

AI should only be used for higher level semantic relationship discovery.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PHASE 3: AI AGENT + MEMORY

Build an AI agent that can interact with the Knowledge OS through controlled tools.

Initially use the OpenAI API.

The OpenAI API key should be supplied through an environment variable or secure configuration mechanism.

Never hardcode the key.

The agent should have tools conceptually similar to:

search_notes()

read_note()

create_note()

edit_note()

delete_note()

rename_note()

move_note()

search_memory()

remember()

forget()

find_related_notes()

create_link()

remove_link()

search_semantic()

summarize()

get_note_context()

get_project_context()

The exact tool definitions should be determined during research.

The agent must NOT receive unrestricted shell access or unrestricted filesystem access.

All modifications must go through controlled application tools.

Implement validation, error handling and logging.

For destructive operations, design an appropriate confirmation mechanism.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MEMORY SYSTEM

Memory must be separate from normal notes.

Create a structured persistent memory layer.

Memory should support:

• Create memory
• Search memory
• Update memory
• Forget memory
• Categorize memory
• Deduplicate memory
• Track creation/update timestamps
• Track source
• Track confidence where appropriate

Examples:

“Remember that I prefer OpenAI for my main agent.”

“Remember this project uses the local VPS.”

“Forget the old Qwen configuration.”

The memory system must not blindly store every conversation.

Do NOT automatically save everything.

Memory extraction should be intentional and controlled.

The user should be able to inspect, edit and delete memories.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PHASE 4: SEMANTIC SEARCH + RAG + AI CHAT

Build semantic search.

The system should index Markdown content locally.

Research and choose the best local embedding solution that fits the VPS resources.

Prefer free and open source local embeddings where practical.

Do NOT automatically use OpenAI embeddings if a good local alternative is sufficient.

The system should:

1. Parse notes
2. Chunk relevant content
3. Generate embeddings locally
4. Store embeddings locally
5. Retrieve semantically relevant content
6. Combine it with metadata and relationships
7. Send only the necessary context to the LLM

The entire vault must NEVER be sent to OpenAI.

Only relevant context should be included.

Example:

User asks:

“What did I learn about scaling winning products?”

The system should:

1. Search locally
2. Retrieve relevant notes
3. Retrieve related memories
4. Traverse useful graph relationships
5. Rank context
6. Send a compact context package to the AI
7. Generate the answer

This is critical for minimizing API costs.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TOKEN AND COST OPTIMIZATION

This requirement is extremely important.

Do not use an LLM for tasks that deterministic software can perform.

These should happen locally:

• File operations
• Markdown parsing
• Exact search
• FTS search
• Link detection
• Backlink generation
• Graph construction
• Metadata extraction
• Duplicate detection where deterministic
• Database operations
• Indexing
• Chunking
• File watching
• Basic classification where possible

AI should only be called when reasoning or language intelligence is actually required.

Implement context minimization.

Do not send:

Entire vault

Entire project

Entire conversation history

when only a small portion is relevant.

Use:

• Retrieval
• Ranking
• Chunking
• Context windows
• Conversation summarization
• Memory retrieval
• Relevant graph traversal

where appropriate.

If local embeddings can replace paid API embedding calls, use them.

If local models can perform a task adequately without API cost, allow that capability.

The architecture should make AI usage measurable.

Create usage statistics such as:

• API calls
• Tokens consumed
• Estimated cost
• Model used
• Operation type

Provide this in the application or an admin/debug section.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AI GATEWAY

Create a provider abstraction.

Conceptually:

AIProvider

OpenAIProvider
OpenRouterProvider
OllamaProvider
CustomOpenAICompatibleProvider

Additional providers can be added later.

Do not hardcode OpenAI logic throughout the application.

Only the provider implementation should know how to communicate with OpenAI.

The rest of the application should communicate through a standard interface.

The configuration should eventually support:

AI provider

Model

API key

Base URL

Temperature or equivalent

Context configuration

Tool support

Vision support where available

Do not require all providers to support every feature.

Gracefully detect capabilities.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OPENAI AGENT

The initial implementation should use OpenAI for the agent.

The agent must be able to:

• Search the knowledge base
• Read relevant notes
• Create notes
• Modify notes
• Manage memory
• Create links
• Find relationships
• Summarize information
• Organize information
• Answer questions using the user’s knowledge

Example:

User:

“Create a note about Meta Ads testing and connect it to my existing creative testing notes.”

The agent should:

1. Search existing notes
2. Identify relevant notes
3. Create the new Markdown note
4. Add appropriate links
5. Update the graph
6. Confirm what it did

Do not merely generate text telling the user what they could do.

The agent must perform the actual operation.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WEB APPLICATION

Build a polished, modern interface.

It should feel like a serious knowledge application, not a developer dashboard.

Core layout should include:

Sidebar

Vault
Folders
Notes
Tags
Projects
Search
Graph
AI
Memory
Settings

Main workspace

Markdown editor
Preview
AI interaction
Search results
Graph

The UI must be responsive.

Use dark mode and light mode if practical.

Prioritize usability over unnecessary animations.

Do not waste development time on decorative features before core functionality works.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LOCAL VPS DEPLOYMENT

The application must run on this VPS.

First inspect:

• OS
• CPU
• RAM
• Disk
• Python version
• Node version
• Docker availability
• Existing services
• Existing ports
• Firewall
• Reverse proxy
• Available disk space

Do not overwrite or break existing applications.

Choose an unused port.

The initial access method should be:

http://VPS_IP:PORT

Do not expose it to the public internet unless explicitly requested.

Bind appropriately according to the safest practical configuration.

If binding to 0.0.0.0 is required for access from my device, document the security implications and configure basic protection where practical.

Do not introduce Cloudflare, domains, public authentication infrastructure, payment systems or SaaS architecture at this stage.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DEPLOYMENT

Prefer Docker Compose if research indicates it is appropriate.

The final project should have:

docker-compose.yml

.env.example

README.md

RESEARCH.md

ARCHITECTURE.md

DEVELOPMENT.md

and appropriate source structure.

The entire project should be reproducible.

I should be able to restart the VPS and bring the application back up without manually reconstructing anything.

Use persistent volumes for:

• Vault
• Database
• Embeddings
• Configuration
• Backups

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SECURITY

Even though this is private, follow good security practices.

Never hardcode secrets.

Never commit API keys.

Use environment variables or secure secret storage.

Do not allow the AI agent unrestricted shell execution.

Validate file paths.

Prevent path traversal.

Validate tool arguments.

Protect destructive operations.

Keep backups before risky modifications.

Do not expose internal services unnecessarily.

Do not expose database ports publicly.

Add appropriate logs.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DATA OWNERSHIP

This is extremely important.

The user’s Markdown vault must remain portable.

If the application is deleted, the Markdown files should still exist.

Do not make the user dependent on your database to recover their knowledge.

Database and vector indexes should be considered rebuildable.

The architecture should allow:

Markdown files

metadata

links

attachments

to remain the fundamental source of truth.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FUTURE VOICE ARCHITECTURE

Do not implement this now.

But design clean interfaces so that later we can add:

DID / SIP trunk

↓

Asterisk or FreeSWITCH

↓

LiveKit Agents or Pipecat

↓

STT

↓

AI Gateway

↓

Agent + Tools

↓

Knowledge OS

↓

TTS

↓

Phone

The future voice interface must be able to use the SAME agent tools.

For example:

Web:

“Remember this.”

Phone:

“Remember this.”

Both should ultimately call:

remember()

The phone system should be an interface to the same AI brain, not a separate brain.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DEVELOPMENT METHODOLOGY

Do not attempt to build everything in one giant implementation.

Work incrementally.

For each phase:

1. Plan
2. Implement
3. Test
4. Inspect
5. Fix
6. Document
7. Move to the next phase

Do not declare something complete because the server starts.

Test actual functionality.

For example:

Create note

Edit note

Restart application

Verify note remains

Create link

Verify backlink

Search

Verify results

Create memory

Restart

Verify memory remains

Ask AI about note

Verify retrieval

Ask agent to create note

Verify actual file creation

Ask agent to modify note

Verify actual modification

Delete note

Verify correct behavior

Rebuild indexes

Verify semantic search

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FAILURE HANDLING

If a chosen technology does not work properly on this VPS:

Do not repeatedly brute force the same approach.

Stop.

Diagnose.

Research alternatives.

Choose the simplest stable alternative.

Document the decision.

Do not introduce unnecessary complexity just to preserve an original technology choice.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

IMPORTANT: DO NOT OVERENGINEER

This is initially a personal application.

Do not build:

• Billing
• Subscriptions
• Marketing pages
• Multi tenant SaaS
• Public registration
• Team permissions
• Enterprise administration
• Complex Kubernetes infrastructure
• Microservices everywhere
• Unnecessary cloud dependencies

Build a clean modular monolith unless research proves another architecture is necessary.

The application should be easy to understand and maintain.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FINAL ACCEPTANCE CRITERIA

Do not consider the project complete until all of the following work:

[ ] Markdown vault works

[ ] Notes can be created and edited

[ ] Notes survive application restart

[ ] Folders work

[ ] Tags work

[ ] Wiki links work

[ ] Backlinks work

[ ] Search works

[ ] Graph works

[ ] Attachments work

[ ] Memory system works

[ ] Memory survives restart

[ ] Semantic search works

[ ] RAG works

[ ] OpenAI agent works

[ ] Agent can use tools

[ ] Agent can create notes

[ ] Agent can edit notes

[ ] Agent can search notes

[ ] Agent can manage memory

[ ] Agent can create relationships

[ ] AI provider abstraction exists

[ ] OpenAI API key is securely configured

[ ] API usage is minimized

[ ] Token usage can be monitored

[ ] Local embeddings are used if technically appropriate

[ ] Entire vault is never unnecessarily sent to OpenAI

[ ] Application runs on the VPS

[ ] Application is accessible through VPS IP and port

[ ] Data persists across restarts

[ ] Backups exist

[ ] Documentation exists

[ ] No secrets are committed

[ ] Existing VPS applications are not broken

[ ] Future voice integration is architecturally possible

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FINAL INSTRUCTION

You are not being asked to produce a toy demo.

Build this as a real, maintainable system.

However, do not confuse “production quality” with unnecessary complexity.

Research existing open source solutions first.

Reuse mature components where appropriate.

Build only what is necessary.

Prefer local computation.

Prefer open source.

Prefer deterministic processing.

Minimize OpenAI API calls.

Keep the user’s data portable.

Keep AI providers interchangeable.

Keep the architecture modular.

Do not guess when a technical question can be researched.

Do not blindly experiment when an existing mature solution can be studied.

Before making major architectural decisions, verify them against current documentation and source code.

Start by inspecting the VPS and performing PHASE 0 research.

Do not start coding until RESEARCH.md and ARCHITECTURE.md have been produced and reviewed internally.

Then proceed sequentially through Phases 1, 2, 3 and 4.

Stop after Phase 4 and provide a final implementation report containing:

• What was built
• Architecture
• Technologies selected
• Open source projects reused or learned from
• Files created
• Services running
• Port and access information
• Environment variables required
• OpenAI configuration instructions
• AI provider configuration
• Memory architecture
• RAG architecture
• Token optimization mechanisms
• Security measures
• Backup strategy
• Known limitations
• Recommended next improvements
• Exact commands to start, stop, restart and update the system

The only thing I should need to provide manually for the initial AI functionality is my OpenAI API key.