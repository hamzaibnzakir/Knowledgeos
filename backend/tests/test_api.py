from knowledge_os import auth, retrieval
from tests.conftest import PASSWORD


def test_api_requires_session(client):
    assert client.get("/api/notes").status_code == 401
    assert client.get("/api/auth/status").json() == {"authenticated": False, "password_configured": True}


def test_login_logout_revokes_session(client):
    r = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"X-KOS": "1"})
    assert r.status_code == 200
    cookie = r.cookies.get(auth.SESSION_COOKIE)
    assert cookie and "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/notes").status_code == 200
    client.post("/api/auth/logout", headers={"X-KOS": "1"})
    client.cookies.set(auth.SESSION_COOKIE, cookie)  # replaying the old cookie must fail
    assert client.get("/api/notes").status_code == 401


def test_forged_cookie_rejected(client):
    client.cookies.set(auth.SESSION_COOKIE, "forged")
    assert client.get("/api/notes").status_code == 401


def test_login_rate_limit(client, env):
    for _ in range(env.login_max_failures):
        r = client.post("/api/auth/login", json={"password": "wrong"}, headers={"X-KOS": "1"})
    assert r.status_code in (401, 429)
    r = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"X-KOS": "1"})
    assert r.status_code == 429  # locked even with the right password


def test_csrf_header_required(authed):
    r = authed.post("/api/notes", json={"path": "a", "content": "x"}, headers={"X-KOS": ""})
    assert r.status_code == 403


def test_no_secrets_exposed(authed):
    text = authed.get("/api/mcp").text + authed.get("/api/system").text + authed.get("/api/agents").text
    assert "static-write-token" not in text and "static-read-token" not in text


def test_note_lifecycle_over_api(authed):
    r = authed.post("/api/notes", json={"path": "Notes/Hello", "content": "# Hello\nworld [[Other]]"})
    assert r.status_code == 200 and r.json()["path"] == "Notes/Hello.md"
    note = authed.get("/api/notes/read", params={"path": "Notes/Hello.md"}).json()
    r = authed.put("/api/notes", json={"path": "Notes/Hello.md", "content": "changed", "expected_hash": note["hash"]})
    assert r.status_code == 200
    r = authed.put("/api/notes", json={"path": "Notes/Hello.md", "content": "stale", "expected_hash": note["hash"]})
    assert r.status_code == 409 and "current_hash" in r.json()
    assert authed.get("/api/notes/versions", params={"path": "Notes/Hello.md"}).json()
    assert authed.post("/api/notes/move", json={"path": "Notes/Hello.md", "new_path": "Archive/Hello.md"}).status_code == 200
    r = authed.delete("/api/notes", params={"path": "Archive/Hello.md"})
    tid = r.json()["trash_id"]
    assert authed.get("/api/trash").json()[0]["id"] == tid
    assert authed.post(f"/api/trash/{tid}/restore").json()["path"] == "Archive/Hello.md"
    assert authed.get("/api/notes/read", params={"path": "../../etc/passwd"}).status_code == 400


def test_search_snippets_are_marked_not_html(authed):
    authed.post("/api/notes", json={"path": "x", "content": "<img src=x onerror=alert(1)> payload"})
    hits = authed.get("/api/search", params={"q": "payload"}).json()
    assert hits and retrieval.HL_START in hits[0]["snippet"]
    assert "<mark>" not in hits[0]["snippet"]


def test_semantic_search(authed, fake_embeddings):
    from knowledge_os import embeddings
    authed.post("/api/notes", json={"path": "Notes/Budget", "content": "increase daily budget on winning campaigns"})
    authed.post("/api/notes", json={"path": "Notes/Tea", "content": "green tea brewing temperature"})
    embeddings.embed_pending()
    hits = authed.get("/api/search", params={"q": "winning campaigns budget", "mode": "semantic"}).json()
    assert hits[0]["path"] == "Notes/Budget.md"


def test_memory_api(authed):
    r = authed.post("/api/memory/facts", json={"subject": "Profile", "fact": "Prefers dark mode"}).json()
    assert r["status"] == "saved" and r["review"] == "ok"
    assert authed.post("/api/memory/facts", json={"subject": "Profile", "fact": "prefers dark mode."}).json()["status"] == "exists"
    fid = r["fact_id"]
    assert authed.get("/api/memory/facts", params={"path": "Memory/Profile.md"}).json()[0]["id"] == fid
    edited = authed.patch(f"/api/memory/facts/{fid}", json={"fact": "Prefers dark mode everywhere"}).json()
    assert edited["status"] == "saved"
    hist = authed.get("/api/memory/facts", params={"status": "superseded"}).json()
    assert hist[0]["id"] == fid and hist[0]["replaced_by"] == edited["fact_id"]
    authed.post(f"/api/memory/facts/{edited['fact_id']}/forget", json={"reason": "changed mind"})
    assert authed.get("/api/memory/facts", params={"path": "Memory/Profile.md"}).json() == []
    subjects = {s["subject"] for s in authed.get("/api/memory/subjects").json()}
    assert {"Profile", "Rules"} <= subjects
    assert "Brief for" in authed.get("/api/memory/brief").json()["brief"]


def test_import_file_and_link_guard(authed):
    r = authed.post("/api/import/file", files={"file": ("brief.txt", b"Client wants a new landing page", "text/plain")})
    assert r.status_code == 200 and r.json()["path"] == "Inbox/brief.md"
    assert authed.post("/api/import/link", json={"url": "http://127.0.0.1:1/x"}).status_code == 400


def test_agent_tokens(authed):
    r = authed.post("/api/agents/tokens", json={"label": "Hermes", "scopes": ["knowledge:read", "knowledge:write"]}).json()
    assert r["token"].startswith("kos_")
    from knowledge_os import oauth
    assert oauth.verify_bearer(r["token"]).client_id == r["client_id"]
    listed = authed.get("/api/agents").json()
    assert any(a["client_id"] == r["client_id"] and "token" not in a for a in listed)
    authed.post(f"/api/agents/{r['client_id']}/revoke")
    assert oauth.verify_bearer(r["token"]) is None


def test_backup_and_export(authed):
    authed.post("/api/notes", json={"path": "a", "content": "x"})
    b = authed.post("/api/backups").json()
    assert b["name"].startswith("kos-backup-")
    assert authed.get(f"/api/backups/{b['name']}").status_code == 200
    assert authed.get("/api/backups/..%2Fknowledge.db").status_code == 404
    assert authed.get("/api/export").headers["content-type"] == "application/zip"


def test_graph_endpoints(authed):
    authed.post("/api/notes", json={"path": "A", "content": "[[B]] #topic"})
    authed.post("/api/notes", json={"path": "B", "content": "back to [[A]]"})
    authed.post("/api/notes", json={"path": "C", "content": "alone"})
    g = authed.get("/api/graph", params={"tags": True}).json()
    assert {"A.md", "B.md", "C.md", "tag:topic"} <= {n["id"] for n in g["nodes"]}
    assert [o["path"] for o in authed.get("/api/graph/orphans").json()] == ["C.md"]
    local = authed.get("/api/graph/local", params={"path": "A.md"}).json()
    assert {n["id"] for n in local["nodes"]} == {"A.md", "B.md"}
