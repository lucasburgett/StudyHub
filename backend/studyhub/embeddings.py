"""Vector embeddings for semantic search, next to the FTS5 keyword index.

Keyword search misses paraphrases: a question about "the chain rule trick" should find a
lecture that said "backpropagation", and spoken transcripts rarely use the words a student
types. Each chunk is embedded once, with its header ("CS 231N · Lecture 3 · recording · 41:12")
in front so the vector knows where the text came from.

Providers, picked by STUDYHUB_EMBEDDINGS (auto by default):
- voyage: Voyage AI's API (VOYAGE_API_KEY). The voyage-4 series has 200M free tokens per account.
- local:  a small model run on your machine via fastembed (`pip install -e ".[local-embeddings]"`).
- off:    keyword search only.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from functools import lru_cache
from typing import Protocol

import httpx
import numpy as np

from .config import Settings, get_settings
from .db import get_meta, set_meta

log = logging.getLogger("studyhub.embeddings")

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
BATCH = 64
MAX_CHARS = 8000


class Embedder(Protocol):
    name: str  # stored next to each vector; changing it re-embeds everything

    def documents(self, texts: list[str]) -> np.ndarray: ...

    def query(self, text: str) -> np.ndarray: ...


def _normalize(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.where(norms == 0, 1, norms)


class VoyageEmbedder:
    def __init__(self, api_key: str, model: str, transport: httpx.BaseTransport | None = None):
        self.name = f"voyage:{model}"
        self.model = model
        self.http = httpx.Client(headers={"Authorization": f"Bearer {api_key}"}, timeout=60, transport=transport)

    def _embed(self, texts: list[str], input_type: str) -> np.ndarray:
        for attempt in range(5):
            resp = self.http.post(VOYAGE_URL, json={"input": texts, "model": self.model, "input_type": input_type})
            if resp.status_code != 429:
                break
            time.sleep(2 ** attempt)
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return _normalize(np.array([d["embedding"] for d in data]))

    def documents(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts, "document")

    def query(self, text: str) -> np.ndarray:
        return self._embed([text], "query")[0]


class LocalEmbedder:
    def __init__(self, model: str = LOCAL_MODEL):
        from fastembed import TextEmbedding  # optional dependency

        self.name = f"local:{model}"
        self.model = TextEmbedding(model)

    def documents(self, texts: list[str]) -> np.ndarray:
        return _normalize(np.stack(list(self.model.passage_embed(texts))))

    def query(self, text: str) -> np.ndarray:
        return _normalize(next(iter(self.model.query_embed(text))))


@lru_cache(maxsize=4)
def _cached(mode: str, voyage_key: str, voyage_model: str) -> Embedder | None:
    if mode == "off":
        return None
    if mode in ("auto", "voyage") and voyage_key:
        return VoyageEmbedder(voyage_key, voyage_model)
    if mode in ("auto", "local"):
        try:
            return LocalEmbedder()
        except ImportError:
            if mode == "local":
                log.warning("STUDYHUB_EMBEDDINGS=local needs `pip install -e \".[local-embeddings]\"`.")
        except Exception as e:  # e.g. the model can't be downloaded while offline; retried on restart
            log.warning("Local embedding model unavailable, using keyword search only: %s", e)
    return None


def get_embedder(settings: Settings | None = None) -> Embedder | None:
    settings = settings or get_settings()
    return _cached(settings.studyhub_embeddings, settings.voyage_api_key, settings.voyage_model)


def index_embeddings(conn: sqlite3.Connection, embedder: Embedder, limit: int | None = None) -> int:
    """Embed every chunk that has no vector from this embedder yet. Returns how many were embedded."""
    rows = conn.execute(
        "SELECT id, header, text FROM chunks WHERE embed_model IS NULL OR embed_model != ? ORDER BY id"
        + (" LIMIT ?" if limit else ""),
        (embedder.name, limit) if limit else (embedder.name,),
    ).fetchall()
    done = 0
    for start in range(0, len(rows), BATCH):
        batch = rows[start:start + BATCH]
        vectors = embedder.documents([f"{r['header']}\n{r['text']}"[:MAX_CHARS] for r in batch])
        conn.executemany(
            "UPDATE chunks SET embedding = ?, embed_model = ? WHERE id = ?",
            [(v.astype(np.float32).tobytes(), embedder.name, r["id"]) for r, v in zip(batch, vectors)],
        )
        done += len(batch)
        set_meta(conn, "embeddings_version", str(int(get_meta(conn, "embeddings_version", "0") or 0) + 1))
        conn.commit()
    return done


class _Matrix:
    def __init__(self, version: str, ids: np.ndarray, courses: np.ndarray, sources: np.ndarray,
                 kinds: np.ndarray, vectors: np.ndarray):
        self.version, self.ids, self.courses, self.sources, self.kinds, self.vectors = \
            version, ids, courses, sources, kinds, vectors


_matrices: dict[tuple[str, str], _Matrix] = {}


def _matrix(conn: sqlite3.Connection, model: str) -> _Matrix | None:
    """All vectors for a model, cached until the next indexing run changes them."""
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    version = get_meta(conn, "embeddings_version", "0") or "0"
    cached = _matrices.get((db, model))
    if cached and cached.version == version:
        return cached
    rows = conn.execute(
        "SELECT c.id, c.embedding, r.course_id, r.source, r.kind FROM chunks c JOIN resources r ON r.id = c.resource_id"
        " WHERE c.embed_model = ? AND c.embedding IS NOT NULL",
        (model,),
    ).fetchall()
    if not rows:
        return None
    m = _Matrix(
        version,
        np.array([r["id"] for r in rows]),
        np.array([r["course_id"] for r in rows]),
        np.array([r["source"] for r in rows]),
        np.array([r["kind"] for r in rows]),
        np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]),
    )
    _matrices[(db, model)] = m
    return m


def nearest(conn: sqlite3.Connection, embedder: Embedder, text: str, *, course_id: int | None = None,
            sources: list[str] | None = None, kinds: list[str] | None = None, k: int = 40) -> list[int]:
    """Chunk ids closest to the query, best first."""
    m = _matrix(conn, embedder.name)
    if m is None:
        return []
    mask = np.ones(len(m.ids), dtype=bool)
    if course_id is not None:
        mask &= m.courses == course_id
    if sources:
        mask &= np.isin(m.sources, sources)
    if kinds:
        mask &= np.isin(m.kinds, kinds)
    if not mask.any():
        return []
    scores = m.vectors[mask] @ embedder.query(text)
    ids = m.ids[mask]
    top = np.argsort(-scores)[:k]
    return [int(ids[i]) for i in top]
