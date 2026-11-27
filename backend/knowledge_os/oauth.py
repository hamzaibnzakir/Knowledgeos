"""Credentials for agents: OAuth 2.1 (via the MCP SDK), agent tokens issued from
the UI, and the legacy static tokens from the environment.

All tokens are stored hashed. Refresh tokens rotate; re-use of a rotated
refresh token revokes the whole token family. Revoking a client revokes every
token it holds.
"""
from __future__ import annotations

import html
import json
import secrets
import time
import uuid
from urllib.parse import urlencode

from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken, TokenError)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from . import auth, db
from .config import get_settings
from .util import KOSError, NotFound, jloads, now, record_event, sha256, slugify

READ, WRITE, FULL = "knowledge:read", "knowledge:write", "knowledge:full"
VALID_SCOPES = [READ, WRITE, FULL]
DEFAULT_SCOPES = [READ, WRITE]
STATIC_WRITE_CLIENT = "knowledge-os-write-client"
STATIC_READ_CLIENT = "knowledge-os-read-client"
CODE_TTL = 300
CONSENT_TTL = 900


def _issue(conn, kind: str, client_id: str, scopes: list[str], *, ttl: int | None, family: str | None = None,
           label: str | None = None, resource: str | None = None, prefix: str = "") -> tuple[str, float | None]:
    token = prefix + secrets.token_urlsafe(40)
    expires = time.time() + ttl if ttl else None
    conn.execute("INSERT INTO tokens(token_hash,kind,client_id,scopes,label,family,resource,expires_at,created_at)"
                 " VALUES(?,?,?,?,?,?,?,?,?)",
                 (sha256(token), kind, client_id, " ".join(scopes), label, family, resource, expires, now()))
    return token, expires


# --------------------------------------------------------------------------- agent tokens (UI)

def create_agent_token(label: str, scopes: list[str]) -> dict:
    label = (label or "").strip()[:60]
    if not label:
        raise KOSError("Give the token a name, e.g. 'Hermes'")
    scopes = [s for s in scopes if s in VALID_SCOPES] or [READ]
    client_id = f"agent:{slugify(label, 40)}-{uuid.uuid4().hex[:6]}"
    with db.connect() as conn:
        token, _ = _issue(conn, "pat", client_id, scopes, ttl=None, label=label, prefix="kos_")
        _ensure_agent(conn, client_id, label)
        record_event(conn, "token.create", details={"client_id": client_id, "label": label, "scopes": scopes}, actor="web")
    return {"token": token, "client_id": client_id, "label": label, "scopes": scopes}


def list_credentials() -> list[dict]:
    """Every agent identity: static env tokens, agent tokens and OAuth clients, with activity."""
    s = get_settings()
    out: list[dict] = []
    with db.connect() as conn:
        agents = {r["client_id"]: dict(r) for r in conn.execute("SELECT * FROM agents")}
        if s.mcp_auth_token:
            out.append({"client_id": STATIC_WRITE_CLIENT, "kind": "env", "label": "Static write token (MCP_AUTH_TOKEN)",
                        "scopes": [READ, WRITE], "revocable": False})
        if s.mcp_read_token:
            out.append({"client_id": STATIC_READ_CLIENT, "kind": "env", "label": "Static read token (MCP_READ_TOKEN)",
                        "scopes": [READ], "revocable": False})
        for r in conn.execute("SELECT client_id,label,scopes,created_at,last_used_at,revoked_at FROM tokens "
                              "WHERE kind='pat' ORDER BY created_at DESC"):
            out.append({"client_id": r["client_id"], "kind": "token", "label": r["label"], "scopes": r["scopes"].split(),
                        "created_at": r["created_at"], "last_used_at": r["last_used_at"],
                        "revoked_at": r["revoked_at"], "revocable": not r["revoked_at"]})
        for r in conn.execute("SELECT client_id,info,label,created_at,revoked_at FROM oauth_clients ORDER BY created_at DESC"):
            info = jloads(r["info"], {})
            active = conn.execute("SELECT count(*), max(last_used_at) FROM tokens WHERE client_id=? AND revoked_at IS NULL "
                                  "AND (expires_at IS NULL OR expires_at > ?)", (r["client_id"], time.time())).fetchone()
            out.append({"client_id": r["client_id"], "kind": "oauth",
                        "label": r["label"] or info.get("client_name") or "OAuth client",
                        "scopes": (info.get("scope") or "").split(), "created_at": r["created_at"],
                        "revoked_at": r["revoked_at"], "active_tokens": active[0], "last_used_at": active[1],
                        "redirect_uris": info.get("redirect_uris", []), "revocable": not r["revoked_at"]})
    for item in out:
        a = agents.get(item["client_id"], {})
        item["activity"] = {k: a.get(k) for k in ("first_connected", "last_seen", "actions", "retrievals", "writes")}
        if a.get("label") and item["kind"] == "oauth":
            item["label"] = a["label"]
    return out


