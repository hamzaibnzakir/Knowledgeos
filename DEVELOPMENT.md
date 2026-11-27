# Development

## Backend

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,local-embeddings]"
export KOS_DATA_DIR=$PWD/../data-dev KOS_MCP_PUBLIC_BASE=http://localhost:8796
python -m knowledge_os set-password
python -m knowledge_os serve          # web :8786, MCP :8796
python -m pytest -q                   # tests run offline (fake embedder)
```

## Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to :8786
npm run build      # outputs frontend/dist, which the backend serves
```

## Conventions

- **All writes go through `vault.py` / `memory.py`.** They validate paths, snapshot versions, keep the index in step and record an audit event with the actor.
- **New capabilities are tools.** Add a function with a Pydantic argument model in `tools.py` and it is available to MCP agents automatically. Mark it `scope=WRITE` if it changes anything.
- **Memory changes go through `memory.py`** (`remember`, `forget`, `supersede`, `approve_fact`, `resolve_proposal`). Never write `Memory/` files by string-munging elsewhere; the trust rules live there.
- **Errors** raise `KOSError` (or `NotFound` / `Conflict` / `Forbidden`) with a message safe to show users; never include filesystem paths or secrets.
- **Schema changes:** add idempotent steps to `db._migrate` and bump `SCHEMA_VERSION`.

## Testing a change end to end

1. `python -m pytest -q`
2. `cd frontend && npm run build`
3. `KOS_FRONTEND_DIST=../frontend/dist python -m knowledge_os serve`, sign in, try the flow.
4. For MCP, point the MCP Inspector (`npx @modelcontextprotocol/inspector`) at `http://localhost:8796/mcp` and connect with OAuth or a token.
