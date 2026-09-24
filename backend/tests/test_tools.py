import pytest

from studyhub.agent.tools import Toolbox, ToolError


def _box(conn):
    return Toolbox(conn)


def _rid(conn, kind, number):
    return conn.execute("SELECT r.id FROM resources r JOIN lectures l ON l.id = r.lecture_id"
                        " WHERE r.kind = ? AND l.number = ?", (kind, number)).fetchone()["id"]


def test_definitions_are_stable_and_stream_eagerly(demo):
    defs = _box(demo).definitions()
    assert [d["name"] for d in defs] == ["search", "read", "get_lecture", "list_assignments", "get_feedback",
                                         "list_resources", "list_announcements", "sync_now"]
    assert all(d["eager_input_streaming"] for d in defs)
    assert defs == _box(demo).definitions()
    assert defs[0]["input_schema"]["required"] == ["query"]


def test_search_returns_citable_hits(demo):
    box = _box(demo)
    result = box.run("search", box.parse("search", {"query": "cross entropy loss at initialization", "course": "cs231n"}))
    rec = _rid(demo, "transcript", 3)
    assert f"[r{rec}@41:12]" in result.text
    assert result.citations[f"r{rec}@41:12"]["label"] == "L3 · 41:12"
    assert result.text.index("lecture_3") < result.text.index("lecture_2")  # lecture 3 ranks first


def test_read_page_range_and_time_window(demo):
    box = _box(demo)
    slides = _rid(demo, "slides", 3)
    result = box.run("read", box.parse("read", {"locator": f"r{slides}#p5"}))
    assert f"[r{slides}#p5]" in result.text and f"[r{slides}#p4]" not in result.text
    rec = _rid(demo, "transcript", 3)
    result = box.run("read", box.parse("read", {"locator": f"r{rec}@41:12"}))
    assert "negative log probability" in result.text and "Last time we saw kNN" not in result.text


def test_get_lecture_bundles_sources(demo):
    box = _box(demo)
    result = box.run("get_lecture", box.parse("get_lecture", {"course": "CS 231N", "number": 3}))
    assert "Linear classifiers" in result.text
    assert "cross-entropy" in result.text.lower()
    assert any(k.endswith("#p4") for k in result.citations)  # the notes page for lecture 3
    missing = box.run("get_lecture", box.parse("get_lecture", {"course": "CS 231N", "number": 1}))
    assert "Not synced for this lecture: recording" in missing.text


def test_assignments_and_feedback(demo):
    box = _box(demo)
    result = box.run("list_assignments", box.parse("list_assignments", {"course": "CS 231N"}))
    assert "Quiz 1" in result.text and "8.5/10" in result.text
    assert result.text.count("Assignment 1") == 1  # the Canvas twin is hidden
    quiz = demo.execute("SELECT id FROM assignments WHERE title = 'Quiz 1'").fetchone()["id"]
    fb = box.run("get_feedback", box.parse("get_feedback", {"assignment": f"a{quiz}"}))
    assert "log base 10" in fb.text and f"a{quiz}/q2" in fb.citations


def test_bad_input_and_unknown_course(demo):
    box = _box(demo)
    with pytest.raises(ToolError):
        box.parse("search", {"q": "missing required field"})
    result = box.run("list_resources", box.parse("list_resources", {"course": "EE 999"}))
    assert result.is_error and "CS 231N" in result.text
