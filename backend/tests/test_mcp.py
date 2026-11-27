import asyncio
import base64
import hashlib
import json
import secrets
import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn

from tests.conftest import PASSWORD


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def mcp_server(env, monkeypatch):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("KOS_MCP_PUBLIC_BASE", base)
    from knowledge_os import auth, config
    config.reset_settings()
    auth.set_password(PASSWORD)
    from knowledge_os.mcp_app import build_mcp_app
    server = uvicorn.Server(uvicorn.Config(build_mcp_app(), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            if httpx.get(base + "/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield base
    server.should_exit = True
    t.join(timeout=5)


async def _session(base, token, fn):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
    async with create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http:
        async with streamable_http_client(base + "/mcp", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                return await fn(s)


def run(base, token, fn):
    return asyncio.run(_session(base, token, fn))


def test_unauthenticated_rejected(mcp_server):
    r = httpx.post(mcp_server + "/mcp", json={})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")


def test_static_tokens_and_scopes(mcp_server):
    async def write(s):
        r = await s.call_tool("create_note", {"path": "Inbox/hi", "content": "hello [[World]]"})
        r2 = await s.call_tool("search_knowledge", {"query": "hello"})  # legacy alias
        return r, r2

    r, r2 = run(mcp_server, "static-write-token", write)
    assert not r.is_error and not r2.is_error

    async def read_only(s):
        tools = await s.list_tools()
        bad = await s.call_tool("create_note", {"path": "Inbox/nope", "content": "x"})
        good = await s.call_tool("read_note", {"path": "Inbox/hi"})
        return tools, bad, good

    tools, bad, good = run(mcp_server, "static-read-token", read_only)
    names = {t.name for t in tools.tools}
    assert {"brief", "recall", "remember", "revise", "forget", "log", "search_knowledge"} <= names
    assert bad.is_error and "scope" in bad.content[0].text
    assert json.loads(good.content[0].text)["content"].startswith("hello")


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _oauth(base, allow_write=True, password=PASSWORD):
    c = httpx.Client(base_url=base)
    reg = c.post("/register", json={"redirect_uris": ["http://localhost:9999/cb"], "client_name": "Test Client",
                                    "token_endpoint_auth_method": "client_secret_post"}).json()
    verifier, challenge = _pkce()
    r = c.get("/authorize", params={"response_type": "code", "client_id": reg["client_id"],
                                    "redirect_uri": "http://localhost:9999/cb", "code_challenge": challenge,
                                    "code_challenge_method": "S256", "state": "st<script>",
                                    "scope": "knowledge:read knowledge:write"})
    assert r.status_code in (302, 303, 307)
    consent_url = r.headers["location"]
    rid = parse_qs(urlparse(consent_url).query)["request"][0]
    page = c.get(consent_url).text
    assert "Test Client" in page and "<script>" not in page
    form = {"request": rid, "password": password, "action": "approve"}
    if allow_write:
        form["allow_write"] = "1"
    r = c.post("/oauth/consent", data=form)
    return c, reg, verifier, r


def test_oauth_flow_refresh_rotation_and_revocation(mcp_server):
    c, reg, verifier, r = _oauth(mcp_server)
    assert r.status_code == 303
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["state"] == ["st<script>"]
    tok = c.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0],
                                 "redirect_uri": "http://localhost:9999/cb", "client_id": reg["client_id"],
                                 "client_secret": reg["client_secret"], "code_verifier": verifier}).json()
    assert tok["refresh_token"] and "knowledge:write" in tok["scope"]
    # code is single-use
    again = c.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0],
                                   "redirect_uri": "http://localhost:9999/cb", "client_id": reg["client_id"],
                                   "client_secret": reg["client_secret"], "code_verifier": verifier})
    assert again.status_code == 400

    async def act(s):
        return await s.call_tool("remember", {"subject": "Profile", "fact": "Works with an agent named Hermes"})
    assert not run(mcp_server, tok["access_token"], act).is_error

    # refresh rotates; replaying the old refresh token kills the family
    new = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                 "client_id": reg["client_id"], "client_secret": reg["client_secret"]}).json()
    assert new["access_token"] != tok["access_token"]
    replay = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                    "client_id": reg["client_id"], "client_secret": reg["client_secret"]})
    assert replay.status_code == 400
    from knowledge_os import oauth
    assert oauth.verify_bearer(new["access_token"]) is None

    # agent identity recorded
    creds = oauth.list_credentials()
    assert any(x["client_id"] == reg["client_id"] and x["activity"]["actions"] for x in creds)


def test_oauth_read_only_consent_and_wrong_password(mcp_server):
    c, reg, verifier, r = _oauth(mcp_server, allow_write=False)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    tok = c.post("/token", data={"grant_type": "authorization_code", "code": code,
                                 "redirect_uri": "http://localhost:9999/cb", "client_id": reg["client_id"],
                                 "client_secret": reg["client_secret"], "code_verifier": verifier}).json()
    assert tok["scope"] == "knowledge:read"

    c2, _, _, bad = _oauth(mcp_server, password="wrong")
    assert bad.status_code == 401 and "Incorrect password" in bad.text