def agent_name(client_id: str) -> str:
    """Human name for a client: used for provenance (by::) and the agent's folder under Agents/."""
    s = get_settings()
    if client_id == STATIC_WRITE_CLIENT:
        return s.mcp_write_token_name or "Hermes"
    if client_id == STATIC_READ_CLIENT:
        return "Read-only agent"
    with db.connect() as conn:
        r = conn.execute("SELECT label FROM agents WHERE client_id=? AND label IS NOT NULL AND label<>''",
                         (client_id,)).fetchone()
        if r:
            return r["label"]
        r = conn.execute("SELECT label FROM tokens WHERE client_id=? AND kind='pat' AND label IS NOT NULL",
                         (client_id,)).fetchone()
        if r:
            return r["label"]
        r = conn.execute("SELECT label, info FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
        if r:
            return r["label"] or jloads(r["info"], {}).get("client_name") or "OAuth client"
    return client_id


def revoke_client(client_id: str) -> dict:
    with db.connect() as conn:
        n = conn.execute("UPDATE tokens SET revoked_at=? WHERE client_id=? AND revoked_at IS NULL",
                         (now(), client_id)).rowcount
        c = conn.execute("UPDATE oauth_clients SET revoked_at=? WHERE client_id=? AND revoked_at IS NULL",
                         (now(), client_id)).rowcount
        conn.execute("UPDATE agents SET status='revoked' WHERE client_id=?", (client_id,))
        if not n and not c:
            raise NotFound("No active credentials for that client")
        record_event(conn, "token.revoke", details={"client_id": client_id, "tokens": n}, actor="web")
    return {"ok": True, "revoked_tokens": n}


def rename_client(client_id: str, label: str) -> dict:
    with db.connect() as conn:
        conn.execute("UPDATE agents SET label=?, name=? WHERE client_id=?", (label[:60], label[:60], client_id))
        conn.execute("UPDATE oauth_clients SET label=? WHERE client_id=?", (label[:60], client_id))
        conn.execute("UPDATE tokens SET label=? WHERE client_id=? AND kind='pat'", (label[:60], client_id))
    return {"ok": True}


def _ensure_agent(conn, client_id: str, label: str | None = None) -> None:
    t = now()
    conn.execute("INSERT OR IGNORE INTO agents(id,client_id,name,label,provider,model,first_connected,last_seen,actions,"
                 "retrievals,skills_used,status,writes) VALUES(?,?,?,?,?,?,?,?,0,0,0,'active',0)",
                 (slugify(client_id, 48), client_id, label or client_id, label, "MCP client", "unknown", t, t))


# --------------------------------------------------------------------------- token verification

def verify_bearer(token: str) -> AccessToken | None:
    s = get_settings()
    if s.mcp_auth_token and secrets.compare_digest(token, s.mcp_auth_token):
        return AccessToken(token=token, client_id=STATIC_WRITE_CLIENT, scopes=[READ, WRITE], subject="owner")
    if s.mcp_read_token and secrets.compare_digest(token, s.mcp_read_token):
        return AccessToken(token=token, client_id=STATIC_READ_CLIENT, scopes=[READ], subject="owner")
    h = sha256(token)
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM tokens WHERE token_hash=? AND kind IN ('access','pat')", (h,)).fetchone()
        if not r or r["revoked_at"] or (r["expires_at"] and r["expires_at"] < time.time()):
            return None
        if not r["last_used_at"] or r["last_used_at"][:16] != now()[:16]:
            conn.execute("UPDATE tokens SET last_used_at=? WHERE token_hash=?", (now(), h))
    return AccessToken(token=token, client_id=r["client_id"], scopes=r["scopes"].split(),
                       expires_at=int(r["expires_at"]) if r["expires_at"] else None, resource=r["resource"],
                       subject="owner")


# --------------------------------------------------------------------------- OAuth provider (MCP SDK protocol)

class OAuthProvider:
    def __init__(self, issuer: str):
        self.issuer = issuer.rstrip("/")

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with db.connect() as conn:
            r = conn.execute("SELECT info, revoked_at FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
        if not r or r["revoked_at"]:
            return None
        return OAuthClientInformationFull.model_validate_json(r["info"])

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        with db.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO oauth_clients(client_id,info,label,created_at) VALUES(?,?,?,?)",
                         (client_info.client_id, client_info.model_dump_json(), client_info.client_name, now()))
            record_event(conn, "oauth.register", details={"client_id": client_info.client_id,
                                                          "client_name": client_info.client_name}, actor="anonymous")

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        rid = secrets.token_urlsafe(24)
        data = {"client_id": client.client_id, "client_name": client.client_name, "state": params.state,
                "scopes": params.scopes or DEFAULT_SCOPES, "code_challenge": params.code_challenge,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "resource": params.resource}
        with db.connect() as conn:
            conn.execute("DELETE FROM oauth_pending WHERE expires_at < ?", (time.time(),))
            conn.execute("INSERT INTO oauth_pending(id,client_id,data,expires_at) VALUES(?,?,?,?)",
                         (rid, client.client_id, json.dumps(data), time.time() + CONSENT_TTL))
        return f"{self.issuer}/oauth/consent?{urlencode({'request': rid})}"

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        with db.connect() as conn:
            r = conn.execute("SELECT data, expires_at FROM oauth_codes WHERE code_hash=?",
                             (sha256(authorization_code),)).fetchone()
        if not r or r["expires_at"] < time.time():
            return None
        d = json.loads(r["data"])
        if d["client_id"] != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code, scopes=d["scopes"], expires_at=r["expires_at"],
                                 client_id=d["client_id"], code_challenge=d["code_challenge"],
                                 redirect_uri=d["redirect_uri"],
                                 redirect_uri_provided_explicitly=d["redirect_uri_provided_explicitly"],
                                 resource=d.get("resource"), subject="owner")

    def _token_pair(self, conn, client_id: str, scopes: list[str], resource: str | None, family: str) -> OAuthToken:
        s = get_settings()
        access, _ = _issue(conn, "access", client_id, scopes, ttl=s.oauth_access_ttl, family=family, resource=resource)
        refresh, _ = _issue(conn, "refresh", client_id, scopes, ttl=s.oauth_refresh_ttl, family=family, resource=resource)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=s.oauth_access_ttl,
                          scope=" ".join(scopes), refresh_token=refresh)

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code) -> OAuthToken:
        with db.connect() as conn, db.transaction(conn):
            gone = conn.execute("DELETE FROM oauth_codes WHERE code_hash=?", (sha256(authorization_code.code),)).rowcount
            if not gone:
                raise TokenError("invalid_grant", "Authorization code already used")
            _ensure_agent(conn, client.client_id, client.client_name)
            tok = self._token_pair(conn, client.client_id, authorization_code.scopes, authorization_code.resource,
                                   uuid.uuid4().hex)
        with db.connect() as conn:
            record_event(conn, "oauth.token", details={"client_id": client.client_id,
                                                       "scopes": authorization_code.scopes}, actor=client.client_id)
        return tok

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        h = sha256(refresh_token)
        with db.connect() as conn:
            r = conn.execute("SELECT * FROM tokens WHERE token_hash=? AND kind='refresh'", (h,)).fetchone()
            if not r or r["client_id"] != client.client_id:
                return None
            if r["revoked_at"]:
                # A rotated refresh token was replayed: assume theft, kill the whole family.
                conn.execute("UPDATE tokens SET revoked_at=? WHERE family=? AND revoked_at IS NULL", (now(), r["family"]))
                record_event(conn, "oauth.refresh_reuse", "blocked", {"client_id": client.client_id}, actor=client.client_id)
                return None
            if r["expires_at"] and r["expires_at"] < time.time():
                return None
        return RefreshToken(token=refresh_token, client_id=r["client_id"], scopes=r["scopes"].split(),
                            expires_at=int(r["expires_at"]) if r["expires_at"] else None, resource=r["resource"],
                            subject="owner")

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token, scopes: list[str]) -> OAuthToken:
        granted = refresh_token.scopes
        if scopes and not set(scopes) <= set(granted):
            raise TokenError("invalid_scope", "Cannot widen scopes on refresh")
        with db.connect() as conn, db.transaction(conn):
            r = conn.execute("SELECT family FROM tokens WHERE token_hash=?", (sha256(refresh_token.token),)).fetchone()
            family = r["family"] if r else uuid.uuid4().hex
            conn.execute("UPDATE tokens SET revoked_at=? WHERE family=? AND revoked_at IS NULL", (now(), family))
            return self._token_pair(conn, client.client_id, scopes or granted, refresh_token.resource, family)

    async def load_access_token(self, token: str) -> AccessToken | None:
        return verify_bearer(token)

    async def revoke_token(self, token) -> None:
        h = sha256(token.token)
        with db.connect() as conn:
            r = conn.execute("SELECT family FROM tokens WHERE token_hash=?", (h,)).fetchone()
            if r and r["family"]:
                conn.execute("UPDATE tokens SET revoked_at=? WHERE family=? AND revoked_at IS NULL", (now(), r["family"]))
            else:
                conn.execute("UPDATE tokens SET revoked_at=? WHERE token_hash=?", (now(), h))

    async def exchange_identity_assertion(self, client, params):  # pragma: no cover - not supported
        raise TokenError("unsupported_grant_type", "Identity assertion grants are not supported")


