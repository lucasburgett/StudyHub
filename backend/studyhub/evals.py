"""Evals: run real questions through the chat agent and check what it cites and says.

A case file is JSON (see backend/evals/demo.json). Keep your real questions somewhere private,
such as data/evals/, since they describe your classes. Each case says which sources a good
answer cites and, optionally, words it must or must not contain and tools it should use:

    {"id": "init-loss", "course": "CS 231N",
     "question": "What should the loss be at initialization?",
     "cites_any": [{"title": "CS 231N Lecture 3", "at": "47:30"}, {"title": "lecture_3.pdf", "page": 5}],
     "mentions": ["2.3"], "must_not": [], "tools": [], "tags": ["lecture"]}

Targets name sources the way you'd recognize them: {"title": …} (anywhere in it), plus "page"
or "at" (a time, matched within "tolerance_s", 150 s by default), or {"assignment": …, "question": n}.
A raw locator string ("r42@41:12") works too.

Runs happen on a copy of the database, so eval chats never show up in your history. API errors
and timeouts go to errors.jsonl, never into the score. Refusals and cut-off answers are scored
but counted separately.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections import Counter, defaultdict
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .citations import Locator, markers, parse_locator
from .config import get_settings
from .db import connect, init_db, now_iso
from .store import find_course
from .util import parse_clock

DEFAULT_TOLERANCE_S = 150  # transcript windows are ~2.5 minutes long


class CaseError(ValueError):
    """The case file itself is wrong: fix it before spending anything."""


@dataclass
class Target:
    resource_id: int | None = None
    assignment_id: int | None = None
    page: int | None = None
    seconds: int | None = None
    tolerance_s: int = DEFAULT_TOLERANCE_S
    question: int | None = None

    def matches(self, loc: Locator) -> bool:
        if self.assignment_id is not None:
            return loc.assignment_id == self.assignment_id and (self.question is None or loc.question == self.question)
        if loc.resource_id != self.resource_id:
            return False
        if self.page is not None:
            return loc.page == self.page
        if self.seconds is not None:
            return loc.seconds is not None and abs(loc.seconds - self.seconds) <= self.tolerance_s
        return True


@dataclass
class Case:
    id: str
    question: str
    course_id: int | None
    cites_any: list[Target] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def _resolve_target(conn: sqlite3.Connection, spec: Any, course_id: int | None, case_id: str) -> Target:
    if isinstance(spec, str):
        loc = parse_locator(spec)
        if loc is None:
            raise CaseError(f"{case_id}: {spec!r} isn't a locator")
        return Target(resource_id=loc.resource_id, assignment_id=loc.assignment_id, page=loc.page,
                      seconds=loc.seconds, question=loc.question)
    if not isinstance(spec, dict):
        raise CaseError(f"{case_id}: each cites_any entry is a locator string or an object")
    scope, params = ("", []) if course_id is None else (" AND course_id = ?", [course_id])
    if "assignment" in spec:
        name = spec["assignment"]
        rows = conn.execute(
            f"SELECT id FROM assignments WHERE lower(title) = lower(?){scope} ORDER BY hidden, id", (name, *params)
        ).fetchall() or conn.execute(
            f"SELECT id FROM assignments WHERE title LIKE ?{scope} ORDER BY hidden, id", (f"%{name}%", *params)
        ).fetchall()
        if not rows:
            raise CaseError(f"{case_id}: no assignment called {name!r}")
        return Target(assignment_id=rows[0]["id"], question=spec.get("question"))
    if "title" in spec:
        name = spec["title"]
        rows = conn.execute(
            f"SELECT id FROM resources WHERE lower(title) = lower(?){scope} ORDER BY id", (name, *params)
        ).fetchall() or conn.execute(
            f"SELECT id FROM resources WHERE title LIKE ?{scope} ORDER BY id", (f"%{name}%", *params)
        ).fetchall()
        if len(rows) != 1:
            found = "nothing" if not rows else f"{len(rows)} items"
            raise CaseError(f"{case_id}: the title {name!r} matches {found}; make it more specific")
        seconds = parse_clock(spec["at"]) if spec.get("at") else None
        if spec.get("at") and seconds is None:
            raise CaseError(f"{case_id}: {spec['at']!r} isn't a time like 41:12")
        return Target(resource_id=rows[0]["id"], page=spec.get("page"), seconds=seconds,
                      tolerance_s=int(spec.get("tolerance_s", DEFAULT_TOLERANCE_S)))
    raise CaseError(f"{case_id}: a target needs \"title\" or \"assignment\"")


def load_cases(conn: sqlite3.Connection, path: Path) -> list[Case]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise CaseError(f"Couldn't read {path}: {e}") from e
    raw_cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(raw_cases, list) or not raw_cases:
        raise CaseError(f"{path} has no cases")
    cases, seen = [], set()
    for raw in raw_cases:
        case_id = str(raw.get("id") or "")
        if not case_id or case_id in seen:
            raise CaseError(f"Every case needs a unique id (problem: {case_id or raw.get('question', '?')!r})")
        seen.add(case_id)
        if not str(raw.get("question") or "").strip():
            raise CaseError(f"{case_id}: missing question")
        course_id = None
        if raw.get("course"):
            course_id = find_course(conn, raw["course"])
            if course_id is None:
                raise CaseError(f"{case_id}: no course matches {raw['course']!r}")
        if not (raw.get("cites_any") or raw.get("mentions") or raw.get("must_not") or raw.get("tools")):
            raise CaseError(f"{case_id}: nothing to check; add cites_any, mentions, must_not or tools")
        cases.append(Case(
            id=case_id, question=raw["question"].strip(), course_id=course_id,
            cites_any=[_resolve_target(conn, t, course_id, case_id) for t in raw.get("cites_any") or []],
            mentions=list(raw.get("mentions") or []), must_not=list(raw.get("must_not") or []),
            tools=list(raw.get("tools") or []), tags=list(raw.get("tags") or []),
        ))
    return cases


def _contains(text: str, pattern: str) -> bool:
    if pattern.startswith("re:"):
        return re.search(pattern[3:], text, re.IGNORECASE) is not None
    return pattern.lower() in text.lower()


def grade(case: Case, answer: str, cited: list[str], tools_used: list[str]) -> dict[str, bool]:
    """Each applicable check, True when it passes."""
    grades: dict[str, bool] = {}
    if case.cites_any:
        locs = [loc for loc in (parse_locator(c) for c in cited) if loc]
        grades["cites"] = any(t.matches(loc) for t in case.cites_any for loc in locs)
    if case.mentions:
        grades["mentions"] = all(_contains(answer, m) for m in case.mentions)
    if case.must_not:
        grades["must_not"] = not any(_contains(answer, m) for m in case.must_not)
    if case.tools:
        grades["tools"] = all(t in tools_used for t in case.tools)
    return grades


class InfraError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _ask(db_path: Path, case: Case, client: Any, effort: str) -> dict:
    """One question through the real chat entry point. Returns the collected events."""
    from .agent.chat import run_chat

    conn = connect(db_path)
    try:
        init_db(conn)
        events = []
        scope = {"course_id": case.course_id} if case.course_id else {}
        for event, data in run_chat(conn, case.question, scope=scope, client=client, effort=effort):
            events.append({"event": event, "data": data})
        return {"events": events}
    finally:
        conn.close()


def run_case(db_path: Path, case: Case, rep: int, *, client: Any, effort: str, timeout_s: float) -> dict:
    started = time.monotonic()
    result: dict[str, Any] = {}

    def work() -> None:
        try:
            result["out"] = _ask(db_path, case, client, effort)
        except Exception as e:  # the chat loop crashing is a harness problem, not the model's answer
            result["error"] = e

    # A daemon thread, so a hung stream can't hold the run (or the process) past its deadline.
    worker = threading.Thread(target=work, daemon=True, name=f"eval-{case.id}-{rep}")
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise InfraError("timeout", f"no answer within {timeout_s:.0f} s")
    if "error" in result:
        e = result["error"]
        raise InfraError("harness", f"{e.__class__.__name__}: {e}")
    out = result["out"]
    events = out["events"]
    answer = "".join(e["data"]["delta"] for e in events if e["event"] == "text")
    citations: dict[str, dict] = {}
    for e in events:
        if e["event"] == "sources":
            citations.update(e["data"]["citations"])
    error = next((e["data"] for e in events if e["event"] == "error"), None)
    done = next((e["data"] for e in events if e["event"] == "done"), {})
    if error and error.get("kind") in ("api", "tool_json", None):
        raise InfraError(error.get("kind") or "unknown", error["message"])
    status = {"refusal": "refused", "truncated": "truncated", "steps": "gave_up"}.get((error or {}).get("kind"), "ok")
    tools_used = [e["data"]["name"] for e in events if e["event"] == "tool" and e["data"]["status"] != "running"]
    cited = [loc for loc in markers(answer) if loc in citations]
    grades = grade(case, answer, cited, tools_used)
    return {
        "case_id": case.id, "rep": rep, "tags": case.tags, "question": case.question, "status": status,
        "pass": status == "ok" and all(grades.values()), "grades": grades, "answer": answer, "cited": cited,
        "tools": tools_used, "model": done.get("model"), "stop_reason": done.get("stop_reason"),
        "usage": done.get("usage", {}), "seconds": round(time.monotonic() - started, 1), "events": events,
    }


def wilson(passed: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a pass rate; honest with small n, unlike ±2·SE."""
    if n == 0:
        return 0.0, 0.0
    p = passed / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, center - half), min(1.0, center + half)


