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


def test_rebuild_is_idempotent(demo):
    before = [tuple(r) for r in demo.execute("SELECT number, date, title FROM lectures ORDER BY id")]
    cid = demo.execute("SELECT id FROM courses").fetchone()["id"]
    rebuild_lectures(demo, cid)
    after = [tuple(r) for r in demo.execute("SELECT number, date, title FROM lectures ORDER BY id")]
    assert before == after


def test_clean_lecture_title():
    assert clean_lecture_title("cs231n_lecture_03_loss-functions.pdf") == "Loss functions"
    assert clean_lecture_title("Lecture 5 slides.pdf") is None
