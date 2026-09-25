"""Homework posted on a page per class, e.g. FRENLANG 1's "Week 1, Day 3" pages, as assignments.

Each class page lists what to do before that class ("Devoirs") and what happens in it ("En
classe"). The pages carry no dates, so a per-course line in Settings (CLASS_SCHEDULES) says which
days the class meets, when, and from which week: "FRENLANG 1=Mon-Fri 9:30 from 2026-09-21".

Like lectures, these assignments are derived: rebuilt from the stored pages and the schedule after
every sync. Ticks ("done") are the student's own and live in `assignment_done`, keyed by the
assignment's stable (source, external_id), so rebuilds keep them.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import get_settings
from ..db import now_iso
from ..store import upsert_assignment
from ..util import course_codes, iso, parse_dt

PREFIX = "devoirs:"  # external_id prefix of class-page homework, after the page's own external_id

_CLASS_TITLE_RE = re.compile(r"\b(?:week|semaine)\s*(\d{1,2})\W+(?:day|jour)\s*(\d{1,2})\b", re.IGNORECASE)
_HOMEWORK_RE = re.compile(r"devoirs?|before class|homework|[àa] faire|to do", re.IGNORECASE)
_IN_CLASS_RE = re.compile(r"en classe|in[- ]class", re.IGNORECASE)


@dataclass
class ClassPage:
    week: int
    day: int
    label: str                # "Devoirs" or "Homework": how the assignment is named
    homework: str | None      # the tasks, as Markdown; None when nothing is due
    template_ok: bool = True  # False when no homework heading was found at all


def _heading(line: str) -> str | None:
    """The text of a section heading ("**En classe (…):**", "## Devoirs", "Before class:"); None for
    anything else, list items included."""
    raw = line.strip()
    if not raw or re.match(r"[*+-]\s", raw):
        return None
    text = raw.strip("*_# ").strip()
    looks_like_heading = raw.startswith(("**", "__", "#")) or raw.rstrip("*_ ").endswith(":")
    return text if looks_like_heading and text and len(text) <= 120 else None


def parse_class_page(title: str, markdown: str | None) -> ClassPage | None:
    """None when the page isn't a class page."""
    m = _CLASS_TITLE_RE.search(title or "")
    if not m:
        return None
    week, day = int(m.group(1)), int(m.group(2))
    lines = (markdown or "").splitlines()
    start = next((i for i, line in enumerate(lines)
                  if (h := _heading(line)) and _HOMEWORK_RE.search(h) and not _IN_CLASS_RE.search(h)), None)
    if start is None:
        return ClassPage(week, day, "Homework", None, template_ok=False)
    heading = _heading(lines[start]) or ""
    label = "Devoirs" if "devoir" in heading.lower() else "Homework"
    body: list[str] = []
    # "**Homework:** read pages 20-22": tasks on the heading line itself.
    inline = lines[start].split(":", 1)[1].strip().strip("*_ ").strip() if ":" in lines[start] else ""
    if inline:
        body.append(inline)
    for line in lines[start + 1:]:
        h = _heading(line)
        if h and _IN_CLASS_RE.search(h):
            break
        body.append(line.rstrip())
    text = "\n".join(line for line in body if not re.fullmatch(r"\s*[-–—]+\s*", line)).strip()
    return ClassPage(week, day, label, text or None)


# ---------------------------------------------------------------- when a class meets

_WEEKDAYS = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2, "thu": 3,
             "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5, "sun": 6,
             "sunday": 6}