def copy_database(dest: Path) -> Path:
    """A private copy of the store, so eval chats don't land in your chat history."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(get_settings().db_path)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        src.close()
        out.close()
    return dest


def summarize(rows: list[dict], errors: list[dict], cases: list[Case]) -> dict:
    scored = len(rows)
    passed = sum(r["pass"] for r in rows)
    low, high = wilson(passed, scored)
    by_case: dict[str, list[bool]] = defaultdict(list)
    by_tag: dict[str, list[bool]] = defaultdict(list)
    failing_checks: Counter = Counter()
    for r in rows:
        by_case[r["case_id"]].append(r["pass"])
        for tag in r["tags"] or ["untagged"]:
            by_tag[tag].append(r["pass"])
        failing_checks.update(k for k, ok in r["grades"].items() if not ok)
    usage: Counter = Counter()
    for r in rows:
        usage.update({k: v for k, v in r["usage"].items() if isinstance(v, int)})
    return {
        "finished_at": now_iso(),
        "cases": len(cases),
        "attempts": scored + len(errors),
        "scored": scored,
        "errors": len(errors),
        "passed": passed,
        "pass_rate": round(passed / scored, 3) if scored else None,
        "pass_rate_95ci": [round(low, 3), round(high, 3)],
        "statuses": dict(Counter(r["status"] for r in rows)),
        "failing_checks": dict(failing_checks),
        "by_tag": {t: {"passed": sum(v), "of": len(v)} for t, v in sorted(by_tag.items())},
        "by_case": {c: {"passed": sum(v), "of": len(v)} for c, v in by_case.items()},
        "models": dict(Counter(r["model"] for r in rows if r["model"])),
        "usage": dict(usage),
    }


def run_eval(cases_path: Path, out_dir: Path, *, reps: int = 1, effort: str = "medium", only: list[str] | None = None,
             client: Any = None, timeout_s: float = 300, on_row=None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = copy_database(out_dir / "eval.db")
    conn = connect(db_path)
    try:
        cases = load_cases(conn, cases_path)
    finally:
        conn.close()
    if only:
        cases = [c for c in cases if c.id in set(only)]
    if client is None:
        from .agent.chat import make_client

        client = make_client()

    transcripts = out_dir / "transcripts"
    transcripts.mkdir(exist_ok=True)
    rows, errors = [], []
    with (out_dir / "results.jsonl").open("w") as results_f, (out_dir / "errors.jsonl").open("w") as errors_f:
        for case in cases:
            for rep in range(reps):
                try:
                    row = run_case(db_path, case, rep, client=client, effort=effort, timeout_s=timeout_s)
                except InfraError as e:
                    err = {"case_id": case.id, "rep": rep, "kind": e.kind, "message": str(e)}
                    errors.append(err)
                    errors_f.write(json.dumps(err) + "\n")
                    if on_row:
                        on_row(None, err)
                    continue
                (transcripts / f"{case.id}.{rep}.json").write_text(json.dumps(row["events"], indent=1))
                row = {k: v for k, v in row.items() if k != "events"}
                rows.append(row)
                results_f.write(json.dumps(row) + "\n")
                if on_row:
                    on_row(row, None)
    summary = summarize(rows, errors, cases)
    summary["config"] = {"cases_file": str(cases_path), "reps": reps, "effort": effort, "model": get_settings().model}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
