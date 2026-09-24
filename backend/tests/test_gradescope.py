from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

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
    props = {
        "questions": [{"id": 1, "title": "Softmax loss", "full_index": "2", "weight": 3.0},
                      {"id": 2, "title": "kNN", "full_index": "1", "weight": 2.0}],
        "question_submissions": [{"question_id": 1, "score": "1.5", "annotations": [{"text": "ln, not log10"}]},
                                 {"question_id": 2, "score": 2.0}],
        "rubric_items": [{"id": 10, "question_id": 1, "description": "Used log base 10", "weight": -1.5},
                         {"id": 11, "question_id": 1, "description": "Correct", "weight": 0},
                         {"id": 12, "question_id": 2, "description": "Correct", "weight": 0, "present": True}],
        "evaluations": [{"rubric_items": [{"rubric_item_id": 10, "present": True}]}],
    }
    items = gradescope.parse_submission_props(props)
    assert [(i.question, i.score, i.max_score) for i in items] == [("Q2: Softmax loss", 1.5, 3.0), ("Q1: kNN", 2.0, 2.0)]
    assert items[0].rubric_items == ["Used log base 10 (-1.5)"]
    assert items[0].comment == "ln, not log10"
    assert items[1].rubric_items == ["Correct (0)"]


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
