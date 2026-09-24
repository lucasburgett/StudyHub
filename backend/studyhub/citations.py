"""Locators name a citable spot: r42 (resource), r42#p27 (page), r42@41:12 (time), a7, a7/q2.

Tools print locators next to everything they return; the model cites them as [[r42#p27]];
the app turns each into a chip that opens the viewer at that spot.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from .util import clock, parse_clock

_LOCATOR_RE = re.compile(r"^(?:r(\d+)(?:#p(\d+)|@(\d{1,2}:\d{2}(?::\d{2})?))?|a(\d+)(?:/q(\d+))?)$")
MARKER_RE = re.compile(r"\[\[([ra]\d+(?:#p\d+|@\d{1,2}:\d{2}(?::\d{2})?|/q\d+)?)\]\]")


@dataclass
class Locator:
    resource_id: int | None = None
    assignment_id: int | None = None
    page: int | None = None
    seconds: int | None = None
    question: int | None = None


def parse_locator(text: str) -> Locator | None:
    m = _LOCATOR_RE.match(text.strip().strip("[]"))
    if not m:
        return None
    if m[1]:
        return Locator(resource_id=int(m[1]), page=int(m[2]) if m[2] else None,
                       seconds=parse_clock(m[3]) if m[3] else None)
    return Locator(assignment_id=int(m[4]), question=int(m[5]) if m[5] else None)


def resource_locator(resource_id: int, page: int | None = None, seconds: int | None = None) -> str:
    if page is not None:
        return f"r{resource_id}#p{page}"
    if seconds is not None:
        return f"r{resource_id}@{clock(seconds)}"
    return f"r{resource_id}"


def _short(text: str, n: int = 26) -> str:
    text = re.sub(r"\.(pdf|pptx?)$", "", text, flags=re.IGNORECASE)
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def citation(conn: sqlite3.Connection, locator: str) -> dict | None:
    """Resolve a locator to the chip the app shows, or None if it names nothing."""
    loc = parse_locator(locator)
    if loc is None:
        return None
    if loc.assignment_id is not None:
        a = conn.execute("SELECT title FROM assignments WHERE id = ?", (loc.assignment_id,)).fetchone()
        if not a:
            return None
        label = _short(a["title"]) + (f" · Q{loc.question}" if loc.question else "")
        return _cite(locator, label, assignment_id=loc.assignment_id)

    r = conn.execute(
        "SELECT r.kind, r.title, r.occurred_at, r.lecture_id, l.number FROM resources r"
        " LEFT JOIN lectures l ON l.id = r.lecture_id WHERE r.id = ?",
        (loc.resource_id,),
    ).fetchone()
    if not r:
        return None
    number = r["number"]
    if loc.page is not None and number is None:
        c = conn.execute(
            "SELECT l.number FROM chunks c JOIN lectures l ON l.id = c.lecture_id WHERE c.resource_id = ? AND c.page = ?",
            (loc.resource_id, loc.page),
        ).fetchone()
        number = c["number"] if c else None
    lec = f"L{number}" if number is not None else None
    kind = r["kind"]
    if kind == "transcript":
        base = lec or _short(r["title"], 18)
    elif kind == "slides":
        base = f"{lec} slides" if lec else _short(r["title"])
    elif kind == "notes":
        base = f"{lec} notes" if lec else "My notes"
    elif kind == "announcement":
        base = f"Announcement · {(r['occurred_at'] or '')[5:10].replace('-', '/')}".rstrip(" ·/")
    else:
        base = _short(r["title"])
    if loc.page is not None:
        label = f"{base} · p.{loc.page}"
    elif loc.seconds is not None:
        label = f"{base} · {clock(loc.seconds)}"
    else:
        label = base
    return _cite(locator, label, resource_id=loc.resource_id, page=loc.page, seconds=loc.seconds)


def _cite(locator: str, label: str, *, resource_id: int | None = None, assignment_id: int | None = None,
          page: int | None = None, seconds: int | None = None) -> dict:
    return {"locator": locator, "label": label, "resource_id": resource_id,
            "assignment_id": assignment_id, "page": page, "seconds": seconds}


def markers(text: str) -> list[str]:
    return list(dict.fromkeys(MARKER_RE.findall(text)))