# --------------------------------------------------------------------------- consent page

def load_pending(request_id: str) -> dict:
    with db.connect() as conn:
        r = conn.execute("SELECT data, expires_at FROM oauth_pending WHERE id=?", (request_id,)).fetchone()
    if not r or r["expires_at"] < time.time():
        raise KOSError("This authorization request expired. Start the connection again from your MCP client.", status=400)
    return json.loads(r["data"])


def complete_consent(request_id: str, password: str, ip: str, allow_write: bool) -> str:
    """Verify the owner password and return the redirect URL carrying the authorization code."""
    data = load_pending(request_id)
    auth.verify_password_rate_limited(password, ip, purpose="oauth_consent")
    scopes = [s for s in data["scopes"] if s in VALID_SCOPES] or DEFAULT_SCOPES
    if not allow_write:
        scopes = [READ]
    code = secrets.token_urlsafe(32)
    stored = {**data, "scopes": scopes}
    with db.connect() as conn, db.transaction(conn):
        conn.execute("DELETE FROM oauth_pending WHERE id=?", (request_id,))
        conn.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (time.time(),))
        conn.execute("INSERT INTO oauth_codes(code_hash,data,expires_at) VALUES(?,?,?)",
                     (sha256(code), json.dumps(stored), time.time() + CODE_TTL))
    with db.connect() as conn:
        record_event(conn, "oauth.consent", details={"client_id": data["client_id"], "scopes": scopes}, actor="web")
    params = {"code": code}
    if data.get("state"):
        params["state"] = data["state"]
    sep = "&" if "?" in data["redirect_uri"] else "?"
    return data["redirect_uri"] + sep + urlencode(params)


