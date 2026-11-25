"""Embeddings: local by default (fastembed / ONNX, no API cost), optional OpenAI, or none.

Vectors are keyed by chunk content hash, so unchanged chunks are never
re-embedded, renames are free and a full reindex costs nothing.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Protocol

import numpy as np

from . import db
from .config import get_settings

log = logging.getLogger("kos.embeddings")

BATCH = 32


class Embedder(Protocol):
    name: str
    available: bool

    def embed(self, texts: list[str], *, query: bool = False) -> np.ndarray: ...


class NullEmbedder:
    name = "none"
    available = False
    sim_floor = 1.0

    def embed(self, texts, *, query=False):  # pragma: no cover
        raise RuntimeError("Embeddings are disabled")


class LocalEmbedder:
    """fastembed with a small ONNX model. Loaded lazily on first use."""

    sim_floor = 0.55  # bge-small: unrelated text scores ~0.40-0.55

    def __init__(self, model: str):
        self.name = f"local:{model}"
        self.model_name = model
        self._model = None
        self._error: str | None = None
        self._lock = threading.Lock()
        try:
            import fastembed  # noqa: F401
            self.available = True
        except ImportError:
            self.available = False
            self._error = "fastembed is not installed"

    def is_ready(self) -> bool:
        return self._model is not None

    def warm_async(self) -> None:
        """Load the model in the background so user requests never wait on a download."""
        if self._model is None and not getattr(self, "_warming", False):
            self._warming = True
            threading.Thread(target=self._safe_load, name="kos-embed-load", daemon=True).start()

    def _safe_load(self):
        try:
            self._load()
        except Exception as e:  # surfaced via status()
            _status["error"] = f"model load failed: {e}"[:300]
        finally:
            self._warming = False

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                cache = get_settings().models_dir
                cache.mkdir(parents=True, exist_ok=True)
                log.info("Loading local embedding model %s", self.model_name)
                self._model = TextEmbedding(model_name=self.model_name, cache_dir=str(cache))
        return self._model

    def embed(self, texts, *, query=False):
        model = self._load()
        if query and hasattr(model, "query_embed"):
            vecs = list(model.query_embed(texts))
        else:
            vecs = list(model.embed(texts, batch_size=BATCH))
        return _normalize(np.asarray(vecs, dtype=np.float32))


class OpenAIEmbedder:
    sim_floor = 0.30

    def __init__(self, model: str):
        self.name = f"openai:{model}"
        self.model_name = model
        s = get_settings()
        self.available = bool(s.ai_api_key)

    def embed(self, texts, *, query=False):
        from .ai import get_gateway
        vecs = get_gateway().embed(texts, model=self.model_name, operation="embed.query" if query else "embed.index")
        return _normalize(np.asarray(vecs, dtype=np.float32))


def _normalize(m: np.ndarray) -> np.ndarray:
    if m.ndim == 1:
        m = m[None, :]
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


_embedder: Embedder | None = None
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                s = get_settings()
                kind = s.embeddings_provider.lower()
                if kind == "local":
                    _embedder = LocalEmbedder(s.embeddings_model)
                elif kind == "openai":
                    _embedder = OpenAIEmbedder(s.openai_embeddings_model)
                else:
                    _embedder = NullEmbedder()
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Override the embedder (tests, or switching providers at runtime)."""
    global _embedder
    with _embedder_lock:
        _embedder = embedder
    _cache.invalidate()
    _status.update(error=None)


# --------------------------------------------------------------------------- vector cache

class _VectorCache:
    """In-memory matrix of chunk vectors for the active model, rebuilt when the index changes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self.ids: list[int] = []
        self.paths: list[str] = []
        self.matrix: np.ndarray | None = None

    def invalidate(self):
        with self._lock:
            self._key = None

    def get(self):
        emb = get_embedder()
        with db.connect() as conn:
            gen = db.get_meta(conn, "index_generation", "0")
            egen = db.get_meta(conn, "embedding_generation", "0")
            key = (emb.name, gen, egen)
            with self._lock:
                if self._key == key and self.matrix is not None:
                    return self.ids, self.paths, self.matrix
                rows = conn.execute(
                    "SELECT c.id, c.path, e.dim, e.vector FROM chunks c JOIN embeddings e "
                    "ON e.chunk_hash=c.hash AND e.model=? ORDER BY c.id", (emb.name,)).fetchall()
                if rows:
                    dim = rows[0]["dim"]
                    rows = [r for r in rows if r["dim"] == dim]
                    self.matrix = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
                else:
                    self.matrix = np.zeros((0, 1), dtype=np.float32)
                self.ids = [r["id"] for r in rows]
                self.paths = [r["path"] for r in rows]
                self._key = key
                return self.ids, self.paths, self.matrix


_cache = _VectorCache()


def relevant(hits: list[tuple[int, str, float]], gap: float = 0.12) -> list[tuple[int, str, float]]:
    """Drop weak matches: below the model's floor or far below the best hit."""
    if not hits:
        return hits
    floor = getattr(get_embedder(), "sim_floor", 0.4)
    best = hits[0][2]
    return [h for h in hits if h[2] >= floor and h[2] >= best - gap]


