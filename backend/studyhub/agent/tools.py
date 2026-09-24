"""Read-only tools the agent can call. Every result prints locators the model can cite."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from ..citations import citation, parse_locator, resource_locator
from ..config import SOURCES, get_settings
from ..search import search, snippet_plain
from ..store import find_course
from ..util import clock, parse_clock, parse_dt

MAX_READ_CHARS = 80_000
MAX_LECTURE_CHARS = 120_000

Source = Literal["canvas", "gradescope", "goodnotes", "granola"]
Kind = Literal["slides", "file", "page", "spec", "notes", "transcript", "submission", "announcement"]
Status = Literal["upcoming", "submitted", "graded", "missing", "unknown"]


@dataclass
class ToolResult:
    text: str
    summary: str
    citations: dict[str, dict] = field(default_factory=dict)
    is_error: bool = False


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- inputs

class SearchIn(BaseModel, extra="forbid"):
    query: str = Field(min_length=1, description="Words to look for, e.g. 'dropout regularization'.")
    course: str | None = Field(None, description="Course code like 'CS 231N'. Omit to search every class.")
    sources: list[Source] | None = Field(None, description="Limit to these sources.")
    kinds: list[Kind] | None = Field(None, description="Limit to these kinds of material.")
    limit: int = Field(8, ge=1, le=20)


class ReadIn(BaseModel, extra="forbid"):
    locator: str = Field(description="A resource locator from another tool: r42, r42#p27 or r42@41:12.")
    start: str | None = Field(None, description="First page (PDFs, e.g. '27') or time (recordings, e.g. '40:00').")
    end: str | None = Field(None, description="Last page or time, inclusive.")


class LectureIn(BaseModel, extra="forbid"):
    course: str = Field(description="Course code like 'CS 231N'.")
    number: int | None = Field(None, description="Lecture number.")
    date: str | None = Field(None, description="Lecture date, YYYY-MM-DD.")


class AssignmentsIn(BaseModel, extra="forbid"):
    course: str | None = Field(None, description="Course code. Omit for every class.")
    status: list[Status] | None = None
    due_after: str | None = Field(None, description="ISO date or datetime.")
    due_before: str | None = Field(None, description="ISO date or datetime.")


class FeedbackIn(BaseModel, extra="forbid"):
    assignment: str = Field(description="Assignment locator like a7.")


class ResourcesIn(BaseModel, extra="forbid"):
    course: str = Field(description="Course code like 'CS 231N'.")
    kinds: list[Kind] | None = None
    lecture: int | None = Field(None, description="Only items linked to this lecture number.")


class AnnouncementsIn(BaseModel, extra="forbid"):
    course: str | None = None
    since: str | None = Field(None, description="ISO date; only announcements posted after it.")
    limit: int = Field(10, ge=1, le=30)


class SyncIn(BaseModel, extra="forbid"):
    source: Source


@dataclass
class _Tool:
    name: str
    description: str
    model: type[BaseModel]
    run: Callable[[Any], ToolResult]
    label: Callable[[Any], str]


def _schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return schema


class Toolbox:
    def __init__(self, conn: sqlite3.Connection, run_sync: Callable[[str], dict] | None = None):
        self.conn = conn
        self.run_sync = run_sync
        self.tz = get_settings().tz
        self.tools = [
            _Tool("search",
                  "Keyword search across synced course materials: lecture recordings, slides, handwritten notes, "
                  "Canvas pages, assignment descriptions and announcements. Returns short excerpts with locators. "
                  "Use it to find where something was covered, then `read` the best hits for full context.",
                  SearchIn, self._search,
                  lambda a: f"Searched {a.course or 'all classes'} for “{a.query}”"),
            _Tool("read",
                  "Read a resource in full, or a page/time range of it. Use after search, or with a locator from the "
                  "course map. Returns text with a locator on every page or transcript window.",
                  ReadIn, self._read, self._read_label),
            _Tool("get_lecture",
                  "Everything for one lecture in one call: the recording transcript, the slides, and the student's "
                  "notes pages linked to that lecture. Give a lecture number or a date.",
                  LectureIn, self._lecture,
                  lambda a: f"Opened {a.course} lecture {a.number if a.number is not None else a.date}"),
            _Tool("list_assignments",
                  "Assignments with due dates, status and scores, from Canvas and Gradescope. Use for deadlines and "
                  "grades instead of search.",
                  AssignmentsIn, self._assignments,
                  lambda a: f"Checked assignments{' in ' + a.course if a.course else ''}"),
            _Tool("get_feedback",
                  "Graded feedback for one assignment: per-question scores, rubric items applied, and comments.",
                  FeedbackIn, self._feedback, self._feedback_label),
            _Tool("list_resources",
                  "Catalog of a course's materials (titles, dates, lectures, locators) without their content.",
                  ResourcesIn, self._resources, lambda a: f"Listed {a.course} materials"),
            _Tool("list_announcements",
                  "Recent course announcements with their text.",
                  AnnouncementsIn, self._announcements,
                  lambda a: f"Checked {a.course + ' ' if a.course else ''}announcements"),
            _Tool("sync_now",
                  "Pull fresh data from one source before answering, when the student says something is new or "
                  "the data looks stale. Takes up to a minute.",
                  SyncIn, self._sync, lambda a: f"Synced {a.source}"),
        ]
        self.by_name = {t.name: t for t in self.tools}

    # ------------------------------------------------------------ plumbing

    def definitions(self) -> list[dict]:
        """Tool definitions in a fixed order, so the prompt prefix stays cacheable."""
        return [
            {"name": t.name, "description": t.description, "input_schema": _schema(t.model),
             "eager_input_streaming": True}
            for t in self.tools
        ]

    def parse(self, name: str, raw: Any) -> BaseModel:
        tool = self.by_name.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool {name}.")
        try:
            return tool.model.model_validate(raw)
        except ValidationError as e:
            raise ToolError(json.dumps({"INVALID_INPUT": json.dumps(raw), "errors": e.errors(include_url=False)},
                                       default=str)) from e

    def label(self, name: str, args: BaseModel) -> str:
        return self.by_name[name].label(args)

    def run(self, name: str, args: BaseModel) -> ToolResult:
        try:
            return self.by_name[name].run(args)
        except ToolError as e:
            return ToolResult(text=str(e), summary="error", is_error=True)

    def _cite(self, out: dict[str, dict], locator: str) -> str:
        if locator not in out:
            c = citation(self.conn, locator)
            if c:
                out[locator] = c
        return locator

    def course_id(self, text: str | None) -> int | None:
        if text is None:
            return None
        if text.strip().isdigit():
            row = self.conn.execute("SELECT id FROM courses WHERE id = ?", (int(text),)).fetchone()
            if row:
                return row["id"]
        cid = find_course(self.conn, text)
        if cid is None:
            row = self.conn.execute(
                "SELECT id FROM courses WHERE title LIKE ? ORDER BY id LIMIT 1", (f"%{text.strip()}%",)
            ).fetchone()
            cid = row["id"] if row else None
        if cid is None:
            codes = ", ".join(r["code"] for r in self.conn.execute("SELECT code FROM courses ORDER BY code"))
            raise ToolError(f"No course matches {text!r}. Courses: {codes or 'none synced yet'}.")
        return cid

    def _local(self, iso_value: str | None, fmt: str = "%a %b %-d, %-I:%M %p") -> str:
        dt = parse_dt(iso_value)
        return dt.astimezone(self.tz).strftime(fmt) if dt else "no date"

    def _resource(self, resource_id: int) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT r.*, c.code AS course_code, l.number AS lecture_number FROM resources r"
            " JOIN courses c ON c.id = r.course_id LEFT JOIN lectures l ON l.id = r.lecture_id WHERE r.id = ?",
            (resource_id,),
        ).fetchone()
        if not row:
            raise ToolError(f"No resource r{resource_id}.")
        return row

    # ------------------------------------------------------------ tools

    def _search(self, a: SearchIn) -> ToolResult:
        hits = search(self.conn, a.query, course_id=self.course_id(a.course), sources=a.sources, kinds=a.kinds,
                      limit=a.limit, snippet_tokens=48)
        if not hits:
            return ToolResult(text="No matches. Try other words, or list_resources to browse.", summary="no hits")
        cites: dict[str, dict] = {}
        lines = []
        for h in hits:
            loc = self._cite(cites, resource_locator(h.resource_id, h.page, h.seconds))
            self._cite(cites, f"r{h.resource_id}")
            lines.append(f"[{loc}] {h.header}\n{snippet_plain(h.snippet)}")
        return ToolResult(text="\n\n".join(lines), summary=f"{len(hits)} hits", citations=cites)

    def _read_label(self, a: ReadIn) -> str:
        loc = parse_locator(a.locator)
        if loc and loc.resource_id:
            row = self.conn.execute("SELECT title FROM resources WHERE id = ?", (loc.resource_id,)).fetchone()
            if row:
                return f"Read {row['title']}"
        return f"Read {a.locator}"

    def _read(self, a: ReadIn) -> ToolResult:
        loc = parse_locator(a.locator)
        if loc is None or loc.resource_id is None:
            raise ToolError("read takes a resource locator such as r42, r42#p27 or r42@41:12.")
        res = self._resource(loc.resource_id)
        start, end = a.start, a.end
        if start is None and loc.page is not None:
            start, end = str(loc.page), str(loc.page + 2)
        if start is None and loc.seconds is not None:
            start, end = clock(max(loc.seconds - 60, 0)), clock(loc.seconds + 300)
        cites: dict[str, dict] = {}
        text, summary = self._render(res, cites, start, end, MAX_READ_CHARS)
        return ToolResult(text=text, summary=summary, citations=cites)

    def _render(self, res: sqlite3.Row, cites: dict[str, dict], start: str | None, end: str | None,
                budget: int, only_chunks: set[int] | None = None) -> tuple[str, str]:
        rid = res["id"]
        head = f"# [{self._cite(cites, f'r{rid}')}] {res['course_code']} · {res['title']} ({res['kind']}"
        head += f", lecture {res['lecture_number']})" if res["lecture_number"] is not None else ")"
        if res["occurred_at"]:
            head += f" · {self._local(res['occurred_at'])}"
        parts = [head]
        if res["summary"] and start is None:
            parts.append(f"Summary:\n{res['summary']}")

        chunks = self.conn.execute(
            "SELECT id, page, seconds, text FROM chunks WHERE resource_id = ? ORDER BY seq", (rid,)
        ).fetchall()
        if only_chunks is not None:
            chunks = [c for c in chunks if c["id"] in only_chunks]
        lo = hi = None
        if res["kind"] == "transcript":
            lo = parse_clock(start) if start else None
            hi = parse_clock(end) if end else None
        elif start and start.isdigit():
            lo, hi = int(start), int(end) if end and end.isdigit() else None

        used = sum(len(p) for p in parts)
        shown = 0
        truncated_at = None
        for c in chunks:
            pos = c["seconds"] if res["kind"] == "transcript" else c["page"]
            if pos is not None and ((lo is not None and pos < lo) or (hi is not None and pos > hi)):
                continue
            if c["page"] is not None:
                loc = resource_locator(rid, page=c["page"])
            elif c["seconds"] is not None:
                loc = resource_locator(rid, seconds=c["seconds"])
            else:
                loc = f"r{rid}"
            block = f"[{self._cite(cites, loc)}]\n{c['text']}"
            if used + len(block) > budget and shown:
                truncated_at = c["page"] if c["page"] is not None else (clock(c["seconds"]) if c["seconds"] is not None else None)
                break
            parts.append(block)
            used += len(block)
            shown += 1
        if not chunks and res["markdown"]:
            parts.append(res["markdown"][:budget])
        if not shown and not res["markdown"]:
            parts.append("(No text for this item. It may be a non-PDF file; open it from the app.)")
        if truncated_at is not None:
            parts.append(f"[Stopped at {truncated_at} to save space. Call read with start={truncated_at} to continue.]")
        if res["kind"] == "transcript":
            summary = f"{res['duration_min'] or '?'} min" if lo is None else f"{start}–{end or 'end'}"
        elif res["page_count"]:
            summary = f"{shown} of {res['page_count']} pages"
        else:
            summary = f"{shown} sections"
        return "\n\n".join(parts), summary

    def _lecture(self, a: LectureIn) -> ToolResult:
        cid = self.course_id(a.course)
        if a.number is None and a.date is None:
            raise ToolError("Give a lecture number or a date.")
        row = None
        if a.number is not None:
            row = self.conn.execute("SELECT * FROM lectures WHERE course_id = ? AND number = ?", (cid, a.number)).fetchone()
        if row is None and a.date:
            row = self.conn.execute("SELECT * FROM lectures WHERE course_id = ? AND date = ?", (cid, a.date[:10])).fetchone()
        if row is None:
            known = self.conn.execute(
                "SELECT number, date, title FROM lectures WHERE course_id = ? ORDER BY COALESCE(date, '9999'), number",
                (cid,),
            ).fetchall()
            listing = "; ".join(
                f"{'L' + str(k['number']) if k['number'] is not None else '?'} {k['date'] or ''} {k['title'] or ''}".strip()
                for k in known
            )
            raise ToolError(f"No such lecture. Known lectures: {listing or 'none yet'}.")
        cites: dict[str, dict] = {}
        name = f"Lecture {row['number']}" if row["number"] is not None else f"Lecture on {row['date']}"
        parts = [f"## {name}{': ' + row['title'] if row['title'] else ''}{' · ' + row['date'] if row['date'] else ''}"]
        budget = MAX_LECTURE_CHARS
        order = {"transcript": 0, "slides": 1, "notes": 2}
        resources = self.conn.execute(
            "SELECT DISTINCT r.id, r.kind FROM resources r LEFT JOIN chunks c ON c.resource_id = r.id"
            " WHERE r.lecture_id = ? OR c.lecture_id = ?",
            (row["id"], row["id"]),
        ).fetchall()
        missing = [k for k in ("transcript", "slides", "notes") if k not in {r["kind"] for r in resources}]
        for r in sorted(resources, key=lambda r: order.get(r["kind"], 3)):
            res = self._resource(r["id"])
            only = None
            if res["lecture_id"] != row["id"]:  # a notebook that spans lectures: just this lecture's pages
                only = {c["id"] for c in self.conn.execute(
                    "SELECT id FROM chunks WHERE resource_id = ? AND lecture_id = ?", (r["id"], row["id"]))}
            text, _ = self._render(res, cites, None, None, max(budget, 4000), only)
            budget -= len(text)
            parts.append(text)
        if missing:
            names = {"transcript": "recording", "slides": "slides", "notes": "handwritten notes"}
            parts.append("Not synced for this lecture: " + ", ".join(names[m] for m in missing) + ".")
        return ToolResult(text="\n\n".join(parts), summary=f"{len(resources)} items", citations=cites)

    def _assignments(self, a: AssignmentsIn) -> ToolResult:
        where, params = ["a.hidden = 0"], []
        cid = self.course_id(a.course)
        if cid is not None:
            where.append("a.course_id = ?")
            params.append(cid)
        if a.status:
            where.append(f"a.status IN ({', '.join('?' for _ in a.status)})")
            params += a.status
        if a.due_after:
            where.append("a.due_at >= ?")
            params.append(a.due_after)
        if a.due_before:
            where.append("a.due_at <= ?")
            params.append(a.due_before if "T" in a.due_before else a.due_before + "T23:59:59Z")
        rows = self.conn.execute(
            "SELECT a.*, c.code, (SELECT COUNT(*) FROM feedback f WHERE f.assignment_id = a.id) AS n_feedback"
            f" FROM assignments a JOIN courses c ON c.id = a.course_id WHERE {' AND '.join(where)}"
            " ORDER BY a.due_at IS NULL, a.due_at",
            params,
        ).fetchall()
        if not rows:
            return ToolResult(text="No assignments match.", summary="none")
        cites: dict[str, dict] = {}
        lines = []
        for r in rows:
            loc = self._cite(cites, f"a{r['id']}")
            score = f" · {r['score']:g}/{r['points']:g}" if r["score"] is not None and r["points"] else (
                f" · {r['points']:g} pts" if r["points"] else "")
            extra = []
            if r["spec_resource_id"]:
                extra.append(f"description {self._cite(cites, 'r' + str(r['spec_resource_id']))}")
            if r["n_feedback"]:
                extra.append("feedback available")
            lines.append(
                f"[{loc}] {r['code']} · {r['title']} · due {self._local(r['due_at'])} · {r['status']}{score}"
                f" · {r['source']}" + (f" ({', '.join(extra)})" if extra else "")
            )
        now = datetime.now(self.tz).strftime("%a %b %-d, %-I:%M %p")
        return ToolResult(text=f"Now: {now}\n" + "\n".join(lines), summary=f"{len(rows)} assignments", citations=cites)

    def _feedback_label(self, a: FeedbackIn) -> str:
        loc = parse_locator(a.assignment)
        if loc and loc.assignment_id:
            row = self.conn.execute("SELECT title FROM assignments WHERE id = ?", (loc.assignment_id,)).fetchone()
            if row:
                return f"Read feedback on {row['title']}"
        return "Read feedback"

    def _feedback(self, a: FeedbackIn) -> ToolResult:
        loc = parse_locator(a.assignment)
        if loc is None or loc.assignment_id is None:
            raise ToolError("get_feedback takes an assignment locator such as a7.")
        asg = self.conn.execute(
            "SELECT a.*, c.code FROM assignments a JOIN courses c ON c.id = a.course_id WHERE a.id = ?",
            (loc.assignment_id,),
        ).fetchone()
        if not asg:
            raise ToolError(f"No assignment a{loc.assignment_id}.")
        cites: dict[str, dict] = {}
        head = f"[{self._cite(cites, 'a' + str(asg['id']))}] {asg['code']} · {asg['title']} · {asg['status']}"
        if asg["score"] is not None:
            head += f" · {asg['score']:g}/{asg['points']:g}" if asg["points"] else f" · score {asg['score']:g}"
        items = self.conn.execute("SELECT * FROM feedback WHERE assignment_id = ? ORDER BY seq", (asg["id"],)).fetchall()
        if not items:
            return ToolResult(text=head + "\nNo per-question feedback synced. Totals only.", summary="totals only",
                              citations=cites)
        lines = [head]
        for f in items:
            loc_q = self._cite(cites, f"a{asg['id']}/q{f['seq']}")
            score = f" {f['score']:g}/{f['max_score']:g}" if f["score"] is not None and f["max_score"] is not None else ""
            lines.append(f"[{loc_q}] {f['question']}:{score}")
            for item in json.loads(f["rubric_items_json"]):
                lines.append(f"  - {item}")
            if f["comment"]:
                lines.append(f"  Comment: {f['comment']}")
        return ToolResult(text="\n".join(lines), summary=f"{len(items)} questions", citations=cites)

    def _resources(self, a: ResourcesIn) -> ToolResult:
        cid = self.course_id(a.course)
        where, params = ["r.course_id = ?"], [cid]
        if a.kinds:
            where.append(f"r.kind IN ({', '.join('?' for _ in a.kinds)})")
            params += a.kinds
        if a.lecture is not None:
            where.append("l.number = ?")
            params.append(a.lecture)
        rows = self.conn.execute(
            "SELECT r.id, r.kind, r.source, r.title, r.occurred_at, r.page_count, r.duration_min, l.number"
            f" FROM resources r LEFT JOIN lectures l ON l.id = r.lecture_id WHERE {' AND '.join(where)}"
            " ORDER BY r.kind, COALESCE(l.number, 999), r.occurred_at",
            params,
        ).fetchall()
        cites: dict[str, dict] = {}
        lines = []
        for r in rows:
            size = f"{r['page_count']} pp" if r["page_count"] else (f"{r['duration_min']} min" if r["duration_min"] else "")
            lec = f"L{r['number']} · " if r["number"] is not None else ""
            lines.append(f"[{self._cite(cites, 'r' + str(r['id']))}] {r['kind']} · {lec}{r['title']} · "
                         f"{self._local(r['occurred_at'], '%b %-d')}{' · ' + size if size else ''} ({r['source']})")
        return ToolResult(text="\n".join(lines) or "Nothing synced for this course yet.",
                          summary=f"{len(rows)} items", citations=cites)

    def _announcements(self, a: AnnouncementsIn) -> ToolResult:
        where, params = ["r.kind = 'announcement'"], []
        cid = self.course_id(a.course)
        if cid is not None:
            where.append("r.course_id = ?")
            params.append(cid)
        if a.since:
            where.append("r.occurred_at >= ?")
            params.append(a.since)
        rows = self.conn.execute(
            f"SELECT r.id, r.title, r.occurred_at, r.markdown, c.code FROM resources r JOIN courses c ON c.id = r.course_id"
            f" WHERE {' AND '.join(where)} ORDER BY r.occurred_at DESC LIMIT ?",
            (*params, a.limit),
        ).fetchall()
        cites: dict[str, dict] = {}
        blocks = [
            f"[{self._cite(cites, 'r' + str(r['id']))}] {r['code']} · {r['title']} · {self._local(r['occurred_at'])}\n"
            f"{(r['markdown'] or '')[:3000]}"
            for r in rows
        ]
        return ToolResult(text="\n\n".join(blocks) or "No announcements.", summary=f"{len(rows)} posts", citations=cites)

    def _sync(self, a: SyncIn) -> ToolResult:
        if self.run_sync is None or a.source not in SOURCES:
            raise ToolError("Syncing isn't available here.")
        if not get_settings().configured(a.source):
            raise ToolError(f"{a.source} isn't set up in backend/.env.")
        result = self.run_sync(a.source)
        if result.get("status") == "busy":
            return ToolResult(text=f"A {a.source} sync is already running; try again shortly.", summary="busy")
        if result.get("status") == "error":
            return ToolResult(text=f"Sync failed: {result.get('error')}", summary="failed", is_error=True)
        return ToolResult(text=f"Synced {a.source}: {result.get('items_changed', 0)} items changed.",
                          summary=f"{result.get('items_changed', 0)} changed")
