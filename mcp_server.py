import base64
import hashlib
import json
import os
import re
import inspect
import secrets
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from pydantic import AnyHttpUrl
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse, RedirectResponse

from knowledge_os_app import ROOT, VAULT, db, index_note, now, safe, password_matches, note_metadata, record_action

PUBLIC_BASE = os.getenv("MCP_PUBLIC_BASE", "http://169.58.171.152:8796")
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "")
MCP_READ_TOKEN = os.getenv("MCP_READ_TOKEN", "")
CONNECTIONS_FILE = ROOT / "data" / "mcp_connections.json"
AGENTS_DIR = VAULT / "Agents"
READ_SCOPE = "knowledge:read"
WRITE_SCOPE = "knowledge:write"
FULL_SCOPE = "knowledge:full"
DEFAULT_SCOPE = f"{READ_SCOPE} {WRITE_SCOPE}"


OAUTH_STATE_FILE = ROOT / "data" / "oauth_state.json"
OAUTH_ISSUER = PUBLIC_BASE.rstrip("/")
OAUTH_AUTH_CODE_TTL = 300
OAUTH_TOKEN_TTL = 3600

def _oauth_read():
    try: return json.loads(OAUTH_STATE_FILE.read_text(encoding="utf-8"))
    except Exception: return {"clients": {}, "codes": {}, "tokens": {}}

def _oauth_write(data):
    OAUTH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OAUTH_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(OAUTH_STATE_FILE)

def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

def _oauth_client_defaults():
    data = _oauth_read()
    cid = os.getenv("OAUTH_CLIENT_ID", "")
    csecret = os.getenv("OAUTH_CLIENT_SECRET", "")
    redirect = os.getenv("OAUTH_CLIENT_REDIRECT_URI", "")
    if cid and csecret and redirect and cid not in data["clients"]:
        data["clients"][cid] = {"client_id": cid, "client_secret": csecret, "redirect_uris": [redirect], "client_name": "Knowledge OS Web Client"}
        _oauth_write(data)
    return data

class StaticBearerVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        if MCP_AUTH_TOKEN and secrets.compare_digest(token, MCP_AUTH_TOKEN):
            return AccessToken(token=token, client_id="knowledge-os-write-client", scopes=[READ_SCOPE, WRITE_SCOPE])
        if MCP_READ_TOKEN and secrets.compare_digest(token, MCP_READ_TOKEN):
            return AccessToken(token=token, client_id="knowledge-os-read-client", scopes=[READ_SCOPE])
        data = _oauth_read()
        item = data.get("tokens", {}).get(token)
        if item and float(item.get("expires_at", 0)) > time.time():
            return AccessToken(token=token, client_id=item.get("client_id", "oauth-client"), scopes=item.get("scopes", [READ_SCOPE]))
        return None


def _agent_slug(client_id: str):
    raw = re.sub(r"[^a-z0-9]+", "-", (client_id or "unknown-agent").lower()).strip("-")
    return (raw[:48] or "unknown-agent")

def ensure_agent(client_id: str):
    cid = client_id or "unknown-agent"
    slug = _agent_slug(cid)
    t = now(); c = db()
    c.execute("CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, client_id TEXT UNIQUE, name TEXT, provider TEXT, model TEXT, first_connected TEXT, last_seen TEXT, actions INTEGER DEFAULT 0, retrievals INTEGER DEFAULT 0, skills_used INTEGER DEFAULT 0)")
    row = c.execute("SELECT * FROM agents WHERE client_id=?", (cid,)).fetchone()
    if row:
        c.execute("UPDATE agents SET last_seen=? WHERE client_id=?", (t, cid)); c.commit(); c.close(); return dict(row), slug
    name = cid.replace("-", " ").replace("_", " ").title()
    c.execute("INSERT INTO agents(id,client_id,name,provider,model,first_connected,last_seen) VALUES(?,?,?,?,?,?,?)", (slug, cid, name, "MCP client", "unknown", t, t)); c.commit(); c.close()
    base = AGENTS_DIR / slug
    for d in [base, base/'Sessions', base/'Decisions', base/'Tasks', base/'Logs']:
        d.mkdir(parents=True, exist_ok=True)
    identity = base/'AGENT.md'
    if not identity.exists():
        identity.write_text(f"---\nname: {name}\nagent_id: {slug}\nprovider: MCP client\nmodel: unknown\nfirst_connected: {t[:10]}\nstatus: active\n---\n\n# Agent Identity\n\nThis identity is registered automatically for an authenticated MCP client.\n\n## Operating policy\n\nSearch before retrieving. Read only relevant notes or skills. Log activity separately from permanent knowledge. Promote only meaningful discoveries into durable notes.\n", encoding='utf-8')
    return {"id":slug,"client_id":cid,"name":name,"provider":"MCP client","model":"unknown","first_connected":t,"last_seen":t,"actions":0,"retrievals":0,"skills_used":0}, slug