_LETTERS = {"m": 0, "t": 1, "tu": 1, "w": 2, "th": 3, "r": 3, "f": 4, "sa": 5, "su": 6}
_LINE_RE = re.compile(
    r"^\s*(?P<code>[^=]+?)\s*=\s*(?P<days>\S+)\s+(?P<time>\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?)"
    r"\s+from\s+(?P<start>\d{4}-\d{2}-\d{2})\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Calendar:
    weekdays: tuple[int, ...]  # Monday = 0
    time: time
    week1: date                # the Monday of the first week of classes

    def class_start(self, week: int, day: int, tz: ZoneInfo) -> datetime | None:
        """When the `day`-th class of `week` starts; None when the class doesn't meet that often."""
        if week < 1 or not 1 <= day <= len(self.weekdays):
            return None
        on = self.week1 + timedelta(weeks=week - 1, days=self.weekdays[day - 1])
        return datetime.combine(on, self.time, tzinfo=tz)


def _weekdays(spec: str) -> tuple[int, ...]:
    s = spec.strip().lower()
    if m := re.fullmatch(r"([a-z]+)-([a-z]+)", s):  # Mon-Fri
        a, b = _WEEKDAYS.get(m.group(1)), _WEEKDAYS.get(m.group(2))
        if a is not None and b is not None and a <= b:
            return tuple(range(a, b + 1))
    parts = [p for p in re.split(r"[/,+]", s) if p]
    if len(parts) > 1 and all(p in _WEEKDAYS for p in parts):  # Mon/Wed/Fri
        return tuple(sorted({_WEEKDAYS[p] for p in parts}))
    letters = re.findall(r"th|tu|sa|su|[mtwrf]", s)  # MWF, TTh, MTWRF
    if letters and "".join(letters) == s:
        return tuple(sorted({_LETTERS[x] for x in letters}))
    raise ValueError(f"can't read the days {spec!r} (try Mon-Fri, MWF, TTh or Mon/Wed)")


def _time(spec: str) -> time:
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?m?\.?", spec.strip().lower())
    if not m:
        raise ValueError(f"can't read the time {spec!r}")
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "p" and hour < 12:
        hour += 12
    elif m.group(3) == "a" and hour == 12:
        hour = 0
    elif not m.group(3) and 1 <= hour <= 6:  # "1:30" without am/pm: classes don't meet at 1:30 AM
        hour += 12
    return time(hour, minute)


def parse_calendars(text: str) -> tuple[dict[str, Calendar], list[str]]:
    """"FRENLANG 1=Mon-Fri 9:30 from 2026-09-21; …" -> ({code key: Calendar}, [problems])."""
    calendars: dict[str, Calendar] = {}
    errors: list[str] = []
    for part in re.split(r"[;\n]", text or ""):
        if not part.strip():
            continue
        m = _LINE_RE.match(part)
        codes = course_codes(m.group("code")) if m else []
        if not m or not codes:
            errors.append(f"{part.strip()!r}: write it as “FRENLANG 1=Mon-Fri 9:30 from 2026-09-21”")
            continue
        try:
            start = date.fromisoformat(m.group("start"))
            calendars[codes[0][1]] = Calendar(_weekdays(m.group("days")), _time(m.group("time")),
                                              start - timedelta(days=start.weekday()))
        except ValueError as e:
            errors.append(f"{part.strip()!r}: {e}")
    return calendars, errors


# ---------------------------------------------------------------- the assignments

def _status(due_at: str | None, done: bool, now: datetime) -> str:
    if done:
        return "done"
    due = parse_dt(due_at)
    return "upcoming" if due is None or due > now else "past"


def rebuild_class_homework(conn: sqlite3.Connection, course_id: int | None = None, *,
                           now: datetime | None = None) -> list[str]:
    """Recompute class-page homework for one course (or all). Returns warnings for the sync status."""
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    calendars, _ = parse_calendars(settings.class_schedules)
    done = {r["external_id"] for r in conn.execute("SELECT external_id FROM assignment_done WHERE source = 'canvas'")}
    courses = conn.execute("SELECT id, code, code_key FROM courses" + (" WHERE id = ?" if course_id else ""),
                           (course_id,) if course_id else ()).fetchall()
    warnings: list[str] = []
    for course in courses:
        calendar = calendars.get(course["code_key"])
        keep: set[str] = set()
        undated = False
        for page in conn.execute(
            "SELECT id, external_id, title, url, markdown FROM resources"
            " WHERE course_id = ? AND source = 'canvas' AND kind = 'page' ORDER BY id", (course["id"],)
        ).fetchall():
            parsed = parse_class_page(page["title"], page["markdown"])
            if parsed is None:
                continue
            if not parsed.template_ok:
                warnings.append(f"{course['code']}: no homework section found on “{page['title']}”; "
                                "its layout may have changed.")
            if parsed.homework is None:
                continue
            start = calendar.class_start(parsed.week, parsed.day, settings.tz) if calendar else None
            undated |= start is None
            external_id = PREFIX + page["external_id"]
            due_at = iso(start)
            keep.add(external_id)
            upsert_assignment(
                conn, course_id=course["id"], source="canvas", external_id=external_id,
                title=f"{parsed.label} · Week {parsed.week}, Day {parsed.day}", due_at=due_at, points=None,
                score=None, status=_status(due_at, external_id in done, now), url=page["url"],
                spec_resource_id=page["id"],
            )
        for row in conn.execute("SELECT id, external_id FROM assignments WHERE course_id = ? AND source = 'canvas'"
                                " AND external_id LIKE ?", (course["id"], PREFIX + "%")).fetchall():
            if row["external_id"] not in keep:
                conn.execute("DELETE FROM assignments WHERE id = ?", (row["id"],))
        if undated:
            warnings.append(f"Add {course['code']}'s class days in Settings → Class schedules to date its homework.")
    conn.commit()
    return warnings


def is_class_homework(external_id: str) -> bool:
    return external_id.startswith(PREFIX)


def mark_done(conn: sqlite3.Connection, assignment_id: int, done: bool, *, now: datetime | None = None) -> None:
    """Tick class-page homework off (or back on). Other assignments get their status from submissions."""
    row = conn.execute("SELECT source, external_id, due_at FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    if row is None or not is_class_homework(row["external_id"]):
        raise ValueError("Only homework from class pages can be ticked off here.")
    if done:
        conn.execute("INSERT OR IGNORE INTO assignment_done(source, external_id, done_at) VALUES (?, ?, ?)",
                     (row["source"], row["external_id"], now_iso()))
    else:
        conn.execute("DELETE FROM assignment_done WHERE source = ? AND external_id = ?",
                     (row["source"], row["external_id"]))
    conn.execute("UPDATE assignments SET status = ? WHERE id = ?",
                 (_status(row["due_at"], done, now or datetime.now(timezone.utc)), assignment_id))
    conn.commit()
