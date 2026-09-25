from studyhub.ingest.linking import clean_lecture_title, rebuild_lectures
from studyhub.store import ChunkIn, ensure_course, upsert_resource


def test_demo_lectures_link_every_source(demo):
    lectures = {r["number"]: dict(r) for r in demo.execute("SELECT * FROM lectures")}
    assert set(lectures) == {1, 2, 3, 4, 5, 6}
    assert lectures[3]["date"] == "2026-09-15"
    assert lectures[3]["title"] == "Linear classifiers and loss functions"
    # Lecture 3 has its slides and recording, and pages 4-5 of the notebook.
    kinds = {r["kind"] for r in demo.execute("SELECT kind FROM resources WHERE lecture_id = ?", (lectures[3]["id"],))}
    assert kinds == {"slides", "transcript"}
    pages = [r["page"] for r in demo.execute("SELECT page FROM chunks WHERE lecture_id = ? AND page IS NOT NULL"
                                             " AND resource_id = (SELECT id FROM resources WHERE kind = 'notes')",
                                             (lectures[3]["id"],))]
    assert pages == [4, 5]


def test_numbered_slides_join_the_recorded_lecture(conn):
    cid = ensure_course(conn, "CS 231N")
    upsert_resource(conn, course_id=cid, source="granola", kind="transcript", external_id="n1",
                    title="CS 231N", occurred_at="2026-09-29T17:30:00Z", chunks=[ChunkIn("hello", seconds=0)])
    # Posted the evening before lecture 3.
    upsert_resource(conn, course_id=cid, source="canvas", kind="slides", external_id="f1",
                    title="lecture_3_loss.pdf", occurred_at="2026-09-29T02:00:00Z", chunks=[ChunkIn("x", page=1)])
    assert rebuild_lectures(conn, cid) == 1
    lec = conn.execute("SELECT * FROM lectures").fetchone()
    assert (lec["number"], lec["date"], lec["title"]) == (3, "2026-09-29", "Loss")


def test_title_slide_dates_and_names_the_lecture(conn):
    """No recording or schedule: the deck's own first line says when and what (a real MATH 115 format)."""
    cid = ensure_course(conn, "MATH 115")
    for n, day, topic in ((1, "September 22", "Introduction"), (2, "September 24", "The real numbers")):
        upsert_resource(conn, course_id=cid, source="canvas", kind="slides", external_id=f"f{n}",
                        title=f"2026FMath115Lecture0{n}.pdf", occurred_at=f"2026-09-2{n}T04:00:00Z",
                        chunks=[ChunkIn(f"Math 115, Lecture {n} ({day}, 2026): {topic}\n1.1\nBackground", page=1),
                                ChunkIn("Due Friday, October 2: problem set 1", page=2)])
    # The same course's other layout: the topic on the next line, no date.
    upsert_resource(conn, course_id=cid, source="canvas", kind="slides", external_id="f4",
                    title="2026FMath115Lecture04.pdf", occurred_at="2026-10-01T04:00:00Z",
                    chunks=[ChunkIn("Math 115 ♢Fall 2026 ♢Rick Sommer ♢Lecture 4\nCauchy Sequences\n1. A sequence", page=1)])
    # A deck whose first page mentions a date that isn't the lecture's stays undated.
    upsert_resource(conn, course_id=cid, source="canvas", kind="slides", external_id="f3",
                    title="Lecture 3.pdf", occurred_at="2026-09-28T04:00:00Z",
                    chunks=[ChunkIn("Sequences\nHomework 1 due 10/2", page=1)])
    assert rebuild_lectures(conn, cid) == 4
    rows = [tuple(r) for r in conn.execute("SELECT number, date, title FROM lectures ORDER BY number")]
    assert rows == [(1, "2026-09-22", "Introduction"), (2, "2026-09-24", "The real numbers"), (3, None, None),
                    (4, None, "Cauchy Sequences")]


def test_rebuild_is_idempotent(demo):
    before = [tuple(r) for r in demo.execute("SELECT number, date, title FROM lectures ORDER BY id")]
    cid = demo.execute("SELECT id FROM courses").fetchone()["id"]
    rebuild_lectures(demo, cid)
    after = [tuple(r) for r in demo.execute("SELECT number, date, title FROM lectures ORDER BY id")]
    assert before == after


def test_clean_lecture_title():
    assert clean_lecture_title("cs231n_lecture_03_loss-functions.pdf") == "Loss functions"
    assert clean_lecture_title("Lecture 5 slides.pdf") is None
    # Real Stanford file names: the course code glued to the term, or a term prefix.
    assert clean_lecture_title("2026FMath115Lecture01.pdf", "MATH 115") is None
    assert clean_lecture_title("F26_STATS118_Lecture_3_Conditional_probability.pdf", "STATS 118") == \
        "Conditional probability"
    assert clean_lecture_title("Autumn 2026 - Lecture 4 - Series.pdf") == "Series"
