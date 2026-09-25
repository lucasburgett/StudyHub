"""Small shared helpers: course codes, dates, text."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Words that look like a course department in "WEEK 2" or "LECTURE 3" but never are.
_NOT_DEPARTMENTS = {
    "WEEK", "LEC", "LECT", "HW", "PS", "PSET", "SEC", "UNIT", "PART", "PAGE", "NOTE",
    "NOTES", "DAY", "QUIZ", "EXAM", "FINAL", "LAB", "LABS", "FALL", "TERM", "CLASS",
    "ROOM", "ASSGN", "PROB", "CH", "CHAP", "SLIDE", "SLIDES", "VOL", "NO", "PP", "PG",
    "SEPT", "SEP", "OCT", "NOV", "DEC", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL",
    "AUG", "MON", "TUE", "TUES", "WED", "THU", "THUR", "THURS", "FRI", "SAT", "SUN",
    "AM", "PM", "V", "Q", "QUESTION", "TOPIC", "MODULE", "HOUR", "HOURS", "MIN",
    # Stanford departments run to eight letters (APPPHYS, FRENLANG), so longer words need listing too.
    "LECTURE", "LECTURES", "SECTION", "SECTIONS", "CHAPTER", "CHAPTERS", "PROBLEM", "PROBLEMS",
    "HOMEWORK", "MIDTERM", "MIDTERMS", "SESSION", "SESSIONS", "PROJECT", "PROJECTS", "READING",
    "READINGS", "EXERCISE", "TUTORIAL", "SEMINAR", "HANDOUT", "HANDOUTS", "PRACTICE", "SOLUTION",
    "LESSON", "LESSONS", "REVIEW", "TEST", "TESTS", "FIGURE", "VERSION", "EPISODE", "NOTEBOOK",
    "RECORDING", "WEEKS", "UNITS", "PARTS", "PAGES", "DAYS", "CLASSES", "COURSE", "LEVEL", "STEP",
    "GROUP", "TEAM", "TABLE", "PHASE", "ROUND", "DEMO", "PAPER", "ITEM", "TASK", "NUMBER",
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "JUNE", "JULY", "AUGUST", "OCTOBER", "NOVEMBER",
    "DECEMBER", "MONDAY", "TUESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY",
}
_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{2,8})[\s\-_]*(\d{1,3}[A-Z]{0,3})(?![A-Z0-9])")


def course_codes(text: str | None) -> list[tuple[str, str]]:
    """All plausible course codes in `text`, as (display, key): ("CS 231N", "CS231N")."""
    if not text:
        return []
    found: list[tuple[str, str]] = []
    for dept, num in _CODE_RE.findall(text.upper()):
        if dept in _NOT_DEPARTMENTS:
            continue
        pair = (f"{dept} {num}", f"{dept}{num}")
        if pair not in found:
            found.append(pair)
    return found


def parse_dt(value: str | datetime | None) -> datetime | None:
    """Parse an ISO timestamp into an aware UTC datetime. Naive values are treated as UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(value: str | datetime | None) -> str | None:
    dt = parse_dt(value)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def local_date(value: str | datetime | None, tz: ZoneInfo) -> str | None:
    dt = parse_dt(value)
    return dt.astimezone(tz).date().isoformat() if dt else None


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def clock(seconds: int | float) -> str:
    """41:12 or 1:05:09."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_clock(text: str) -> int | None:
    parts = text.split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def normalize_space(text: str) -> str:
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def title_key(title: str) -> str:
    """Loose key for matching the same assignment across Canvas and Gradescope."""
    t = title.lower()
    t = re.sub(r"\b(problem set|pset)\b", "ps", t)
    t = re.sub(r"\bhomework\b", "hw", t)
    t = re.sub(r"\bassignment\b", "a", t)
    return re.sub(r"[^a-z0-9]", "", t)


# Only "lecture"/"lec": a bare "L3" is too easily "L2 regularization".
_LECTURE_NO_RE = re.compile(r"(?<![A-Za-z])lec(?:ture)?s?[\s_\-#.]*0*(\d{1,2})(?!\d)", re.IGNORECASE)


def lecture_number(text: str | None) -> int | None:
    """"Lecture 3", "lec03_loss.pdf", "CS231N_lecture_3" -> 3."""
    if not text:
        return None
    m = _LECTURE_NO_RE.search(text)
    return int(m.group(1)) if m else None


_MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
         ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
         ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december")]
    )
    for m in names
}
# Slashes only: "2.3" in notes is far more often a number than February 3rd.
_DATE_NUMERIC_RE = re.compile(r"(?<![\d./])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/])")
_DATE_ISO_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)")
_DATE_WORD_RE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(20\d{2}))?")


def find_date(text: str, default_year: int) -> date | None:
    """First date written in text: 9/24, 2026-09-24, Sep 24, September 24th 2026."""
    candidates: list[tuple[int, date]] = []
    for m in _DATE_ISO_RE.finditer(text):
        try:
            candidates.append((m.start(), date(int(m[1]), int(m[2]), int(m[3]))))
        except ValueError:
            pass
    for m in _DATE_NUMERIC_RE.finditer(text):
        year = int(m[3]) if m[3] else default_year
        if year < 100:
            year += 2000
        try:
            candidates.append((m.start(), date(year, int(m[1]), int(m[2]))))
        except ValueError:
            pass
    for m in _DATE_WORD_RE.finditer(text):
        month = _MONTHS.get(m[1].lower())
        if not month:
            continue
        try:
            candidates.append((m.start(), date(int(m[3]) if m[3] else default_year, month, int(m[2]))))
        except ValueError:
            pass
    return min(candidates)[1] if candidates else None
