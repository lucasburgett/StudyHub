import httpx

from studyhub.config import get_settings
from studyhub.connectors.base import SyncContext
from studyhub.connectors.granola import GranolaConnector
from studyhub.sync import rebuild_course


def _transcript(n: int):
    return [{"speaker": {"source": "speaker"}, "text": f"Sentence {i} about backpropagation.",
             "start_time": f"2026-09-29T17:{30 + i // 60:02d}:{i % 60:02d}Z",
             "end_time": f"2026-09-29T17:{30 + (i + 5) // 60:02d}:{(i + 5) % 60:02d}Z"} for i in range(0, n, 5)]


class FakeGranola:
    def __init__(self):
        self.transcript = _transcript(600)
        self.updated = "2026-09-29T19:00:00Z"
        self.too_large = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer grn_test"
        path, q = request.url.path.removeprefix("/v1"), request.url.params
        if path == "/folders":
            return httpx.Response(200, json={"folders": [
                {"id": "fol_1", "object": "folder", "name": "CS 231N", "parent_folder_id": None},
                {"id": "fol_2", "object": "folder", "name": "1:1s", "parent_folder_id": None},
            ], "hasMore": False, "cursor": None})
        if path == "/notes":
            assert q["folder_id"] == "fol_1"
            return httpx.Response(200, json={"notes": [
                {"id": "not_a", "object": "note", "title": "CS 231N", "owner": {"name": None, "email": "x@y"},
                 "created_at": "2026-09-29T17:30:00Z", "updated_at": self.updated},
            ], "hasMore": False, "cursor": None})
        if path == "/notes/not_a":
            if self.too_large and q.get("include") == "transcript":
                return httpx.Response(413, json={"error": "TRANSCRIPT_TOO_LARGE"})
            note = {"id": "not_a", "title": "CS 231N", "web_url": "https://notes.granola.ai/d/1",
                    "created_at": "2026-09-29T17:30:00Z", "updated_at": self.updated,
                    "calendar_event": {"scheduled_start_time": "2026-09-29T17:30:00Z"},
                    "summary_markdown": "## Backprop\nChain rule on graphs.", "summary_text": "Backprop",
                    "transcript": None if self.too_large else self.transcript}
            return httpx.Response(200, json=note)
        if path == "/notes/not_a/transcript":
            half = len(self.transcript) // 2
            if q.get("cursor") == "c2":
                return httpx.Response(200, json={"transcript": self.transcript[half:], "hasMore": False, "cursor": None})
            return httpx.Response(200, json={"transcript": self.transcript[:half], "hasMore": True, "cursor": "c2"})
        return httpx.Response(404)


def _sync(conn, fake):
    import os

    os.environ["GRANOLA_API_KEY"] = "grn_test"  # conftest resets this per test
    get_settings.cache_clear()
    ctx = SyncContext(conn=conn, settings=get_settings())
    GranolaConnector(transport=httpx.MockTransport(fake)).sync(ctx)
    for cid in ctx.touched_courses:
        rebuild_course(conn, cid)
    return ctx


def test_granola_sync_and_lecture_date(conn, monkeypatch):
    monkeypatch.setattr("studyhub.connectors.granola.time.sleep", lambda s: None)
    _sync(conn, FakeGranola())
    r = conn.execute("SELECT * FROM resources").fetchone()
    assert (r["kind"], r["duration_min"], r["url"]) == ("transcript", 10, "https://notes.granola.ai/d/1")
    assert r["summary"].startswith("## Backprop")
    seconds = [c["seconds"] for c in conn.execute("SELECT seconds FROM chunks ORDER BY seq")]
    assert seconds[0] == 0 and seconds == sorted(seconds) and len(seconds) >= 4
    lec = conn.execute("SELECT * FROM lectures").fetchone()
    assert lec["date"] == "2026-09-29"  # 10:30 Pacific
    assert conn.execute("SELECT granola_folder_id FROM courses").fetchone()[0] == "fol_1"


def test_granola_large_transcript_is_paged(conn, monkeypatch):
    monkeypatch.setattr("studyhub.connectors.granola.time.sleep", lambda s: None)
    fake = FakeGranola()
    fake.too_large = True
    _sync(conn, fake)
    text = " ".join(c["text"] for c in conn.execute("SELECT text FROM chunks ORDER BY seq"))
    assert text.count("Sentence") == len(fake.transcript)


def test_granola_keeps_transcript_deleted_upstream(conn, monkeypatch):
    monkeypatch.setattr("studyhub.connectors.granola.time.sleep", lambda s: None)
    fake = FakeGranola()
    _sync(conn, fake)
    before = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    fake.transcript = []
    fake.updated = "2026-10-30T00:00:00Z"
    _sync(conn, fake)
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == before
