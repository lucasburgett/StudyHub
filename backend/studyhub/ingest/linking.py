"""Group a course's items into lectures.

Lectures are derived data, rebuilt from scratch after every sync so the result never
depends on sync order. Signals, strongest first:

0. The imported course schedule (lecture number, date, title), if there is one.
1. A Granola recording happened on a date -> there was a lecture that day.
2. A GoodNotes page has a date written near its top -> that page belongs to that day's lecture.
3. A Canvas file, module or course-site row says "Lecture N" -> it belongs to lecture N (a
   course-site row also gives the lecture's date). If a dated lecture
   without a number sits just after the file's upload date, they're the same lecture.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..config import get_settings
from ..store import refresh_headers
from ..util import course_codes, find_date, lecture_number, local_date

NUMBERED_KINDS = ("slides", "file", "page")


@dataclass
class _Lecture:
    number: int | None = None
    date: str | None = None
    title: str | None = None
    resources: set[int] = field(default_factory=set)
    chunks: set[int] = field(default_factory=set)


# "2026F", "F26", "Autumn 2026": the term a file was made for, not part of its title.
_TERM_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:(?:fall|autumn|aut|winter|win|spring|spr|summer|sum)[\s_\-]*20\d{2}"
    r"|20\d{2}[\s_\-]*(?:fall|autumn|winter|spring|summer|[FWSU](?![a-z]))|[FWSU]\d{2})(?![0-9])",
    re.IGNORECASE,
)


def clean_lecture_title(text: str | None, course_code: str | None = None) -> str | None:
    """"cs231n_lecture_03_loss-functions.pdf" -> "Loss functions"."""
    if not text:
        return None
    t = re.sub(r"\.(pdf|pptx?|key|docx?)$", "", text, flags=re.IGNORECASE)
    codes = course_codes(t)
    if course_code:  # this course's own code, even glued to the term: "2026FMath115Lecture01"
        codes += [(course_code, re.sub(r"\s+", "", course_code))]
    for display, key in codes:
        t = re.sub(re.escape(key), " ", t, flags=re.IGNORECASE)
        t = re.sub(re.escape(display), " ", t, flags=re.IGNORECASE)
    t = _TERM_RE.sub(" ", t)
    t = re.sub(r"(?<![A-Za-z])lec(?:ture)?s?[\s_\-#.]*\d{1,2}(?!\d)", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(slides?|notes?|annotated|handout|final|v\d+)\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"[_\-:|·]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .,")
    if len(t) < 3 or t.isdigit():
        return None
    return t[0].upper() + t[1:]


def _title_slide(conn: sqlite3.Connection, resource_id: int, number: int, year: int) -> tuple[str | None, str | None]:
    """Date and topic from a deck's first lines, when one of them names this lecture:
    "Math 115, Lecture 1 (September 22, 2026): Introduction"."""
    first = conn.execute("SELECT text FROM chunks WHERE resource_id = ? ORDER BY page, seq LIMIT 1",
                         (resource_id,)).fetchone()
    lines = [line.strip() for line in (first["text"] if first else "").splitlines() if line.strip()][:3]
    for i, line in enumerate(lines):
        if lecture_number(line) != number:
            continue
        day = find_date(line, year)
        topic = line.rsplit(":", 1)[1].strip() if ":" in line else ""
        if not topic and i + 1 < len(lines):  # "…♢ Lecture 2" then "The Real Numbers" on its own line
            topic = lines[i + 1] if len(lines[i + 1]) <= 80 and not lines[i + 1].endswith(".") else ""
        return (day.isoformat() if day else None), (topic if re.match(r"[A-Z][A-Za-z].{1,}", topic) else None)
    return None, None


def _generic_title(title: str | None, course_code: str | None = None) -> bool:
    if not title:
        return True
    rest = clean_lecture_title(title, course_code)
    return rest is None or rest.lower() in {"class", "lecture", "meeting", "untitled", "new note"}


def rebuild_lectures(conn: sqlite3.Connection, course_id: int) -> int:
    """Recompute lectures for a course. Returns the number of lectures."""
    tz = get_settings().tz
    code = (conn.execute("SELECT code FROM courses WHERE id = ?", (course_id,)).fetchone() or {"code": None})["code"]
    lectures: list[_Lecture] = []
    by_date: dict[str, _Lecture] = {}
    by_number: dict[int, _Lecture] = {}

    def at_date(day: str) -> _Lecture:
        if day not in by_date:
            lec = _Lecture(date=day)
            lectures.append(lec)
            by_date[day] = lec
        return by_date[day]

    # 0. The course schedule, when imported, fixes numbers, dates and titles up front.
    for row in conn.execute("SELECT number, date, title FROM schedule WHERE course_id = ? ORDER BY number", (course_id,)):
        lec = at_date(row["date"]) if row["date"] else _Lecture()
        if not row["date"]:
            lectures.append(lec)
        lec.number, lec.title = row["number"], row["title"]
        by_number[row["number"]] = lec

    # 1. Recordings anchor lecture days.
    for r in conn.execute(
        "SELECT id, title, occurred_at FROM resources WHERE course_id = ? AND kind = 'transcript'", (course_id,)
    ):
        day = local_date(r["occurred_at"], tz)
        if not day:
            continue
        lec = at_date(day)
        lec.resources.add(r["id"])
        n = lecture_number(r["title"])
        if n is not None and lec.number is None and n not in by_number:
            lec.number = n
            by_number[n] = lec
        if not lec.title and not _generic_title(r["title"], code):
            lec.title = clean_lecture_title(r["title"], code)

    # 2. Dated handwritten pages. A date in a page's first two lines starts that day's notes;
    #    following undated pages continue the same day.
    for r in conn.execute(
        "SELECT id, occurred_at FROM resources WHERE course_id = ? AND kind = 'notes'", (course_id,)
    ).fetchall():
        year = int((local_date(r["occurred_at"], tz) or str(date.today()))[:4])
        current: _Lecture | None = None
        for c in conn.execute(
            "SELECT id, page, text FROM chunks WHERE resource_id = ? ORDER BY page", (r["id"],)
        ):
            found = find_date("\n".join(c["text"].splitlines()[:2]), year)
            if found:
                current = at_date(found.isoformat())
            if current is not None:
                current.chunks.add(c["id"])

    # 3. Numbered Canvas and course-site items. A course site's schedule row also gives the date.
    numbered = []
    for r in conn.execute(
        f"SELECT id, title, occurred_at, meta_json FROM resources WHERE course_id = ? AND kind IN "
        f"({', '.join('?' for _ in NUMBERED_KINDS)})",
        (course_id, *NUMBERED_KINDS),
    ):
        meta = json.loads(r["meta_json"] or "{}")
        n = lecture_number(r["title"]) or lecture_number(meta.get("module"))
        if n is None:
            continue
        hint = local_date(r["occurred_at"], tz)
        slide_date, slide_title = _title_slide(conn, r["id"], n, int((hint or date.today().isoformat())[:4]))
        title = slide_title or clean_lecture_title(r["title"], code) or clean_lecture_title(meta.get("module"), code)
        numbered.append((n, hint, meta.get("lecture_date") or slide_date, title, r["id"]))

    for n, hint, lecture_date, title, rid in sorted(numbered, key=lambda x: (x[0], x[1] or "")):
        lec = by_number.get(n)
        if lec is None and lecture_date and lecture_date in by_date and by_date[lecture_date].number is None:
            lec = by_date[lecture_date]
            lec.number = n
        if lec is None and hint and not lecture_date:
            lec = _nearest_unnumbered(lectures, hint)
            if lec is not None:
                lec.number = n
        if lec is None:
            lec = _Lecture(number=n)
            lectures.append(lec)
        if lec.date is None and lecture_date and lecture_date not in by_date:
            lec.date = lecture_date
            by_date[lecture_date] = lec
        by_number[n] = lec
        lec.resources.add(rid)
        if title and not lec.title:
            lec.title = title

    # Write the result.
    conn.execute("UPDATE resources SET lecture_id = NULL WHERE course_id = ?", (course_id,))
    conn.execute(
        "UPDATE chunks SET lecture_id = NULL WHERE resource_id IN (SELECT id FROM resources WHERE course_id = ?)",
        (course_id,),
    )
    conn.execute("DELETE FROM lectures WHERE course_id = ?", (course_id,))
    lectures.sort(key=lambda lec: (lec.date or "9999", lec.number or 0))
    for lec in lectures:
        lecture_id = conn.execute(
            "INSERT INTO lectures(course_id, number, date, title) VALUES (?, ?, ?, ?)",
            (course_id, lec.number, lec.date, lec.title),
        ).lastrowid
        for rid in lec.resources:
            conn.execute("UPDATE resources SET lecture_id = ? WHERE id = ?", (lecture_id, rid))
            conn.execute("UPDATE chunks SET lecture_id = ? WHERE resource_id = ?", (lecture_id, rid))
        for cid in lec.chunks:
            conn.execute("UPDATE chunks SET lecture_id = ? WHERE id = ?", (lecture_id, cid))

    for r in conn.execute("SELECT id FROM resources WHERE course_id = ?", (course_id,)).fetchall():
        refresh_headers(conn, r["id"])
    return len(lectures)


def _nearest_unnumbered(lectures: list[_Lecture], hint: str) -> _Lecture | None:
    """The first unnumbered dated lecture from one day before the upload date to four days after."""
    start = date.fromisoformat(hint) - timedelta(days=1)
    end = date.fromisoformat(hint) + timedelta(days=4)
    candidates = [
        lec for lec in lectures
        if lec.number is None and lec.date and start <= date.fromisoformat(lec.date) <= end
    ]
    return min(candidates, key=lambda lec: lec.date) if candidates else None