def touch_agent(tool_name: str):
    token = get_access_token(); cid = getattr(token, "client_id", None) if token else None
    row, slug = ensure_agent(cid or "unknown-agent")
    retrieval = tool_name in {"list_notes","read_note","search_knowledge","list_memories","get_knowledge_graph","get_backlinks","list_skills","list_audit_events"}
    skill = tool_name in {"list_skills","read_skill"}
    t = now(); c = db(); c.execute("UPDATE agents SET last_seen=?, actions=actions+1, retrievals=retrievals+?, skills_used=skills_used+? WHERE id=?", (t, int(retrieval), int(skill), slug)); c.commit(); c.close()
    log = AGENTS_DIR / slug / "Logs" / (t[:10] + ".md")
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(f"\n## {t}\n\n- Tool: `{tool_name}`\n- Client ID: `{cid or 'unknown-agent'}`\n- Type: {'retrieval' if retrieval else 'action'}\n")
    return slug

def require_scope(scope: str) -> None:
    token = get_access_token()
    if token is None or (scope not in token.scopes and FULL_SCOPE not in token.scopes):
        raise ToolError(f"MCP authorization denied: this operation requires the {scope} scope. Reconnect the OAuth client and approve knowledge:write, or use the full bearer token.")
    touch_agent(inspect.stack()[1].function)


mcp = MCPServer(
    name="Personal AI Knowledge OS",
    title="Personal AI Knowledge OS",
    description="Provider-independent access to a private Markdown knowledge base, intentional memory, skills, search, and graph relationships.",
    version="1.0.0",
    token_verifier=StaticBearerVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(PUBLIC_BASE + "/"),
        resource_server_url=AnyHttpUrl(PUBLIC_BASE + "/mcp"),
        required_scopes=[READ_SCOPE],
    ),
)


