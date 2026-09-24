import json

import httpx
import pymupdf

from studyhub.config import get_settings
from studyhub.connectors.base import SyncContext
from studyhub.connectors.canvas import CanvasConnector
from studyhub.sync import rebuild_course

SITE = "https://canvas.example.edu"


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


class FakeCanvas:
    """Just enough of the Canvas REST API, including pagination and a hidden Files tab."""

    def __init__(self):
        self.downloads = 0
        self.file_updated = "2026-09-20T10:00:00Z"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, q = request.url.path, request.url.params
        assert request.url.host == "canvas.example.edu"
        if path.startswith("/files/download"):
            self.downloads += 1
            return httpx.Response(200, content=_pdf("Softmax classifier and cross-entropy loss"))
        assert request.headers["authorization"] == "Bearer t0ken"
        api = path.removeprefix("/api/v1")
        if api == "/users/self":
            return httpx.Response(200, json={"name": "Lucas"})
        if api == "/courses":
            if q.get("page") == "2":
                return httpx.Response(200, json=[{"id": 2, "name": "Campus orientation", "course_code": "ORIENT"}])
            return httpx.Response(
                200,
                json=[{"id": 1, "name": "CS 231N: Deep Learning for Computer Vision", "course_code": "CS 231N",
                       "term": {"name": "Autumn 2026", "start_at": "2026-09-21T07:00:00Z"},
                       "syllabus_body": "<p>Collaboration policy: discuss, but write your own code.</p>"},
                      {"id": 3, "access_restricted_by_date": True}],
                headers={"Link": f'<{SITE}/api/v1/courses?page=2&per_page=100>; rel="next"'},
            )
        if api == "/courses/1/assignments":
            return httpx.Response(200, json=[
                {"id": 11, "name": "Assignment 1", "description": "<p>Implement <b>softmax</b>.</p>",
                 "due_at": "2099-10-10T06:59:00Z", "points_possible": 100, "html_url": f"{SITE}/courses/1/assignments/11",
                 "submission": {"workflow_state": "unsubmitted"}},
                {"id": 12, "name": "Quiz 0", "due_at": "2026-09-20T06:59:00Z", "points_possible": 5,
                 "submission": {"workflow_state": "graded", "score": 4}},
            ])
        if api == "/courses/1/modules":
            return httpx.Response(200, json=[{"name": "Lecture 3: Loss functions",
                                              "items": [{"type": "File", "content_id": 99, "title": "slides.pdf"}]}])
        if api == "/courses/1/files":
            return httpx.Response(403, json={"errors": [{"message": "unauthorized"}]})
        if api == "/courses/1/files/99":
            return httpx.Response(200, json={"id": 99, "display_name": "slides.pdf", "content-type": "application/pdf",
                                             "url": f"{SITE}/files/download/99", "size": 1000,
                                             "created_at": "2026-09-28T20:00:00Z", "updated_at": self.file_updated})
        if api == "/courses/1/pages":
            return httpx.Response(200, json=[{"url": "office-hours", "title": "Office hours",
                                              "updated_at": "2026-09-21T00:00:00Z", "html_url": f"{SITE}/x"}])
        if api == "/courses/1/pages/office-hours":
            return httpx.Response(200, json={"body": "<h2>Office hours</h2><p>Gates 104, Tuesdays</p>"})
        if api == "/courses/1/discussion_topics":
            assert q.get("only_announcements") == "true"
            return httpx.Response(200, json=[{"id": 5, "title": "Welcome", "message": "<p>Hi all</p>",
                                              "posted_at": "2026-09-21T18:00:00Z"}])
        return httpx.Response(404, json={})


def _sync(conn, fake):
    import os

    os.environ.update(CANVAS_BASE_URL=SITE, CANVAS_TOKEN="t0ken")  # conftest resets these per test
    get_settings.cache_clear()
    ctx = SyncContext(conn=conn, settings=get_settings())
    CanvasConnector(transport=httpx.MockTransport(fake)).sync(ctx)
    for cid in ctx.touched_courses:
        rebuild_course(conn, cid)
    return ctx


def test_canvas_sync(conn):
    fake = FakeCanvas()
    ctx = _sync(conn, fake)
    course = conn.execute("SELECT * FROM courses").fetchone()
    assert (course["code"], course["title"], course["term"], course["term_start"]) == \
        ("CS 231N", "Deep Learning for Computer Vision", "Autumn 2026", "2026-09-21")
    assert conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 1  # orientation has no code

    kinds = sorted(r["kind"] for r in conn.execute("SELECT kind FROM resources"))
    assert kinds == ["announcement", "page", "page", "slides", "spec"]
    slides = conn.execute("SELECT * FROM resources WHERE kind = 'slides'").fetchone()
    assert slides["page_count"] == 1 and json.loads(slides["meta_json"])["module"] == "Lecture 3: Loss functions"
    lec = conn.execute("SELECT * FROM lectures").fetchone()
    assert lec["number"] == 3 and slides["lecture_id"] == lec["id"]

    status = {r["title"]: (r["status"], r["score"]) for r in conn.execute("SELECT * FROM assignments")}
    assert status == {"Assignment 1": ("upcoming", None), "Quiz 0": ("graded", 4)}
    assert ctx.changed > 0

    # A second sync with nothing new downloads nothing and changes nothing.
    ctx2 = _sync(conn, fake)
    assert fake.downloads == 1
    assert ctx2.changed == 0


def test_canvas_redownloads_changed_file(conn):
    fake = FakeCanvas()
    _sync(conn, fake)
    fake.file_updated = "2026-09-30T10:00:00Z"
    _sync(conn, fake)
    assert fake.downloads == 2


def test_bad_token_stops_sync_but_locked_items_dont(conn):
    import pytest

    from studyhub.connectors.canvas import CanvasError

    class Locked(FakeCanvas):
        def __call__(self, request):
            if request.url.path == "/api/v1/courses/1/pages":
                return httpx.Response(401, json={"status": "unauthorized",
                                                 "errors": [{"message": "user not authorized to perform that action"}]})
            return super().__call__(request)

    _sync(conn, Locked())
    assert conn.execute("SELECT COUNT(*) FROM resources WHERE kind = 'slides'").fetchone()[0] == 1

    class BadToken(FakeCanvas):
        def __call__(self, request):
            return httpx.Response(401, json={"errors": [{"message": "Invalid access token."}]},
                                  headers={"WWW-Authenticate": 'Bearer realm="canvas-lms"'})

    with pytest.raises(CanvasError):
        _sync(conn, BadToken())
