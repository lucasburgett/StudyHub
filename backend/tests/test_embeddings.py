import json
import sqlite3

import httpx
import numpy as np

from studyhub.agent.tools import Toolbox
from studyhub.db import init_db
from studyhub.embeddings import VoyageEmbedder, index_embeddings
from studyhub.search import hybrid_search, search

# Words that mean the same thing share a dimension, so the fake embedder "understands" paraphrase.
CONCEPTS = [
    {"backpropagation", "backprop", "chain", "gradient", "gradients", "derivative", "derivatives"},
    {"softmax", "cross", "entropy", "probability", "probabilities"},
    {"regularization", "overfitting", "weight", "decay", "l2"},
    {"neighbor", "neighbors", "knn", "nearest", "distance"},
    {"convolution", "filter", "filters", "conv", "pooling"},
]


class FakeEmbedder:
    name = "fake:concepts"

    def __init__(self):
        self.calls = 0

    def _vec(self, text):
        words = {w.strip(".,:;()[]").lower() for w in text.split()}
        v = np.array([len(words & c) for c in CONCEPTS], dtype=np.float32) + 1e-3
        return v / np.linalg.norm(v)

    def documents(self, texts):
        self.calls += len(texts)
        return np.stack([self._vec(t) for t in texts])

    def query(self, text):
        return self._vec(text)


def test_index_is_incremental(demo):
    emb = FakeEmbedder()
    total = demo.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert index_embeddings(demo, emb) == total
    assert index_embeddings(demo, emb) == 0
    demo.execute("UPDATE chunks SET text = 'changed', embed_model = NULL WHERE id = 1")
    assert index_embeddings(demo, emb) == 1


def test_hybrid_finds_paraphrases_keyword_search_misses(conn):
    from studyhub.store import ChunkIn, ensure_course, upsert_resource

    cid = ensure_course(conn, "CS 231N")
    upsert_resource(conn, course_id=cid, source="granola", kind="transcript", external_id="a", title="Lecture 5",
                    chunks=[ChunkIn("The chain rule lets us push gradients back node by node.", seconds=0)])
    upsert_resource(conn, course_id=cid, source="canvas", kind="slides", external_id="b", title="CNNs",
                    chunks=[ChunkIn("Max pooling halves width and height.", page=1)])
    index_embeddings(conn, FakeEmbedder())

    assert search(conn, "derivative", limit=5) == []  # no shared words
    hits = hybrid_search(conn, "derivative", FakeEmbedder(), limit=5)
    assert hits[0].title == "Lecture 5" and hits[0].seconds == 0
    assert "chain rule" in hits[0].snippet
    only_slides = hybrid_search(conn, "derivative", FakeEmbedder(), kinds=["slides"], limit=5)
    assert [h.title for h in only_slides] == ["CNNs"]  # filters apply to vector results too


def test_agent_search_uses_the_embedder(demo):
    emb = FakeEmbedder()
    index_embeddings(demo, emb)
    box = Toolbox(demo, embedder=emb)
    result = box.run("search", box.parse("search", {"query": "chain rule derivatives"}))
    assert "Lecture 5" in result.text


def test_voyage_request_shape():
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append((request.headers["authorization"], body))
        data = [{"index": i, "embedding": [1.0, float(i)]} for i in range(len(body["input"]))]
        return httpx.Response(200, json={"data": list(reversed(data))})

    emb = VoyageEmbedder("vk-test", "voyage-4", transport=httpx.MockTransport(handler))
    vecs = emb.documents(["a", "b"])
    assert emb.name == "voyage:voyage-4"
    assert seen[0][0] == "Bearer vk-test"
    assert seen[0][1] == {"input": ["a", "b"], "model": "voyage-4", "input_type": "document"}
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1) and vecs[0][1] == 0  # sorted by index, normalized
    emb.query("q")
    assert seen[1][1]["input_type"] == "query"


def test_migration_adds_columns_to_old_databases(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, resource_id INTEGER, seq INTEGER, page INTEGER,"
                 " seconds INTEGER, lecture_id INTEGER, header TEXT, text TEXT, image_hash TEXT,"
                 " transcribed INTEGER NOT NULL DEFAULT 0)")
    conn.execute("CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN SELECT 1; END")
    init_db(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(chunks)")}
    assert {"embedding", "embed_model"} <= cols
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'chunks_au'").fetchone()["sql"]
    assert "UPDATE OF header, text" in sql


def test_search_falls_back_when_embedding_fails(demo):
    class Broken(FakeEmbedder):
        def query(self, text):
            raise httpx.ConnectError("offline")

    index_embeddings(demo, Broken())
    hits = hybrid_search(demo, "softmax", Broken(), limit=3)
    assert hits and "softmax" in hits[0].snippet.lower()  # keyword results still come back