@mcp.tool()
def list_notes(limit: int = 50) -> list[dict]:
    """List Markdown notes in the private knowledge vault."""
    require_scope(READ_SCOPE)
    c = db(); rows = c.execute("SELECT path,title,updated_at,tags,aliases,properties FROM notes ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall(); c.close()
    out = []
    for r in rows:
        item = dict(r); item["aliases"] = json.loads(item.pop("aliases") or "[]"); item["properties"] = json.loads(item.pop("properties") or "{}"); out.append(item)
    return out


@mcp.tool()
def read_note(path: str) -> dict:
    """Read the full Markdown content and parsed metadata of one note."""
    require_scope(READ_SCOPE)
    p = safe(path)
    if not p.exists(): raise ValueError("Note not found")
    content = p.read_text(encoding="utf-8"); meta, aliases, props = note_metadata(content)
    return {"path": str(p.relative_to(VAULT)), "content": content, "frontmatter": meta, "aliases": aliases, "properties": props}

@mcp.tool()
def update_note(path: str, content: str) -> dict:
    """Replace the full Markdown content of an existing note and rebuild its indexes."""
    require_scope(WRITE_SCOPE)
    p = safe(path)
    if not p.exists(): raise ValueError("Note not found")
    p.write_text(content, encoding="utf-8"); rel = str(p.relative_to(VAULT)); index_note(rel, content)
    c = db(); action = record_action(c, None, "mcp.note.update", "completed", {"path": rel}); c.commit(); c.close()
    return {"ok": True, "path": rel, "action": action}
@mcp.tool()
def get_backlinks(path: str) -> list[dict]:
    """Return notes that link to the requested vault-relative note."""
    require_scope(READ_SCOPE)
    rel = str(safe(path).relative_to(VAULT)); c = db(); rows = c.execute("SELECT source FROM edges WHERE target=? ORDER BY source", (rel,)).fetchall(); c.close()
    return [{"path": r[0]} for r in rows]
@mcp.tool()
def list_audit_events(limit: int = 100) -> list[dict]:
    """List server-recorded action events for connected-client verification."""
    require_scope(READ_SCOPE)
    c = db(); rows = c.execute("SELECT id,chat_id,action,status,details,created_at FROM action_events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall(); c.close()
    out=[]
    for r in rows:
        item=dict(r)
        try: item["details"] = json.loads(item["details"])
        except Exception: pass
        out.append(item)
    return out
@mcp.tool()
def search_knowledge(query: str, limit: int = 20) -> list[dict]:
    """Search the Markdown vault using the Knowledge OS full-text index."""
    require_scope(READ_SCOPE)
    q = query.strip()
    if not q: return []
    c = db()
    try:
        rows = c.execute("SELECT path,title,snippet(notes_fts,2,'<mark>','</mark>','…',18) snippet FROM notes_fts WHERE notes_fts MATCH ? LIMIT ?", (q.replace('"', ' '), max(1, min(limit, 50)))).fetchall()
    except Exception:
        needle = "%" + q.replace("%", "\\%") + "%"
        rows = c.execute("SELECT path,title,substr(content,1,240) snippet FROM notes WHERE lower(content) LIKE lower(?) ESCAPE '\\' OR lower(title) LIKE lower(?) ESCAPE '\\' LIMIT ?", (needle, needle, max(1, min(limit, 50)))).fetchall()
    c.close(); return [dict(r) for r in rows]


@mcp.tool()
def list_memories(limit: int = 100) -> list[dict]:
    """List intentional user memories. Only explicitly saved memories appear here."""
    require_scope(READ_SCOPE)
    c = db(); rows = c.execute("SELECT id,content,category,confidence,created_at,updated_at FROM memories ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall(); c.close()
    return [dict(r) for r in rows]


@mcp.tool()
def get_knowledge_graph() -> dict:
    """Return note nodes and wiki-link relationships from the local knowledge graph."""
    require_scope(READ_SCOPE)
    c = db(); nodes = [dict(r) for r in c.execute("SELECT path,title FROM notes")]; edges = [dict(r) for r in c.execute("SELECT source,target FROM edges")]; c.close()
    return {"nodes": nodes, "edges": edges}


@mcp.tool()
def list_skills(limit: int = 100) -> list[dict]:
    """List reusable Markdown skill files stored in the Skills library."""
    require_scope(READ_SCOPE)
    c = db(); rows = c.execute("SELECT path,title,content,updated_at,tags FROM notes WHERE path LIKE 'Skills/%' ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall(); c.close()
    return [dict(r) for r in rows]


def _filename(value: str, fallback: str = "Untitled") -> str:
    name = Path((value or fallback).strip()).name
    if name.lower().endswith('.md'): name = name[:-3]
    name = re.sub(r"[^A-Za-z0-9 _.-]+", "-", name).strip(" .-")
    return (name or fallback)[:120] + ".md"

def _frontmatter(content: str, title: str, category: str, service: str = "") -> str:
    if content.lstrip().startswith("---"):
        return content
    extra = f"\nservice: {service}" if service else ""
    return f"---\ntitle: {title}\ncategory: {category}\nstatus: active{extra}\n---\n\n" + content.lstrip()

@mcp.tool()
def create_skill(title: str, content: str, service: str = "") -> dict:
    """Create a reusable Markdown skill in the Skills library with standard frontmatter."""
    require_scope(WRITE_SCOPE)
    title = title.strip() or "Untitled Skill"; path = "Skills/" + _filename(title, "Untitled Skill")
    return create_note(path, _frontmatter(content, title, "skill", service))

@mcp.tool()
def upload_skill(filename: str, content: str, service: str = "") -> dict:
    """Save extracted text or Markdown supplied by an external AI as a reusable skill file."""
    require_scope(WRITE_SCOPE)
    title = Path(filename or "Uploaded Skill").stem.strip() or "Uploaded Skill"; path = "Skills/" + _filename(title, "Uploaded Skill")
    return create_note(path, _frontmatter(content, title, "skill", service))

@mcp.tool()
def upload_note(filename: str, content: str, destination: str = "Inbox") -> dict:
    """Save extracted text or Markdown supplied by an external AI as a Markdown note in Inbox, Notes, Projects, or Resources."""
    require_scope(WRITE_SCOPE)
    folder = (destination or "Inbox").strip().strip("/")
    if folder not in {"Inbox", "Notes", "Projects", "Resources"}: raise ToolError("destination must be Inbox, Notes, Projects, or Resources")
    title = Path(filename or "Uploaded Note").stem.strip() or "Uploaded Note"; path = folder + "/" + _filename(title, "Uploaded Note")
    return create_note(path, _frontmatter(content, title, "note"))

@mcp.tool()
def upload_memory(content: str, category: str = "general", confidence: float = 1.0) -> dict:
    """Save extracted text as one intentional memory after explicit external-AI instruction."""
    require_scope(WRITE_SCOPE)
    return save_memory(content, category, confidence)

@mcp.tool()
def create_note(path: str, content: str) -> dict:
    """Create a new Markdown note. This is an approved write operation."""
    require_scope(WRITE_SCOPE)
    p = safe(path)
    if p.exists(): raise ValueError("Note already exists")
    try:
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content, encoding="utf-8"); rel = str(p.relative_to(VAULT)); index_note(rel, content)
        c = db(); action = record_action(c, None, "mcp.note.create", "completed", {"path": rel}); c.commit(); c.close()
        return {"ok": True, "path": rel, "action": action}
    except Exception as e:
        try:
            c = db(); record_action(c, None, "mcp.note.create", "failed", {"path": str(path), "error": str(e)}); c.commit(); c.close()
        except Exception: pass
        raise ToolError(f"MCP create_note failed for {path!r}: {type(e).__name__}: {e}") from e


@mcp.tool()
def save_memory(content: str, category: str = "general", confidence: float = 1.0) -> dict:
    """Save one intentional memory, or return the existing copy when content is already stored."""
    require_scope(WRITE_SCOPE)
    text = content.strip()
    if not text: raise ValueError("Memory content cannot be empty")
    t = now(); c = db(); old = c.execute("SELECT * FROM memories WHERE content=?", (text,)).fetchone()
    if old: c.close(); return {"created": False, **dict(old)}
    cur = c.execute("INSERT INTO memories(content,category,confidence,created_at,updated_at) VALUES(?,?,?,?,?)", (text, category[:40], max(0.0, min(1.0, confidence)), t, t)); action = record_action(c, None, "mcp.memory.create", "completed", {"memory_id": cur.lastrowid, "category": category[:40]}); c.commit(); result = {"created": True, "id": cur.lastrowid, "content": text, "category": category[:40], "confidence": confidence, "action": action}; c.close(); return result


@mcp.tool()
def delete_memory(memory_id: int) -> dict:
    """Delete one intentional memory by ID. This is an approved destructive operation."""
    require_scope(WRITE_SCOPE)
    c = db(); cur = c.execute("DELETE FROM memories WHERE id=?", (memory_id,));
    if cur.rowcount == 0: c.close(); raise ValueError("Memory not found")
    action = record_action(c, None, "mcp.memory.delete", "completed", {"memory_id": memory_id}); c.commit(); c.close()
    return {"ok": True, "deleted_id": memory_id, "action": action}


@mcp.tool()
def delete_note(path: str) -> dict:
    """Delete one Markdown note by vault-relative path. This is an approved destructive operation."""
    require_scope(WRITE_SCOPE)
    p = safe(path)
    if not p.exists(): raise ValueError("Note not found")
    rel = str(p.relative_to(VAULT)); trash = ROOT / "backups" / (now().replace(":", "").replace("+00:00", "") + "-mcp-trash"); trash.mkdir(parents=True, exist_ok=True); shutil.copy2(p, trash / p.name); p.unlink(); c = db(); c.execute("DELETE FROM notes WHERE path=?", (rel,)); c.execute("DELETE FROM notes_fts WHERE path=?", (rel,)); c.execute("DELETE FROM edges WHERE source=? OR target=?", (rel, rel)); action = record_action(c, None, "mcp.note.delete", "completed", {"path": rel, "backup": str((trash / p.name).relative_to(ROOT))}); c.commit(); c.close()
    return {"ok": True, "deleted_path": rel, "backup": str((trash / p.name).relative_to(ROOT)), "action": action}


def _platform_name(request: Request) -> str:
    raw = " ".join([request.headers.get("x-mcp-client-name", ""), request.headers.get("mcp-client-name", ""), request.headers.get("user-agent", "")]).lower()
    for needle, name in [("claude", "Claude"), ("cursor", "Cursor"), ("chatgpt", "ChatGPT"), ("windsurf", "Windsurf"), ("vscode", "VS Code"), ("vs code", "VS Code"), ("continue", "Continue"), ("python", "Python MCP client"), ("node", "Node MCP client")]:
        if needle in raw: return name
    ua = request.headers.get("user-agent", "Unknown MCP client").split(" ", 1)[0]
    return ua[:80] or "Unknown MCP client"


class ConnectionTracker(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/mcp" and response.status_code < 400:
            platform = _platform_name(request)
            try:
                existing = json.loads(CONNECTIONS_FILE.read_text(encoding="utf-8")) if CONNECTIONS_FILE.exists() else {}
            except Exception:
                existing = {}
            key = platform.lower().replace(" ", "-")
            item = existing.get(key, {"platform": platform, "requests": 0, "sessions": 0})
            item["platform"] = platform
            item["requests"] = int(item.get("requests", 0)) + 1
            item["last_seen"] = now()
            session = request.headers.get("mcp-session-id")
            if session and session != item.get("last_session"):
                item["sessions"] = int(item.get("sessions", 0)) + 1
                item["last_session"] = session
            existing[key] = item
            try:
                CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp = CONNECTIONS_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
                tmp.replace(CONNECTIONS_FILE)
            except Exception:
                pass
        return response


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "knowledge-os-mcp", "transport": "streamable-http"})


security = TransportSecuritySettings(allowed_hosts=["mcp-knowledgeos.brainboxecomlab.com", "mcp-knowledgeos.brainboxecomlab.com:*", "169.58.171.152", "169.58.171.152:*", "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"])
app = mcp.streamable_http_app(transport_security=security)
app.add_middleware(ConnectionTracker)


# OAuth 2.1 / MCP discovery and authorization endpoints.
def oauth_protected_resource(request: Request):
    base = PUBLIC_BASE.rstrip("/")
    return JSONResponse({"resource": base + "/mcp", "authorization_servers": [base], "scopes_supported": [READ_SCOPE, WRITE_SCOPE], "bearer_methods_supported": ["header"]})

def oauth_server_metadata(request: Request):
    base = PUBLIC_BASE.rstrip("/")
    return JSONResponse({"issuer": base + "/", "authorization_endpoint": base + "/oauth/authorize", "token_endpoint": base + "/oauth/token", "registration_endpoint": base + "/oauth/register", "response_types_supported": ["code"], "grant_types_supported": ["authorization_code"], "code_challenge_methods_supported": ["S256"], "scopes_supported": [READ_SCOPE, WRITE_SCOPE], "token_endpoint_auth_methods_supported": ["client_secret_post", "none"]})

async def oauth_register(request: Request):
    body = await request.json()
    redirect_uris = body.get("redirect_uris") or ([body.get("redirect_uri")] if body.get("redirect_uri") else [])
    if not redirect_uris: return JSONResponse({"error": "invalid_client_metadata", "error_description": "redirect_uris is required"}, status_code=400)
    data = _oauth_read(); cid = "kosc_" + secrets.token_urlsafe(18); secret = "koss_" + secrets.token_urlsafe(32)
    item = {"client_id": cid, "client_secret": secret, "redirect_uris": redirect_uris, "client_name": body.get("client_name", "MCP client")}
    data.setdefault("clients", {})[cid] = item; _oauth_write(data)
    return JSONResponse({"client_id": cid, "client_secret": secret, "client_name": item["client_name"], "redirect_uris": redirect_uris, "token_endpoint_auth_method": "client_secret_post"}, status_code=201)

async def oauth_authorize(request: Request):
    q = request.query_params
    client_id, redirect_uri, state = q.get("client_id", ""), q.get("redirect_uri", ""), q.get("state", "")
    data = _oauth_client_defaults(); client = data.get("clients", {}).get(client_id)
    if not client or redirect_uri not in client.get("redirect_uris", []): return JSONResponse({"error": "invalid_request", "error_description": "Unknown client or redirect URI"}, status_code=400)
    if q.get("response_type") != "code" or q.get("code_challenge_method") != "S256" or not q.get("code_challenge"): return JSONResponse({"error": "invalid_request", "error_description": "Authorization code with S256 PKCE is required"}, status_code=400)
    if request.method == "GET":
        hidden = "".join(f"<input type='hidden' name='{k}' value='{str(q.get(k,''))}'>" for k in ["client_id","redirect_uri","response_type","state","code_challenge","code_challenge_method","scope"])
        page = """<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><title>Authorize · Knowledge OS</title><style>:root{color-scheme:dark}body{margin:0;min-height:100vh;background:#070b12;color:#e8eef8;font-family:system-ui;display:grid;place-items:center;padding:22px}.p{width:min(430px,100%);padding:32px;border:1px solid #263650;border-radius:20px;background:#111927}h1{margin:8px 0}p{color:#aab8cc;line-height:1.55}input{width:100%;height:48px;background:#080d15;border:1px solid #30415b;color:white;border-radius:10px;padding:0 14px;font-size:20px;letter-spacing:.24em;text-align:center}button{width:100%;height:46px;margin-top:14px;border:0;border-radius:10px;background:#8070ff;color:white;font-weight:800}</style><main class=p><div>✦ Knowledge OS</div><h1>Authorize MCP access</h1><p>Approve this MCP client to access your protected Knowledge OS with the requested scope: <b>""" + (q.get("scope") or DEFAULT_SCOPE) + """</b>.</p><form method=post>""" + hidden + "<label>Workspace access code</label><input name='password' type='password' inputmode='numeric' maxlength='6' required autofocus><button>Approve access</button></form></main>"
        return HTMLResponse(page)
    form = await request.form()
    if not password_matches(str(form.get("password", ""))): return HTMLResponse("<p>Incorrect workspace access code. Go back and try again.</p>", status_code=401)
    code = secrets.token_urlsafe(32); data = _oauth_read(); data.setdefault("codes", {})[code] = {"client_id": client_id, "redirect_uri": redirect_uri, "challenge": q.get("code_challenge"), "scope": DEFAULT_SCOPE, "expires_at": time.time() + OAUTH_AUTH_CODE_TTL}; _oauth_write(data)
    target = redirect_uri + ("&" if "?" in redirect_uri else "?") + urllib.parse.urlencode({"code": code, "state": state})
    return RedirectResponse(target, status_code=303)

async def oauth_token(request: Request):
    form = await request.form(); grant = form.get("grant_type", "")
    if grant != "authorization_code": return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    data = _oauth_read(); code = str(form.get("code", "")); item = data.get("codes", {}).pop(code, None)
    cid = str(form.get("client_id", "")); client = data.get("clients", {}).get(cid)
    if not item or item.get("expires_at", 0) < time.time() or not client or item.get("redirect_uri") != form.get("redirect_uri"):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    verifier = str(form.get("code_verifier", "")); expected = _b64(hashlib.sha256(verifier.encode()).digest())
    if not secrets.compare_digest(expected, item.get("challenge", "")): return JSONResponse({"error": "invalid_grant"}, status_code=400)
    supplied = str(form.get("client_secret", ""))
    if client.get("client_secret") and not secrets.compare_digest(supplied, client.get("client_secret")): return JSONResponse({"error": "invalid_client"}, status_code=401)
    access = secrets.token_urlsafe(40); data.setdefault("tokens", {})[access] = {"client_id": cid, "scopes": item.get("scope", DEFAULT_SCOPE).split(), "expires_at": time.time() + OAUTH_TOKEN_TTL}; _oauth_write(data)
    return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": OAUTH_TOKEN_TTL, "scope": item.get("scope", DEFAULT_SCOPE)})

app.add_route("/.well-known/oauth-protected-resource", oauth_protected_resource, methods=["GET"])
app.add_route("/.well-known/oauth-authorization-server", oauth_server_metadata, methods=["GET"])
app.add_route("/oauth/register", oauth_register, methods=["POST"])
app.add_route("/oauth/authorize", oauth_authorize, methods=["GET", "POST"])
app.add_route("/oauth/token", oauth_token, methods=["POST"])