def deny_consent(request_id: str) -> str:
    data = load_pending(request_id)
    with db.connect() as conn:
        conn.execute("DELETE FROM oauth_pending WHERE id=?", (request_id,))
    params = {"error": "access_denied"}
    if data.get("state"):
        params["state"] = data["state"]
    sep = "&" if "?" in data["redirect_uri"] else "?"
    return data["redirect_uri"] + sep + urlencode(params)


def consent_page(request_id: str, error: str = "") -> str:
    try:
        data = load_pending(request_id)
    except KOSError as e:
        return _page("Authorization expired", f"<p>{html.escape(e.message)}</p>")
    e = html.escape
    name = e(data.get("client_name") or "An MCP client")
    redirect = e(data["redirect_uri"].split("?")[0])
    wants_write = any(s in (WRITE, FULL) for s in data["scopes"])
    err = f"<div class='err'>{e(error)}</div>" if error else ""
    write_box = ("<label class='chk'><input type='checkbox' name='allow_write' value='1' checked> "
                 "Allow it to create and edit notes and memories</label>") if wants_write else ""
    body = f"""
      <p><b>{name}</b> wants to connect to your Knowledge OS.</p>
      <ul><li>Read and search your notes, graph and memories</li>{'<li>Create and edit notes and memories (changes are versioned; deletions go to trash)</li>' if wants_write else ''}</ul>
      <p class='muted'>After approval it returns to <code>{redirect}</code></p>
      <form method='post'>
        <input type='hidden' name='request' value='{e(request_id)}'>
        {write_box}
        <label for='pw'>Your Knowledge OS password</label>
        <input id='pw' name='password' type='password' autocomplete='current-password' required autofocus>
        {err}
        <div class='row'><button name='action' value='deny' class='ghost' formnovalidate>Deny</button>
        <button name='action' value='approve'>Approve</button></div>
      </form>"""
    return _page("Authorize access", body)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · Knowledge OS</title><meta name=referrer content=no-referrer>