def vector_search(query: str, k: int = 30) -> list[tuple[int, str, float]]:
    """Return [(chunk_id, path, cosine)] for the nearest chunks, best first."""
    emb = get_embedder()
    if not emb.available:
        return []
    if hasattr(emb, "is_ready") and not emb.is_ready():
        emb.warm_async()   # keyword search answers meanwhile
        return []
    try:
        ids, paths, matrix = _cache.get()
        if matrix is None or matrix.shape[0] == 0:
            return []
        q = emb.embed([query], query=True)[0]
        if q.shape[0] != matrix.shape[1]:
            return []
        scores = matrix @ q
        top = np.argsort(-scores)[:k]
        return [(ids[i], paths[i], float(scores[i])) for i in top]
    except Exception as e:  # never let semantic search break lexical search
        log.warning("vector search failed: %s", e)
        _status["error"] = str(e)[:300]
        return []


def embed_texts(texts: list[str]) -> np.ndarray | None:
    emb = get_embedder()
    if not emb.available or not texts:
        return None
    if hasattr(emb, "is_ready") and not emb.is_ready():
        emb.warm_async()
        return None
    try:
        return emb.embed(texts)
    except Exception as e:
        _status["error"] = str(e)[:300]
        return None


# --------------------------------------------------------------------------- worker

_wake = threading.Event()
_status: dict = {"pending": 0, "embedded": 0, "error": None, "running": False, "last_run": None}


def notify() -> None:
    _wake.set()


def pending_count() -> int:
    emb = get_embedder()
    if not emb.available:
        return 0
    with db.connect() as conn:
        return conn.execute(
            "SELECT count(DISTINCT c.hash) FROM chunks c LEFT JOIN embeddings e ON e.chunk_hash=c.hash AND e.model=? "
            "WHERE e.chunk_hash IS NULL", (emb.name,)).fetchone()[0]


def embed_pending(limit: int = 512) -> int:
    """Embed chunks that have no vector for the active model. Returns how many were embedded."""
    emb = get_embedder()
    if not emb.available:
        return 0
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT c.hash, c.text, c.heading, n.title FROM chunks c JOIN notes n ON n.path=c.path "
            "LEFT JOIN embeddings e ON e.chunk_hash=c.hash AND e.model=? WHERE e.chunk_hash IS NULL "
            "GROUP BY c.hash LIMIT ?", (emb.name, limit)).fetchall()
    done = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        texts = [f"{r['title']}\n{r['heading']}\n{r['text']}".strip() for r in batch]
        vecs = emb.embed(texts)
        with db.connect() as conn, db.transaction(conn):
            for r, v in zip(batch, vecs):
                conn.execute("INSERT OR REPLACE INTO embeddings(chunk_hash,model,dim,vector) VALUES(?,?,?,?)",
                             (r["hash"], emb.name, int(v.shape[0]), v.astype(np.float32).tobytes()))
            db.set_meta(conn, "embedding_generation", str(time.time()))
        done += len(batch)
    return done


def gc_orphans() -> int:
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM embeddings WHERE chunk_hash NOT IN (SELECT hash FROM chunks)")
        return cur.rowcount


def status() -> dict:
    emb = get_embedder()
    out = dict(_status)
    out.update({"provider": emb.name, "available": emb.available})
    try:
        out["pending"] = pending_count()
    except Exception:
        pass
    return out


def worker_loop(stop: threading.Event) -> None:
    """Background thread: embed new chunks shortly after they are indexed."""
    while not stop.is_set():
        _wake.wait(timeout=60)
        _wake.clear()
        if stop.is_set():
            break
        if not get_embedder().available:
            continue
        try:
            _status["running"] = True
            while not stop.is_set():
                n = embed_pending()
                _status["embedded"] += n
                if n == 0:
                    break
            _status["error"] = None
        except Exception as e:
            log.warning("embedding worker error: %s", e)
            _status["error"] = str(e)[:300]
            stop.wait(30)
        finally:
            _status["running"] = False
            _status["last_run"] = time.time()
