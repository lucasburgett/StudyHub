"""Transcribe handwritten GoodNotes pages with Claude vision.

GoodNotes' own text layer catches most prose but mangles math and ignores diagrams. This
replaces a page's text with a Markdown + LaTeX transcription. Each page's image hash is
stored, so a re-exported notebook only sends new or edited pages.
"""

from __future__ import annotations

import base64
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import anthropic

from ..config import get_settings
from ..store import file_abspath, find_course
from ..sync import index_new_chunks, rebuild_course
from .text import render_page_png

log = logging.getLogger("studyhub.handwriting")

RULES = """\
- Keep the student's structure: headings, bullets, arrows (→), underlines as **bold**.
- Write math in LaTeX with $…$ inline and $$…$$ for display.
- Put a date written on the page first, exactly as written.
- Describe each diagram or sketch in one line in square brackets, e.g. [diagram: computational graph for f = (x + y)·z].
- Mark words you can't read as [?]."""

PROMPT = f"""\
This is one page of a student's handwritten lecture notes. Transcribe it into Markdown.
{RULES}
Reply with the transcription only."""

# On the Claude subscription every call starts a Claude Code process, so pages go in small batches.
BATCH = 4
BATCH_PROMPT = """\
These are {n} pages of a student's handwritten lecture notes, in order. Transcribe each into Markdown.
{rules}
Return one entry per page: "page" is its number in the order given (1 to {n}), "markdown" its transcription."""
PAGES_SCHEMA = {
    "type": "object",
    "properties": {"pages": {"type": "array", "items": {
        "type": "object",
        "properties": {"page": {"type": "integer"}, "markdown": {"type": "string"}},
        "required": ["page", "markdown"],
        "additionalProperties": False,
    }}},
    "required": ["pages"],
    "additionalProperties": False,
}


def _transcribe(client: anthropic.Anthropic, model: str, png: bytes) -> str | None:
    response = client.beta.messages.create(
        model=model,
        max_tokens=4000,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.standard_b64encode(png).decode()}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        return None
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text or None


def _transcribe_batch(pngs: list[bytes]) -> list[str | None]:
    """Pages on the Claude subscription; a page Claude skipped comes back None."""
    from ..agent import subscription

    answer = subscription.ask_json(BATCH_PROMPT.format(n=len(pngs), rules=RULES), PAGES_SCHEMA, images=pngs,
                                   effort="medium")
    by_page = {p["page"]: p["markdown"].strip() for p in answer.get("pages") or []
               if isinstance(p, dict) and isinstance(p.get("page"), int) and isinstance(p.get("markdown"), str)}
    return [by_page.get(i + 1) or None for i in range(len(pngs))]


def transcribe_notes(conn: sqlite3.Connection, *, course: str | None = None, limit: int = 50,
                     dry_run: bool = False, client: anthropic.Anthropic | None = None) -> int:
    where, params = ["r.kind = 'notes'", "c.transcribed = 0", "c.page IS NOT NULL", "r.file_path IS NOT NULL"], []
    if course:
        course_id = find_course(conn, course)
        if course_id is None:
            raise SystemExit(f"No course matches {course!r}")
        where.append("r.course_id = ?")
        params.append(course_id)
    rows = conn.execute(
        f"SELECT c.id, c.page, r.id AS resource_id, r.course_id, r.file_path FROM chunks c"
        f" JOIN resources r ON r.id = c.resource_id WHERE {' AND '.join(where)} ORDER BY r.id, c.page LIMIT ?",
        (*params, limit),
    ).fetchall()
    if dry_run or not rows:
        return len(rows)

    from ..agent.chat import make_client

    on_subscription = client is None and get_settings().agent_backend == "subscription"
    client = None if on_subscription else client or make_client()
    model = get_settings().model
    # Render in this thread (PyMuPDF isn't thread-safe); only the API calls run in parallel.
    pdfs: dict[str, bytes] = {}
    jobs = []
    for row in rows:
        if row["file_path"] not in pdfs:
            pdfs[row["file_path"]] = file_abspath(row["file_path"]).read_bytes()
        jobs.append((row, render_page_png(pdfs[row["file_path"]], row["page"])))

    def work(job: tuple[sqlite3.Row, bytes]) -> tuple[int, str | None]:
        row, png = job
        try:
            return row["id"], _transcribe(client, model, png)
        except anthropic.APIError as e:
            log.warning("page %s of r%s failed: %s", row["page"], row["resource_id"], e)
            return row["id"], None

    errors: list[str] = []

    def work_batch(batch: list[tuple[sqlite3.Row, bytes]]) -> list[tuple[int, str | None]]:
        try:
            texts = _transcribe_batch([png for _, png in batch])
        except RuntimeError as e:  # not logged in, usage limit, …: say so rather than fail quietly
            log.warning("pages %s of r%s failed: %s", [r["page"] for r, _ in batch], batch[0][0]["resource_id"], e)
            errors.append(str(e))
            texts = [None] * len(batch)
        return [(row["id"], text) for (row, _), text in zip(batch, texts)]

    done = 0
    courses: set[int] = set()
    with ThreadPoolExecutor(max_workers=4) as pool:
        if on_subscription:
            batches = [jobs[i:i + BATCH] for i in range(0, len(jobs), BATCH)]
            results = [pair for pairs in pool.map(work_batch, batches) for pair in pairs]
        else:
            results = list(pool.map(work, jobs))
        for chunk_id, text in results:
            if text:
                conn.execute("UPDATE chunks SET text = ?, transcribed = 1, embed_model = NULL WHERE id = ?",
                             (text, chunk_id))
                done += 1
        conn.commit()
    for row in rows:
        courses.add(row["course_id"])
    for resource_id in {r["resource_id"] for r in rows}:
        pages = conn.execute("SELECT page, text FROM chunks WHERE resource_id = ? ORDER BY page", (resource_id,))
        markdown = "\n\n".join(f"--- page {p['page']} ---\n{p['text']}" for p in pages)
        conn.execute("UPDATE resources SET markdown = ? WHERE id = ?", (markdown, resource_id))
    for course_id in courses:
        rebuild_course(conn, course_id)  # transcriptions can reveal dates for lecture linking
    index_new_chunks(conn, get_settings())
    if errors and not done:
        raise RuntimeError(errors[0])
    return done
