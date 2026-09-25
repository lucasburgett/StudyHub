from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from studyhub.config import get_settings
from studyhub.connectors import gradescope
from studyhub.connectors.base import SyncContext
from studyhub.store import ensure_course, upsert_assignment
from studyhub.sync import rebuild_course


@dataclass
class Course:
    name: str
    full_name: str
    semester: str
    year: str


@dataclass
class Assignment:
    assignment_id: str | None
    name: str
    release_date: datetime | None
    due_date: datetime | None
    late_due_date: datetime | None
    submissions_status: str
    grade: float | None
    max_grade: float | None


def test_parse_submission_props():
    """The AssignmentSubmissionViewer props as Gradescope sends them (Sep 2026); made-up course content."""
    props = {
        "questions": [
            {"id": 1, "type": "FreeResponseQuestion", "title": "Softmax loss", "full_index": "2", "weight": "3.0"},
            {"id": 2, "type": "FreeResponseQuestion", "title": "kNN", "full_index": "1", "weight": "2.0"},
            {"id": 3, "type": "QuestionGroup", "title": "Proofs", "full_index": "3", "weight": "4.0"},
            {"id": 4, "type": "FreeResponseQuestion", "title": "Part (i)", "full_index": "3.1", "weight": "4.0"},
        ],
        "question_submissions": [
            {"question_id": 1, "score": "1.5", "evaluations": [{"id": 7, "points": None, "comments": "ln, not log10"}],
             "annotations": [{"id": 8, "page_number": 2, "content": "see the lecture 3 slides"}]},
            {"question_id": 2, "score": "2.0", "evaluations": [], "annotations": []},
            {"question_id": 4, "score": "4.0", "evaluations": [{"id": 9, "points": None, "comments": ""}],
             "annotations": []},
        ],
        "rubric_items": [
            {"id": 10, "question_id": 1, "description": "Used log base 10", "weight": "-1.5", "present": True},
            {"id": 11, "question_id": 1, "description": "Correct", "weight": "0.0", "present": False},
            {"id": 12, "question_id": 2, "description": "Correct", "weight": "0.0", "present": True},
            {"id": 13, "question_id": 4, "description": "Correct", "weight": "0.0", "present": True},
        ],
    }
    items = gradescope.parse_submission_props(props)
    # The group heading (Q3) is left out; its part carries the score.
    assert [(i.question, i.score, i.max_score) for i in items] == [
        ("Q2: Softmax loss", 1.5, 3.0), ("Q1: kNN", 2.0, 2.0), ("Q3.1: Part (i)", 4.0, 4.0)]
    assert items[0].rubric_items == ["Used log base 10 (-1.5)"]
    assert items[0].comment == "ln, not log10\nsee the lecture 3 slides"
    assert items[1].rubric_items == ["Correct (0)"] and items[1].comment is None
    assert items[2].comment is None


def test_feedback_asks_for_the_viewer_page():
    """Without Accept: text/html, Gradescope sends the scan's JSON, which has no feedback in it."""
    seen = {}

    class Session:
        def get(self, url, headers=None):
            seen["accept"] = (headers or {}).get("Accept")
            return SimpleNamespace(status_code=200, text="<html>no viewer</html>")

    assert gradescope.fetch_feedback(Session(), "1", "2") is None
    assert seen["accept"] == "text/html"


class FakeAccount:
    def get_courses(self):
        return {"student": {"555": Course("CS 231N", "Deep Learning for Computer Vision", "Fall", "2026"),
                            "111": Course("CS 106B", "Programming Abstractions", "Fall", "2024")}}

    def get_assignments(self, course_id):
        assert course_id == "555"
        return [
            Assignment("901", "Quiz 1", None, datetime(2026, 9, 18, 18, 50, tzinfo=timezone.utc), None,
                       "Graded", 8.5, 10.0),
            Assignment("902", "Assignment 1", None, datetime(2099, 10, 3, 6, 59, tzinfo=timezone.utc), None,
                       "No Submission", None, 100.0),
        ]


def test_gradescope_sync_merges_with_canvas(conn, monkeypatch):
    cid = ensure_course(conn, "CS 231N", canvas_id="1")
    upsert_assignment(conn, course_id=cid, source="canvas", external_id="11", title="Assignment 1",
                      due_at="2099-10-03T06:59:00Z", points=100, score=None, status="upcoming", url=None)
    session = SimpleNamespace(get=lambda url: SimpleNamespace(status_code=500, text=""))
    monkeypatch.setattr(gradescope, "_login", lambda s: SimpleNamespace(account=FakeAccount(), session=session))

    ctx = SyncContext(conn=conn, settings=get_settings())
    gradescope.GradescopeConnector().sync(ctx)
    for c in ctx.touched_courses:
        rebuild_course(conn, c)

    rows = {(r["source"], r["title"]): dict(r) for r in conn.execute("SELECT * FROM assignments")}
    assert rows[("gradescope", "Quiz 1")]["score"] == 8.5
    assert rows[("gradescope", "Quiz 1")]["status"] == "graded"
    assert rows[("canvas", "Assignment 1")]["hidden"] == 1  # Gradescope's copy wins
    assert rows[("gradescope", "Assignment 1")]["status"] == "upcoming"
    assert conn.execute("SELECT gradescope_id FROM courses").fetchone()[0] == "555"
    # CS 106B isn't in Canvas this term, so it isn't imported.
    assert conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 1
    assert any("feedback" in w for w in ctx.warnings)


LOGIN_PAGE = """<html><head><meta name="csrf-token" content="tok"></head><body>
<div class="alert alert-flashMessage alert-error" role="alert"><span>You'll need to set a password for this account.
We've sent you a password reset link to student@example.edu.</span></div>
<form action="/login"><input name="authenticity_token" value="abc"></form></body></html>"""


class _Resp:
    def __init__(self, url, text):
        self.url, self.text = url, text


def test_refused_login_says_why(monkeypatch):
    """Gradescope answers a failed login by redirecting back to /login, which gradescopeapi counts as success."""
    import requests

    from studyhub.config import Settings

    posted = {}

    def post(self, url, data=None, **kw):
        posted.update(url=url, data=data, params=kw.get("params"))
        return _Resp("https://www.gradescope.com/login", LOGIN_PAGE)

    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kw: _Resp(url, LOGIN_PAGE))
    monkeypatch.setattr(requests.Session, "post", post)
    settings = Settings(gradescope_email="student@example.edu", gradescope_password="pw")
    with pytest.raises(RuntimeError, match="set a password for this account"):
        gradescope._login(settings)
    # The password goes in the form body, not the URL.
    assert posted["data"]["session[password]"] == "pw" and posted["params"] is None


def test_accepted_login(monkeypatch):
    import requests

    from studyhub.config import Settings

    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kw: _Resp(url, LOGIN_PAGE))
    monkeypatch.setattr(requests.Session, "post",
                        lambda self, url, **kw: _Resp("https://www.gradescope.com/account", LOGIN_PAGE))
    gs = gradescope._login(Settings(gradescope_email="student@example.edu", gradescope_password="pw"))
    assert gs.logged_in and gs.account is not None and gs.session.headers["X-CSRF-Token"] == "tok"
