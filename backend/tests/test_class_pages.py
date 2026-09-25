"""Homework posted on a page per class ("Week 1, Day 3"), as FRENLANG 1 does, becomes assignments."""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from studyhub import config
from studyhub.ingest.class_pages import mark_done, parse_calendars, parse_class_page, rebuild_class_homework
from studyhub.store import delete_missing, ensure_course, upsert_resource

# The layout of the real French class pages (no names or personal details in them).
DAY2 = """**Students’ Tasks to Complete Before Class (Devoirs):**

--

**En classe (In-Class activities)**

* Introduction du cours (Syllabus et Canvas)
"""
DAY3 = """**Students’ Tasks to Complete Before Class (Devoirs):**

* Finish reading the syllabus
* Complete Canvas tutorial if you haven't already (5-10min)

Étudiez et lisez (study and read):

* Regardez les vidéos:
 + Tu et vous
* *Édito*: étudiez les nombres 1-31 (p. 14)

**En classe (In-Class activities):**

* Question sur Canvas?
* Page 14: Les nombres
"""
PT = ZoneInfo("America/Los_Angeles")


def test_parse_class_page():
    page = parse_class_page("Week 1, Day 3", DAY3)
    assert (page.week, page.day, page.label, page.template_ok) == (1, 3, "Devoirs", True)
    assert page.homework.startswith("* Finish reading the syllabus")
    assert "Regardez les vidéos" in page.homework and "nombres 1-31" in page.homework
    assert "En classe" not in page.homework and "Question sur Canvas" not in page.homework

    assert parse_class_page("Week 1, Day 2", DAY2).homework is None  # "--": nothing due
    assert (parse_class_page("Semaine 3 - Jour 2", DAY3).week, parse_class_page("Semaine 3 - Jour 2", DAY3).day) == (3, 2)
    assert parse_class_page("Dictionnaires (Liens)", DAY3) is None  # not a class page
    inline = parse_class_page("Week 2, Day 1", "**Homework:** read pages 20-22\n\n**In class:** quiz")
    assert (inline.label, inline.homework) == ("Homework", "read pages 20-22")

    # Only headings end the homework, not a task that mentions class.
    tasks = parse_class_page("Week 2, Day 2", "**Devoirs:**\n\n* Bring your in-class notebook\n* Read p. 30\n\n"
                                              "**En classe**\n\n* Quiz")
    assert tasks.homework == "* Bring your in-class notebook\n* Read p. 30"

    changed = parse_class_page("Week 2, Day 4", "Today: a movie in French.")
    assert changed.homework is None and not changed.template_ok


def test_parse_calendars():
    cals, errors = parse_calendars(
        "FRENLANG 1=Mon-Fri 9:30 from 2026-09-21; MATH 51=MWF 1:30pm from 2026-09-23\nCS 106B=TTh 10:30am from 2026-09-22"
    )
    assert errors == []
    fr, math, cs = cals["FRENLANG1"], cals["MATH51"], cals["CS106B"]
    assert (fr.weekdays, fr.time, fr.week1) == ((0, 1, 2, 3, 4), time(9, 30), date(2026, 9, 21))
    assert (math.weekdays, math.time, math.week1) == ((0, 2, 4), time(13, 30), date(2026, 9, 21))  # its Monday
    assert cs.weekdays == (1, 3)
    assert fr.class_start(1, 5, PT) == datetime(2026, 9, 25, 9, 30, tzinfo=PT)
    assert math.class_start(2, 3, PT).date() == date(2026, 10, 2)  # week 2's third class: Friday
    assert fr.class_start(1, 6, PT) is None  # there's no sixth class in a week
    assert parse_calendars("STATS 118=Mon/Wed 3:00pm from 2026-09-21")[0]["STATS118"].weekdays == (0, 2)

    _, errors = parse_calendars("FRENLANG 1=sometimes; nonsense")
    assert len(errors) == 2


def _page(conn, cid, title, md, n):
    return upsert_resource(conn, course_id=cid, source="canvas", kind="page", external_id=f"page:9:p{n}",
                           title=title, url=f"https://canvas.example.edu/courses/9/pages/p{n}", markdown=md)[0]


