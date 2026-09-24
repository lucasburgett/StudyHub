"""HTTP API for the web app (see docs/API.md). Also serves the built app from web/dist."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import sync
from .agent.chat import run_chat
from .citations import citation, resource_locator
from .config import REPO_DIR, SOURCES, get_settings
from .db import connect, get_meta, init_db, loads, now_iso
from .search import search as run_search
from .search import snippet_html
from .store import file_abspath
from .util import clock, local_date, monday_of

log = logging.getLogger("studyhub.api")
app = FastAPI(title="StudyHub", docs_url="/api/docs", openapi_url="/api/openapi.json")


def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        init_db(conn)
        yield conn
    finally:
        conn.close()


def _one(conn: sqlite3.Connection, sql: str, *params: Any) -> sqlite3.Row:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise HTTPException(404, "Not found")
    return row


# ---------------------------------------------------------------- serializers

def resource_summary(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "course_id": r["course_id"], "source": r["source"], "kind": r["kind"], "title": r["title"],
        "occurred_at": r["occurred_at"], "lecture_id": r["lecture_id"], "url": r["url"],
        "has_file": bool(r["file_path"]), "page_count": r["page_count"], "duration_min": r["duration_min"],
    }


def assignment_summary(a: sqlite3.Row) -> dict:
    return {
        "id": a["id"], "course_id": a["course_id"], "source": a["source"], "title": a["title"], "due_at": a["due_at"],
        "points": a["points"], "score": a["score"], "status": a["status"], "url": a["url"],
        "spec_resource_id": a["spec_resource_id"],
    }


def course_summary(conn: sqlite3.Connection, c: sqlite3.Row) -> dict:
    counts = {
        "lectures": conn.execute("SELECT COUNT(*) FROM lectures WHERE course_id = ?", (c["id"],)).fetchone()[0],
        "resources": conn.execute("SELECT COUNT(*) FROM resources WHERE course_id = ?", (c["id"],)).fetchone()[0],
        "assignments": conn.execute(
            "SELECT COUNT(*) FROM assignments WHERE course_id = ? AND hidden = 0", (c["id"],)
        ).fetchone()[0],
    }
    nxt = conn.execute(
        "SELECT id, title, due_at FROM assignments WHERE course_id = ? AND hidden = 0 AND due_at >= ?"
        " AND status IN ('upcoming', 'unknown') ORDER BY due_at LIMIT 1",
        (c["id"], now_iso()),
    ).fetchone()
    return {
        "id": c["id"], "code": c["code"], "title": c["title"], "term": c["term"], "counts": counts,
        "next_due": dict(nxt) if nxt else None,
    }


# ---------------------------------------------------------------- status and sync

@app.get("/api/status")
def status(conn: sqlite3.Connection = Depends(db)) -> dict:
    settings = get_settings()
    sources = []
    for source in SOURCES:
        run = conn.execute(
            "SELECT * FROM sync_runs WHERE source = ? AND status != 'running' ORDER BY id DESC LIMIT 1", (source,)
        ).fetchone()
        sources.append({
            "source": source,
            "configured": settings.configured(source),
            "running": sync.is_running(source),
            "last_run": {
                "status": run["status"], "started_at": run["started_at"], "finished_at": run["finished_at"],
                "items_changed": run["items_changed"], "warnings": loads(run["warnings_json"], []),
                "error": run["error"],
            } if run else None,
        })
    return {"demo": get_meta(conn, "demo") == "1", "agent_ready": settings.agent_ready, "sources": sources}


class SyncRequest(BaseModel):
    source: str | None = None


@app.post("/api/sync")
def start_sync(body: SyncRequest | None = None) -> dict:
    wanted = [body.source] if body and body.source else sync.configured_sources()
    for source in wanted:
        if source not in SOURCES:
            raise HTTPException(400, f"Unknown source {source}")
        if not get_settings().configured(source):
            raise HTTPException(400, f"{source} isn't set up in backend/.env")
    return {"started": sync.start_background(wanted)}


# ---------------------------------------------------------------- courses

@app.get("/api/courses")
def courses(conn: sqlite3.Connection = Depends(db)) -> list[dict]:
    return [course_summary(conn, c) for c in conn.execute("SELECT * FROM courses ORDER BY code")]


@app.get("/api/courses/{course_id}")
def course(course_id: int, conn: sqlite3.Connection = Depends(db)) -> dict:
    c = _one(conn, "SELECT * FROM courses WHERE id = ?", course_id)
    return {**course_summary(conn, c), "term_start": c["term_start"], "site_url": c["site_url"],
            "canvas_url": c["canvas_url"]}


@app.get("/api/courses/{course_id}/timeline")
def timeline(course_id: int, conn: sqlite3.Connection = Depends(db)) -> dict:
    settings = get_settings()
    tz = settings.tz
    c = _one(conn, "SELECT * FROM courses WHERE id = ?", course_id)
    now_local = datetime.now(tz).date()
    today = now_local.isoformat()
    stale_before = (now_local - timedelta(days=3)).isoformat()
    kinds_present = {r["kind"] for r in conn.execute(
        "SELECT DISTINCT kind FROM resources WHERE course_id = ?", (course_id,))}
    expect_recordings = settings.configured("granola") or "transcript" in kinds_present

    buckets: dict[str | None, dict] = {}

    def bucket(day: str | None) -> dict:
        key = monday_of(date.fromisoformat(day)).isoformat() if day else None
        if key not in buckets:
            buckets[key] = {"start": key, "lectures": [], "assignments": [], "other": []}
        return buckets[key]

    linked_notes: set[int] = set()
    for lec in conn.execute(
        "SELECT * FROM lectures WHERE course_id = ? ORDER BY COALESCE(date, '9999'), number", (course_id,)
    ).fetchall():
        items = []
        for r in conn.execute("SELECT * FROM resources WHERE lecture_id = ? ORDER BY kind, id", (lec["id"],)):
            items.append({**resource_summary(r), "pages": None})
        for r in conn.execute(
            "SELECT r.*, MIN(ch.page) AS first_page, MAX(ch.page) AS last_page FROM chunks ch"
            " JOIN resources r ON r.id = ch.resource_id WHERE ch.lecture_id = ? AND (r.lecture_id IS NULL OR r.lecture_id != ?)"
            " GROUP BY r.id",
            (lec["id"], lec["id"]),
        ):
            linked_notes.add(r["id"])
            items.append({**resource_summary(r), "pages": [r["first_page"], r["last_page"]]})
        kinds = {i["kind"] for i in items}
        day = lec["date"] or min((local_date(i["occurred_at"], tz) for i in items if i["occurred_at"]), default=None)
        # An undated lecture's day comes from its slides, which are often posted days early.
        settled = lec["date"] <= today if lec["date"] else (day is None or day <= stale_before)
        missing = []
        if settled:
            if expect_recordings and "transcript" not in kinds:
                missing.append("recording")
            if "notes" in kinds_present and "notes" not in kinds:
                missing.append("notes")
            if "slides" in kinds_present and "slides" not in kinds:
                missing.append("slides")
        bucket(day)["lectures"].append({
            "id": lec["id"], "number": lec["number"], "title": lec["title"], "date": lec["date"],
            "resources": items, "missing": missing, "_sort": (day or "9999", lec["number"] or 0),
        })

    for a in conn.execute(
        "SELECT * FROM assignments WHERE course_id = ? AND hidden = 0 ORDER BY due_at", (course_id,)
    ):
        bucket(local_date(a["due_at"], tz))["assignments"].append(assignment_summary(a))

    for r in conn.execute(
        "SELECT * FROM resources WHERE course_id = ? AND lecture_id IS NULL"
        " AND kind IN ('slides', 'file', 'notes', 'transcript') ORDER BY occurred_at",
        (course_id,),
    ):
        if r["id"] in linked_notes:
            continue
        bucket(local_date(r["occurred_at"], tz))["other"].append(resource_summary(r))

    starts = [k for k in buckets if k]
    first = c["term_start"] or (min(starts) if starts else None)
    term_monday = monday_of(date.fromisoformat(first)) if first else None
    weeks = []
    for key in sorted(buckets, key=lambda k: k or "9999"):
        b = buckets[key]
        b["lectures"].sort(key=lambda lec: lec["_sort"])
        if key and term_monday:
            n = (date.fromisoformat(key) - term_monday).days // 7 + 1
            b.update(week=n, label=f"Week {n}" if n >= 1 else f"Before week 1 ({date.fromisoformat(key):%b %-d})")
        else:
            b.update(week=None, label="Undated")
        weeks.append(b)
    for b in weeks:
        for lec in b["lectures"]:
            del lec["_sort"]
    return {"weeks": weeks}


@app.get("/api/courses/{course_id}/resources")
def course_resources(course_id: int, kind: str | None = None, source: str | None = None,
                     conn: sqlite3.Connection = Depends(db)) -> list[dict]:
    where, params = ["course_id = ?"], [course_id]
    for column, value in (("kind", kind), ("source", source)):
        if value:
            values = [v for v in value.split(",") if v]
            where.append(f"{column} IN ({', '.join('?' for _ in values)})")
            params += values
    rows = conn.execute(
        f"SELECT * FROM resources WHERE {' AND '.join(where)} ORDER BY occurred_at IS NULL, occurred_at DESC, id DESC",
        params,
    )
    return [resource_summary(r) for r in rows]


@app.get("/api/courses/{course_id}/assignments")
def course_assignments(course_id: int, conn: sqlite3.Connection = Depends(db)) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM assignments WHERE course_id = ? AND hidden = 0 ORDER BY due_at IS NULL, due_at", (course_id,)
    )
    return [assignment_summary(a) for a in rows]


# ---------------------------------------------------------------- resources and assignments

@app.get("/api/resources/{resource_id}")
def resource(resource_id: int, conn: sqlite3.Connection = Depends(db)) -> dict:
    r = _one(conn, "SELECT r.*, c.code AS course_code FROM resources r JOIN courses c ON c.id = r.course_id"
                   " WHERE r.id = ?", resource_id)
    chunks = conn.execute("SELECT page, seconds, text FROM chunks WHERE resource_id = ? ORDER BY seq",
                          (resource_id,)).fetchall()
    pages = [{"page": ch["page"], "text": ch["text"]} for ch in chunks if ch["page"] is not None]
    segments = [{"seconds": ch["seconds"], "label": clock(ch["seconds"]), "text": ch["text"]}
                for ch in chunks if ch["seconds"] is not None]
    return {
        **resource_summary(r), "course_code": r["course_code"], "markdown": r["markdown"], "summary": r["summary"],
        "pages": pages or None, "segments": segments or None,
        "file_url": f"/api/resources/{resource_id}/file" if r["file_path"] else None,
    }


@app.get("/api/resources/{resource_id}/file")
def resource_file(resource_id: int, conn: sqlite3.Connection = Depends(db)) -> FileResponse:
    r = _one(conn, "SELECT title, file_path FROM resources WHERE id = ?", resource_id)
    if not r["file_path"]:
        raise HTTPException(404, "This item has no file")
    path = file_abspath(r["file_path"])
    if not path.exists():
        raise HTTPException(404, "File missing from the data folder; run a sync")
    name = r["title"] if r["title"].lower().endswith(".pdf") else f"{r['title']}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=name, content_disposition_type="inline")


@app.get("/api/assignments/{assignment_id}")
def assignment(assignment_id: int, conn: sqlite3.Connection = Depends(db)) -> dict:
    a = _one(conn, "SELECT a.*, c.code AS course_code FROM assignments a JOIN courses c ON c.id = a.course_id"
                   " WHERE a.id = ?", assignment_id)
    spec = conn.execute("SELECT markdown FROM resources WHERE id = ?", (a["spec_resource_id"],)).fetchone() \
        if a["spec_resource_id"] else None
    feedback = [
        {"question": f["question"], "score": f["score"], "max_score": f["max_score"],
         "rubric_items": json.loads(f["rubric_items_json"]), "comment": f["comment"]}
        for f in conn.execute("SELECT * FROM feedback WHERE assignment_id = ? ORDER BY seq", (assignment_id,))
    ]
    return {**assignment_summary(a), "course_code": a["course_code"],
            "description": spec["markdown"] if spec else None, "feedback": feedback}


# ---------------------------------------------------------------- search

@app.get("/api/search")
def search(q: str = Query(..., min_length=1), course_id: int | None = None, source: str | None = None,
           limit: int = Query(20, ge=1, le=50), conn: sqlite3.Connection = Depends(db)) -> list[dict]:
    hits = run_search(conn, q, course_id=course_id, sources=source.split(",") if source else None,
                      limit=limit, snippet_tokens=20, prefix_last=True)
    out = []
    for h in hits:
        loc = resource_locator(h.resource_id, h.page, h.seconds)
        cite = citation(conn, loc)
        out.append({
            "chunk_id": h.chunk_id, "resource_id": h.resource_id, "course_code": h.course_code, "source": h.source,
            "kind": h.kind, "title": h.title, "locator": loc, "label": cite["label"] if cite else loc,
            "snippet": snippet_html(h.snippet), "lecture_id": h.lecture_id, "page": h.page, "seconds": h.seconds,
        })
    return out


# ---------------------------------------------------------------- chat

class ChatScope(BaseModel):
    course_id: int | None = None
    resource_id: int | None = None


class ChatRequest(BaseModel):
    message: str
    thread_id: int | None = None
    scope: ChatScope | None = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/chat")
def chat(body: ChatRequest) -> StreamingResponse:
    if not get_settings().agent_ready:
        raise HTTPException(400, "Chat needs a Claude API key. Add ANTHROPIC_API_KEY to backend/.env and restart.")
    if not body.message.strip():
        raise HTTPException(400, "Empty message")

    def stream() -> Iterator[str]:
        conn = connect()
        try:
            init_db(conn)
            events = run_chat(conn, body.message.strip(), thread_id=body.thread_id,
                              scope=body.scope.model_dump() if body.scope else {}, run_sync=sync.run_source)
            for event, data in events:
                yield _sse(event, data)
        except Exception as e:  # surface anything unexpected to the chat panel
            log.exception("chat failed")
            yield _sse("error", {"message": f"Something went wrong: {e}"})
        finally:
            conn.close()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/threads")
def threads(course_id: int | None = None, conn: sqlite3.Connection = Depends(db)) -> list[dict]:
    rows = conn.execute("SELECT * FROM threads ORDER BY updated_at DESC LIMIT 100").fetchall()
    out = []
    for t in rows:
        scope = loads(t["scope_json"], {})
        if course_id is not None and scope.get("course_id") != course_id:
            continue
        out.append({"id": t["id"], "title": t["title"], "scope": scope, "updated_at": t["updated_at"]})
    return out


@app.get("/api/threads/{thread_id}")
def thread(thread_id: int, conn: sqlite3.Connection = Depends(db)) -> dict:
    t = _one(conn, "SELECT * FROM threads WHERE id = ?", thread_id)
    messages = [
        {"id": m["id"], "role": m["role"], "text": m["text"], "tools": loads(m["tools_json"], []),
         "citations": loads(m["citations_json"], {}), "created_at": m["created_at"]}
        for m in conn.execute("SELECT * FROM messages WHERE thread_id = ? ORDER BY id", (thread_id,))
    ]
    return {"id": t["id"], "title": t["title"], "scope": loads(t["scope_json"], {}), "messages": messages}


# ---------------------------------------------------------------- the web app

_dist = REPO_DIR / "web" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
