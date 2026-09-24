import json
from pathlib import Path

import anthropic
import httpx2
import pytest
from test_chat import FakeClient, _message

from studyhub.citations import parse_locator
from studyhub.config import get_settings
from studyhub.evals import CaseError, Target, grade, load_cases, run_eval, wilson

DEMO_CASES = Path(__file__).parent.parent / "evals" / "demo.json"


def _rid(conn, title):
    return conn.execute("SELECT id FROM resources WHERE title = ?", (title,)).fetchone()["id"]


def test_demo_cases_resolve_against_the_demo_data(demo):
    cases = load_cases(demo, DEMO_CASES)
    assert len(cases) == 6
    init = next(c for c in cases if c.id == "init-loss")
    assert init.cites_any[0].resource_id == _rid(demo, "CS 231N Lecture 3")
    assert init.cites_any[0].seconds == 47 * 60 + 30


def test_case_file_problems_are_caught_before_running(demo, tmp_path):
    def load(cases):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"cases": cases}))
        return load_cases(demo, path)

    with pytest.raises(CaseError, match=r"matches \d+ items; make it more specific"):
        load([{"id": "a", "question": "q", "cites_any": [{"title": "Lecture"}]}])
    with pytest.raises(CaseError, match="no assignment"):
        load([{"id": "a", "question": "q", "cites_any": [{"assignment": "Homework 9"}]}])
    with pytest.raises(CaseError, match="nothing to check"):
        load([{"id": "a", "question": "q"}])
    with pytest.raises(CaseError, match="unique id"):
        load([{"id": "a", "question": "q", "mentions": ["x"]}, {"id": "a", "question": "q", "mentions": ["x"]}])


def test_target_matching():
    t = Target(resource_id=6, seconds=2850)
    assert t.matches(parse_locator("r6@46:00"))       # within 150 s
    assert not t.matches(parse_locator("r6@41:12"))   # too far
    assert not t.matches(parse_locator("r7@47:30"))   # other resource
    assert Target(resource_id=5, page=5).matches(parse_locator("r5#p5"))
    assert not Target(resource_id=5, page=5).matches(parse_locator("r5#p4"))
    assert Target(resource_id=5).matches(parse_locator("r5#p4"))
    assert Target(assignment_id=4, question=2).matches(parse_locator("a4/q2"))
    assert not Target(assignment_id=4, question=2).matches(parse_locator("a4"))


def test_grade():
    from studyhub.evals import Case

    case = Case(id="x", question="q", course_id=None, cites_any=[Target(resource_id=5, page=5)],
                mentions=["2.3", "re:ln|natural log"], must_not=["log10"], tools=["search"])
    assert grade(case, "It is ln(10) ≈ 2.3", ["r5#p5"], ["search"]) == \
        {"cites": True, "mentions": True, "must_not": True, "tools": True}
    assert grade(case, "log10 gives 2.3", [], []) == {"cites": False, "mentions": False, "must_not": False,
                                                      "tools": False}


def test_wilson():
    assert wilson(0, 0) == (0.0, 0.0)
    low, high = wilson(8, 10)
    assert 0.49 < low < 0.5 and 0.94 < high < 0.95


class ScriptedClient(FakeClient):
    """Like FakeClient, but a script entry can be an exception to raise."""

    def _stream(self, **kwargs):
        nxt = self.script[0]
        if isinstance(nxt, Exception):
            self.script.pop(0)
            raise nxt
        return super()._stream(**kwargs)


def test_run_eval_scores_errors_and_isolation(demo, tmp_path):
    rec = _rid(demo, "CS 231N Lecture 3")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"cases": [
        {"id": "good", "course": "CS 231N", "question": "Loss at init?", "tags": ["lecture"],
         "cites_any": [{"title": "CS 231N Lecture 3", "at": "41:12"}], "mentions": ["2.3"]},
        {"id": "uncited", "course": "CS 231N", "question": "Loss at init?",
         "cites_any": [{"title": "CS 231N Lecture 3", "at": "41:12"}], "mentions": ["2.3"]},
        {"id": "broken-api", "question": "Anything", "mentions": ["x"]},
    ]}))
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    client = ScriptedClient([
        _message([{"type": "tool_use", "id": "t1", "name": "search", "input": {"query": "cross entropy loss"}}],
                 "tool_use"),
        _message([{"type": "text", "text": f"About 2.3 [[r{rec}@41:12]]."}], "end_turn"),
        _message([{"type": "text", "text": "About 2.3, trust me."}], "end_turn"),
        anthropic.APIConnectionError(request=request),
    ])
    out = tmp_path / "run"
    summary = run_eval(cases, out, client=client, timeout_s=30)

    assert (summary["scored"], summary["passed"], summary["errors"]) == (2, 1, 1)
    assert summary["failing_checks"] == {"cites": 1}
    assert summary["by_case"]["good"] == {"passed": 1, "of": 1}
    assert summary["models"] == {"claude-opus-5": 2}
    rows = [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()]
    assert rows[0]["cited"] == [f"r{rec}@41:12"] and rows[0]["tools"] == ["search"]
    errors = [json.loads(line) for line in (out / "errors.jsonl").read_text().splitlines()]
    assert errors[0]["case_id"] == "broken-api" and errors[0]["kind"] == "api"
    assert (out / "transcripts" / "good.0.json").exists() and (out / "summary.json").exists()
    # The eval ran on a copy: no eval chats in the real database.
    assert demo.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 0
    assert get_settings().db_path.exists()


def test_refusals_and_truncation_are_scored_separately(demo, tmp_path):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"cases": [{"id": "r", "question": "q", "mentions": ["x"]}]}))
    client = ScriptedClient([_message([{"type": "text", "text": ""}], "refusal")])
    summary = run_eval(cases, tmp_path / "run", client=client, timeout_s=30)
    assert summary["statuses"] == {"refused": 1} and summary["errors"] == 0 and summary["passed"] == 0
