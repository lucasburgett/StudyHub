"""Import a course's lecture schedule, so lectures exist (numbered, dated, titled) even
when nothing was recorded or posted for them.

Sources: Claude reading the syllabus and course pages already synced from Canvas, a course
website URL (many CS courses keep the schedule there), or a CSV of number,date,title.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import date
from pathlib import Path

import anthropic
import httpx

from ..config import get_settings
from ..sync import rebuild_course
from .text import html_to_markdown

MAX_SOURCE_CHARS = 200_000

SCHEMA = {
    "type": "object",
    "properties": {
        "lectures": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer", "description": "Lecture number, counting lectures only"},
                    "date": {"type": "string", "description": "YYYY-MM-DD, or empty if the source gives none"},
                    "title": {"type": "string", "description": "The lecture topic"},
                },
                "required": ["number", "date", "title"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lectures"],
    "additionalProperties": False,
}

PROMPT = """\
Below is material from the course {code}{title} ({term}{start}). Extract its lecture schedule.

- Include lectures only: skip sections, office hours, exams, holidays and deadlines.
- Number lectures in order starting at 1, using the course's own numbering when it has one.
- Dates as YYYY-MM-DD. Use the term's year. Leave the date empty if the source doesn't give one.
- If there is no lecture schedule in the material, return an empty list.

<material>
{material}
</material>"""


def course_material(conn: sqlite3.Connection, course_id: int) -> str:
    rows = conn.execute(
        "SELECT title, markdown FROM resources WHERE course_id = ? AND kind = 'page' AND markdown IS NOT NULL"
        " ORDER BY CASE WHEN title = 'Syllabus' THEN 0 ELSE 1 END, id",
        (course_id,),
    ).fetchall()
    return "\n\n".join(f"# {r['title']}\n{r['markdown']}" for r in rows)[:MAX_SOURCE_CHARS]


def fetch_page(url: str) -> str:
    resp = httpx.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    return html_to_markdown(resp.text)[:MAX_SOURCE_CHARS]


def extract_schedule(conn: sqlite3.Connection, course_id: int, material: str,
                     client: anthropic.Anthropic | None = None) -> list[dict]:
    from ..agent.chat import make_client

    course = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
    prompt = PROMPT.format(
        code=course["code"], title=f": {course['title']}" if course["title"] else "",
        term=course["term"] or "current term", start=f", starting {course['term_start']}" if course["term_start"] else "",
        material=material,
    )
    client = client or make_client()
    response = client.beta.messages.create(
        model=get_settings().model,
        max_tokens=16000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Couldn't extract a schedule (stop reason: {response.stop_reason}).")
    text = next(b.text for b in response.content if b.type == "text")
    return clean(json.loads(text)["lectures"])


def read_csv(path: Path) -> list[dict]:
    """number,date,title — a header row is optional."""
    rows = []
    with path.open(newline="") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip().isdigit():
                continue
            rows.append({"number": int(row[0]), "date": row[1].strip() if len(row) > 1 else "",
                         "title": row[2].strip() if len(row) > 2 else ""})
    return clean(rows)


def clean(rows: list[dict]) -> list[dict]:
    out: dict[int, dict] = {}
    for r in rows:
        day = (r.get("date") or "").strip()
        try:
            day = date.fromisoformat(day).isoformat() if day else None
        except ValueError:
            day = None
        out[int(r["number"])] = {"number": int(r["number"]), "date": day, "title": (r.get("title") or "").strip() or None}
    return [out[n] for n in sorted(out)]


def save_schedule(conn: sqlite3.Connection, course_id: int, rows: list[dict]) -> None:
    conn.execute("DELETE FROM schedule WHERE course_id = ?", (course_id,))
    seen_dates: set[str] = set()
    for r in rows:
        day = r["date"] if r["date"] not in seen_dates else None
        if day:
            seen_dates.add(day)
        conn.execute("INSERT INTO schedule(course_id, number, date, title) VALUES (?, ?, ?, ?)",
                     (course_id, r["number"], day, r["title"]))
    rebuild_course(conn, course_id)