<style>:root{{color-scheme:light dark;--bg:#f6f5f2;--fg:#1d1d1b;--mut:#6b6a66;--card:#fff;--line:#e4e2dc;--acc:#3b5bdb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121212;--fg:#ecebe8;--mut:#9a9893;--card:#1b1b1a;--line:#2c2c2a;--acc:#7c94ff}}}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;padding:20px}}
main{{width:min(440px,100%);background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px}}
h1{{font-size:20px;margin:0 0 12px}}.muted{{color:var(--mut);font-size:13px}}code{{font-size:12px;word-break:break-all}}
label{{display:block;font-size:13px;font-weight:600;margin:16px 0 6px}}.chk{{font-weight:400;display:flex;gap:8px;align-items:flex-start}}
input[type=password]{{width:100%;height:42px;border:1px solid var(--line);border-radius:8px;padding:0 12px;background:transparent;color:inherit;font-size:16px}}
.row{{display:flex;gap:10px;margin-top:18px}}button{{flex:1;height:42px;border:0;border-radius:8px;background:var(--acc);color:#fff;font-weight:600;font-size:15px;cursor:pointer}}
button.ghost{{background:transparent;color:var(--fg);border:1px solid var(--line)}}.err{{color:#d6336c;font-size:13px;margin-top:8px}}ul{{padding-left:18px}}</style>
<main><div class=muted>Knowledge OS</div><h1>{html.escape(title)}</h1>{body}</main></html>"""
