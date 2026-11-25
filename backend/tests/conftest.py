import hashlib
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

PASSWORD = "correct horse battery"


class FakeEmbedder:
    """Deterministic bag-of-words embedder so semantic paths run offline."""

    name = "fake:bow"
    available = True
    sim_floor = 0.2
    dim = 256

    def embed(self, texts, *, query=False):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"\w+", t.lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
        n = np.linalg.norm(out, axis=1, keepdims=True)
        n[n == 0] = 1
        return out / n


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KOS_EMBEDDINGS", "none")
    monkeypatch.setenv("KOS_AI_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "static-write-token")
    monkeypatch.setenv("MCP_READ_TOKEN", "static-read-token")
    monkeypatch.setenv("KOS_BACKGROUND_WORKERS", "false")
    monkeypatch.delenv("KNOWLEDGE_OS_GATE_HASH", raising=False)
    monkeypatch.delenv("KNOWLEDGE_OS_GATE_SALT", raising=False)
    from knowledge_os import ai, config, db, embeddings
    config.reset_settings()
    db.reset_schema_cache()
    ai.set_gateway(None)
    embeddings.set_embedder(None)
    db.ensure_schema()
    yield config.get_settings()
    config.reset_settings()
    db.reset_schema_cache()
    ai.set_gateway(None)
    embeddings.set_embedder(None)


@pytest.fixture()
def fake_embeddings(env):
    from knowledge_os import embeddings
    embeddings.set_embedder(FakeEmbedder())
    yield
    embeddings.set_embedder(None)


@pytest.fixture()
def client(env):
    from knowledge_os import auth
    from knowledge_os.web import create_app
    auth.set_password(PASSWORD)
    return TestClient(create_app())


@pytest.fixture()
def authed(client):
    r = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"X-KOS": "1"})
    assert r.status_code == 200, r.text
    client.headers.update({"X-KOS": "1"})
    return client
