"""Writes into the local store: courses, lectures, resources with their chunks, assignments."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import get_settings
from .db import now_iso
from .util import clock, course_codes, sha256

KIND_LABELS = {
    "slides": "slides",
    "file": "file",
    "page": "page",
    "spec": "assignment",
    "notes": "my notes",
    "transcript": "recording",
    "submission": "graded submission",
    "announcement": "announcement",
}


@dataclass
class ChunkIn:
    text: str
    page: int | None = None
    seconds: int | None = None
    image_hash: str | None = None


# ---------------------------------------------------------------- courses

def find_course(conn: sqlite3.Connection, text: str | None) -> int | None:
    """The existing course whose code appears in `text`."""
    for _, key in course_codes(text):
        row = conn.execute("SELECT id FROM courses WHERE code_key = ?", (key,)).fetchone()
        if row:
            return row["id"]
    return None


def ensure_course(conn: sqlite3.Connection, text: str, **fields: Any) -> int | None:
    """Find or create the course whose code appears in `text`, then fill in any given fields.

    Returns None when `text` has no recognizable course code.
    """
    codes = course_codes(text)
    if not codes:
        return None
    course_id = find_course(conn, text)
    if course_id is None:
        display, key = codes[0]
        course_id = conn.execute(
            "INSERT INTO courses(code, code_key, created_at) VALUES (?, ?, ?)",
            (display, key, now_iso()),
        ).lastrowid
    updates = {k: v for k, v in fields.items() if v is not None}
    if updates:
        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE courses SET {sets} WHERE id = ?", (*updates.values(), course_id))
    return course_id


def course_code(conn: sqlite3.Connection, course_id: int) -> str:
    row = conn.execute("SELECT code FROM courses WHERE id = ?", (course_id,)).fetchone()
    return row["code"] if row else "?"


# ---------------------------------------------------------------- lectures

def ensure_lecture(
    conn: sqlite3.Connection,
    course_id: int,
    *,
    number: int | None = None,
    date: str | None = None,
    title: str | None = None,
) -> int:
    """Find the lecture by number or date (either may be unknown), creating or completing it."""
    row = None
    if number is not None:
        row = conn.execute(
            "SELECT * FROM lectures WHERE course_id = ? AND number = ?", (course_id, number)
        ).fetchone()
    if row is None and date is not None:
        row = conn.execute(
            "SELECT * FROM lectures WHERE course_id = ? AND date = ?", (course_id, date)
        ).fetchone()
    if row is None:
        return conn.execute(
            "INSERT INTO lectures(course_id, number, date, title) VALUES (?, ?, ?, ?)",
            (course_id, number, date, title),
        ).lastrowid
    updates: dict[str, Any] = {}
    if number is not None and row["number"] is None:
        clash = conn.execute(
            "SELECT 1 FROM lectures WHERE course_id = ? AND number = ?", (course_id, number)
        ).fetchone()
        if not clash:
            updates["number"] = number
    if date is not None and row["date"] is None:
        clash = conn.execute(
            "SELECT 1 FROM lectures WHERE course_id = ? AND date = ?", (course_id, date)
        ).fetchone()
        if not clash:
            updates["date"] = date
    if title and not row["title"]:
        updates["title"] = title
    if updates:
        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE lectures SET {sets} WHERE id = ?", (*updates.values(), row["id"]))
    return row["id"]


def lecture_label(conn: sqlite3.Connection, lecture_id: int | None) -> str | None:
    if lecture_id is None:
        return None
    row = conn.execute("SELECT number, date FROM lectures WHERE id = ?", (lecture_id,)).fetchone()
    if not row:
        return None
    if row["number"] is not None:
        return f"Lecture {row['number']}"
    return f"Lecture {row['date']}" if row["date"] else None


# ---------------------------------------------------------------- files

def save_file(data: bytes, suffix: str = ".pdf") -> str:
    """Store bytes by content hash under data/files; returns the path relative to the data dir."""
    digest = sha256(data)
    rel = Path("files") / digest[:2] / f"{digest}{suffix}"
    path = get_settings().data_dir / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return rel.as_posix()


def file_abspath(rel: str) -> Path:
    return get_settings().data_dir / rel


# ---------------------------------------------------------------- resources

def resource_row(conn: sqlite3.Connection, source: str, external_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM resources WHERE source = ? AND external_id = ?", (source, external_id)
    ).fetchone()


def resource_meta(conn: sqlite3.Connection, source: str, external_id: str) -> dict[str, Any] | None:
    row = resource_row(conn, source, external_id)
    return json.loads(row["meta_json"]) if row else None


def chunk_header(
    conn: sqlite3.Connection,
    course_id: int,
    lecture_id: int | None,
    kind: str,
    title: str,
    page: int | None,
    seconds: int | None,
) -> str:
    parts = [course_code(conn, course_id)]
    label = lecture_label(conn, lecture_id)
    if label:
        parts.append(label)
    parts.append(KIND_LABELS.get(kind, kind))
    parts.append(title)
    if page is not None:
        parts.append(f"p.{page}")
    if seconds is not None:
        parts.append(clock(seconds))
    return " · ".join(parts)


def upsert_resource(
    conn: sqlite3.Connection,
    *,
    course_id: int,
    source: str,
    kind: str,
    external_id: str,
    title: str,
    url: str | None = None,
    occurred_at: str | None = None,
    markdown: str | None = None,
    summary: str | None = None,
    file_path: str | None = None,
    page_count: int | None = None,
    duration_min: int | None = None,
    meta: dict[str, Any] | None = None,
    chunks: list[ChunkIn] | None = None,
) -> tuple[int, bool]:
    """Insert or update a resource. Chunks are replaced only when the content changed.

    Returns (resource_id, changed).
    """
    chunks = chunks or []
    content_hash = sha256(
        json.dumps(
            [title, kind, markdown, summary, file_path, [(c.page, c.seconds, c.text, c.image_hash) for c in chunks]],
            sort_keys=True,
        )
    )
    now = now_iso()
    meta_json = json.dumps(meta or {}, sort_keys=True)
    existing = resource_row(conn, source, external_id)

    if existing and existing["content_hash"] == content_hash and existing["course_id"] == course_id:
        conn.execute(
            "UPDATE resources SET url = ?, occurred_at = ?, meta_json = ?, synced_at = ? WHERE id = ?",
            (url, occurred_at, meta_json, now, existing["id"]),
        )
        return existing["id"], False

    values = dict(
        course_id=course_id, source=source, kind=kind, external_id=external_id, title=title, url=url,
        occurred_at=occurred_at, content_hash=content_hash, file_path=file_path, page_count=page_count,
        duration_min=duration_min, markdown=markdown, summary=summary, meta_json=meta_json, synced_at=now,
    )
    if existing:
        resource_id = existing["id"]
        sets = ", ".join(f"{k} = ?" for k in values)
        conn.execute(f"UPDATE resources SET {sets} WHERE id = ?", (*values.values(), resource_id))
    else:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        resource_id = conn.execute(
            f"INSERT INTO resources({cols}) VALUES ({marks})", tuple(values.values())
        ).lastrowid

    # Keep handwriting transcriptions for pages whose image did not change.
    kept = {
        r["image_hash"]: r["text"]
        for r in conn.execute(
            "SELECT image_hash, text FROM chunks WHERE resource_id = ? AND transcribed = 1 AND image_hash IS NOT NULL",
            (resource_id,),
        )
    }
    lecture_id = existing["lecture_id"] if existing else None
    conn.execute("DELETE FROM chunks WHERE resource_id = ?", (resource_id,))
    for seq, chunk in enumerate(chunks):
        text, transcribed = chunk.text, 0
        if chunk.image_hash and chunk.image_hash in kept:
            text, transcribed = kept[chunk.image_hash], 1
        conn.execute(
            "INSERT INTO chunks(resource_id, seq, page, seconds, lecture_id, header, text, image_hash, transcribed)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                resource_id, seq, chunk.page, chunk.seconds, lecture_id,
                chunk_header(conn, course_id, lecture_id, kind, title, chunk.page, chunk.seconds),
                text, chunk.image_hash, transcribed,
            ),
        )
    return resource_id, True


def refresh_headers(conn: sqlite3.Connection, resource_id: int) -> None:
    res = conn.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
    for c in conn.execute("SELECT id, page, seconds, lecture_id FROM chunks WHERE resource_id = ?", (resource_id,)).fetchall():
        header = chunk_header(
            conn, res["course_id"], c["lecture_id"] or res["lecture_id"], res["kind"], res["title"], c["page"], c["seconds"]
        )
        conn.execute("UPDATE chunks SET header = ? WHERE id = ? AND header != ?", (header, c["id"], header))


def delete_missing(conn: sqlite3.Connection, source: str, course_id: int, kinds: tuple[str, ...], seen: set[str]) -> int:
    """Remove resources of `kinds` for a course that the source no longer lists."""
    marks = ", ".join("?" for _ in kinds)
    rows = conn.execute(
        f"SELECT id, external_id FROM resources WHERE source = ? AND course_id = ? AND kind IN ({marks})",
        (source, course_id, *kinds),
    ).fetchall()
    gone = [r["id"] for r in rows if r["external_id"] not in seen]
    for rid in gone:
        conn.execute("DELETE FROM resources WHERE id = ?", (rid,))
    return len(gone)


# ---------------------------------------------------------------- assignments

def upsert_assignment(
    conn: sqlite3.Connection,
    *,
    course_id: int,
    source: str,
    external_id: str,
    title: str,
    due_at: str | None,
    points: float | None,
    score: float | None,
    status: str,
    url: str | None,
    spec_resource_id: int | None = None,
) -> tuple[int, bool]:
    now = now_iso()
    row = conn.execute(
        "SELECT * FROM assignments WHERE source = ? AND external_id = ?", (source, external_id)
    ).fetchone()
    values = dict(
        course_id=course_id, title=title, due_at=due_at, points=points, score=score, status=status, url=url,
        spec_resource_id=spec_resource_id,
    )
    if row:
        changed = any(row[k] != v for k, v in values.items())
        sets = ", ".join(f"{k} = ?" for k in values)
        conn.execute(
            f"UPDATE assignments SET {sets}, synced_at = ? WHERE id = ?", (*values.values(), now, row["id"])
        )
        return row["id"], changed
    cols = ", ".join(["source", "external_id", *values, "synced_at"])
    marks = ", ".join("?" for _ in range(len(values) + 3))
    new_id = conn.execute(
        f"INSERT INTO assignments({cols}) VALUES ({marks})", (source, external_id, *values.values(), now)
    ).lastrowid
    return new_id, True


@dataclass
class FeedbackIn:
    question: str
    score: float | None = None
    max_score: float | None = None
    rubric_items: list[str] | None = None
    comment: str | None = None


def replace_feedback(conn: sqlite3.Connection, assignment_id: int, items: list[FeedbackIn]) -> None:
    conn.execute("DELETE FROM feedback WHERE assignment_id = ?", (assignment_id,))
    for seq, item in enumerate(items, start=1):
        conn.execute(
            "INSERT INTO feedback(assignment_id, seq, question, score, max_score, rubric_items_json, comment)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (assignment_id, seq, item.question, item.score, item.max_score,
             json.dumps(item.rubric_items or []), item.comment),
        )
