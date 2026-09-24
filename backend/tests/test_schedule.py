import json
from types import SimpleNamespace

from studyhub.ingest import schedule
from studyhub.store import ChunkIn, ensure_course, upsert_resource


def test_schedule_anchors_unrecorded_lectures(conn, tmp_path):
    cid = ensure_course(conn, "CS 231N", term_start="2026-09-21")
    upsert_resource(conn, course_id=cid, source="granola", kind="transcript", external_id="n2",
                    title="CS 231N", occurred_at="2026-09-24T17:30:00Z", chunks=[ChunkIn("hi", seconds=0)])
    csv_path = tmp_path / "schedule.csv"
    csv_path.write_text("number,date,title\n1,2026-09-22,Introduction\n2,2026-09-24,Image classification\n"
                        "3,,Loss functions\n")
    schedule.save_schedule(conn, cid, schedule.read_csv(csv_path))

    lectures = [tuple(r) for r in conn.execute("SELECT number, date, title FROM lectures ORDER BY number")]
    assert lectures == [(1, "2026-09-22", "Introduction"), (2, "2026-09-24", "Image classification"),
                        (3, None, "Loss functions")]
    rec = conn.execute("SELECT l.number FROM resources r JOIN lectures l ON l.id = r.lecture_id").fetchone()
    assert rec["number"] == 2  # the recording joined the scheduled lecture


def test_extract_schedule_uses_structured_output(conn):
    cid = ensure_course(conn, "CS 231N", term="Autumn 2026")
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        text = json.dumps({"lectures": [{"number": 2, "date": "2026-09-24", "title": "kNN"},
                                        {"number": 1, "date": "Sept 22", "title": "Intro"}]})
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    rows = schedule.extract_schedule(conn, cid, "Week 1: Tue 9/22 Intro; Thu 9/24 kNN", client=client)
    assert rows == [{"number": 1, "date": None, "title": "Intro"}, {"number": 2, "date": "2026-09-24", "title": "kNN"}]
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert "CS 231N" in sent["messages"][0]["content"]