def _homework(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT title, due_at, status FROM assignments WHERE external_id LIKE 'devoirs:%' ORDER BY due_at, title")]


def test_class_pages_become_dated_homework(conn, monkeypatch):
    cid = ensure_course(conn, "FRENLANG 1")
    _page(conn, cid, "Week 1, Day 2", DAY2, 2)
    day3 = _page(conn, cid, "Week 1, Day 3", DAY3, 3)
    _page(conn, cid, "Week 1, Day 4", DAY3.replace("syllabus", "Édito p. 3-5"), 4)
    _page(conn, cid, "Dictionnaires (Liens)", "* links", 5)

    # No class schedule yet: the homework exists, undated, and the sync says how to fix that.
    warnings = rebuild_class_homework(conn, now=datetime(2026, 9, 22, 12, tzinfo=timezone.utc))
    assert _homework(conn) == [("Devoirs · Week 1, Day 3", None, "upcoming"), ("Devoirs · Week 1, Day 4", None, "upcoming")]
    assert warnings == ["Add FRENLANG 1's class days in Settings → Class schedules to date its homework."]

    monkeypatch.setenv("CLASS_SCHEDULES", "FRENLANG 1=Mon-Fri 9:30 from 2026-09-21")
    config.get_settings.cache_clear()
    now = datetime(2026, 9, 23, 20, tzinfo=timezone.utc)  # Wed 1 PM: after Wednesday's class
    assert rebuild_class_homework(conn, now=now) == []
    assert _homework(conn) == [("Devoirs · Week 1, Day 3", "2026-09-23T16:30:00Z", "past"),
                               ("Devoirs · Week 1, Day 4", "2026-09-24T16:30:00Z", "upcoming")]
    hw = conn.execute("SELECT * FROM assignments WHERE title LIKE '%Day 3'").fetchone()
    assert (hw["course_id"], hw["source"], hw["spec_resource_id"]) == (cid, "canvas", day3)
    assert hw["url"].endswith("/pages/p3")

    # A tick survives rebuilds, and a page that goes away takes its homework with it.
    mark_done(conn, hw["id"], True, now=now)
    assert conn.execute("SELECT status FROM assignments WHERE id = ?", (hw["id"],)).fetchone()[0] == "done"
    rebuild_class_homework(conn, now=now)
    assert _homework(conn)[0] == ("Devoirs · Week 1, Day 3", "2026-09-23T16:30:00Z", "done")
    mark_done(conn, hw["id"], False, now=now)
    assert _homework(conn)[0][2] == "past"

    delete_missing(conn, "canvas", cid, ("page",), {"page:9:p2", "page:9:p3", "page:9:p5"})
    rebuild_class_homework(conn, now=now)
    assert [t for t, _, _ in _homework(conn)] == ["Devoirs · Week 1, Day 3"]


def test_only_class_page_homework_can_be_ticked(conn):
    from studyhub.store import upsert_assignment

    cid = ensure_course(conn, "FRENLANG 1")
    aid, _ = upsert_assignment(conn, course_id=cid, source="canvas", external_id="123", title="Journal",
                               due_at=None, points=100, score=None, status="upcoming", url=None)
    with pytest.raises(ValueError):
        mark_done(conn, aid, True)


def test_changed_template_is_reported(conn):
    cid = ensure_course(conn, "FRENLANG 1")
    _page(conn, cid, "Week 2, Day 4", "Today: a movie in French.", 1)
    warnings = rebuild_class_homework(conn)
    assert warnings == ["FRENLANG 1: no homework section found on “Week 2, Day 4”; its layout may have changed."]


def test_settings_and_ticks_over_http(conn, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from studyhub import envfile
    from studyhub.api import app
    from studyhub.store import upsert_assignment

    env = tmp_path / ".env"
    monkeypatch.setattr(envfile, "ENV_PATH", env)
    monkeypatch.setattr(config.Settings, "model_config", {**config.Settings.model_config, "env_file": env})
    config.get_settings.cache_clear()
    cid = ensure_course(conn, "FRENLANG 1")
    _page(conn, cid, "Week 1, Day 3", DAY3, 3)
    journal, _ = upsert_assignment(conn, course_id=cid, source="canvas", external_id="77", title="Journal",
                                   due_at=None, points=100, score=None, status="upcoming", url=None)
    rebuild_class_homework(conn)
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    bad = client.put("/api/settings", json={"values": {"CLASS_SCHEDULES": "FRENLANG 1=sometimes"}})
    assert bad.status_code == 400 and "FRENLANG 1=Mon-Fri 9:30 from" in bad.json()["detail"]
    saved = client.put("/api/settings", json={"values": {"CLASS_SCHEDULES": "FRENLANG 1=Mon-Fri 9:30 from 2026-09-21"}})
    assert saved.status_code == 200

    # Saving the schedule dated the homework right away.
    homework = next(a for a in client.get(f"/api/courses/{cid}/assignments").json() if a["checkable"])
    assert homework["due_at"] == "2026-09-23T16:30:00Z" and homework["title"] == "Devoirs · Week 1, Day 3"
    assert client.put(f"/api/assignments/{homework['id']}/done", json={"done": True}).json()["status"] == "done"
    detail = client.get(f"/api/assignments/{homework['id']}").json()
    assert detail["status"] == "done" and "Finish reading the syllabus" in detail["description"]

    refused = client.put(f"/api/assignments/{journal}/done", json={"done": True})
    assert refused.status_code == 400
