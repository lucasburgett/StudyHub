"""Run connectors, record each run, and rebuild derived data (lectures, merged assignments)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import traceback
from datetime import datetime, timezone

from .config import SOURCES, Settings, get_settings
from .connectors import SyncContext, get_connector
from .db import get_meta, now_iso, session, set_meta
from .ingest.linking import rebuild_lectures
from .util import parse_dt, title_key

log = logging.getLogger("studyhub.sync")
_locks = {source: threading.Lock() for source in SOURCES}


def is_running(source: str) -> bool:
    return _locks[source].locked()


def merge_assignment_twins(conn: sqlite3.Connection, course_id: int) -> None:
    """Hide a Canvas assignment when Gradescope has the same one (Gradescope holds the grade).

    The Gradescope row inherits what only Canvas knows: the description, points and due date.
    """
    conn.execute("UPDATE assignments SET hidden = 0 WHERE course_id = ?", (course_id,))
    canvas = conn.execute(
        "SELECT * FROM assignments WHERE course_id = ? AND source = 'canvas'", (course_id,)
    ).fetchall()
    for g in conn.execute(
        "SELECT * FROM assignments WHERE course_id = ? AND source = 'gradescope'", (course_id,)
    ).fetchall():
        gkey = title_key(g["title"])
        for c in canvas:
            ckey = title_key(c["title"])
            same = ckey == gkey or (min(len(ckey), len(gkey)) >= 4 and (ckey.startswith(gkey) or gkey.startswith(ckey)))
            if not same:
                continue
            conn.execute("UPDATE assignments SET hidden = 1 WHERE id = ?", (c["id"],))
            conn.execute(
                "UPDATE assignments SET spec_resource_id = COALESCE(spec_resource_id, ?),"
                " points = COALESCE(points, ?), due_at = COALESCE(due_at, ?) WHERE id = ?",
                (c["spec_resource_id"], c["points"], c["due_at"], g["id"]),
            )
            break


def rebuild_course(conn: sqlite3.Connection, course_id: int) -> None:
    merge_assignment_twins(conn, course_id)
    rebuild_lectures(conn, course_id)
    conn.commit()


def clear_demo_data(conn: sqlite3.Connection) -> None:
    """The first real sync replaces the example data set."""
    if get_meta(conn, "demo") == "1":
        conn.execute("DELETE FROM courses")
        conn.execute("DELETE FROM threads")
        conn.execute("DELETE FROM sync_runs")
        set_meta(conn, "demo", "0")
        conn.commit()


def run_source(source: str, settings: Settings | None = None) -> dict:
    """Sync one source now. Returns the finished run as a dict (status "busy" if one is running)."""
    settings = settings or get_settings()
    lock = _locks[source]
    if not lock.acquire(blocking=False):
        return {"source": source, "status": "busy"}
    try:
        with session(settings.db_path) as conn:
            clear_demo_data(conn)
            run_id = conn.execute(
                "INSERT INTO sync_runs(source, started_at, status) VALUES (?, ?, 'running')", (source, now_iso())
            ).lastrowid
            conn.commit()
            ctx = SyncContext(conn=conn, settings=settings)
            status, error = "ok", None
            try:
                get_connector(source).sync(ctx)
                conn.commit()
            except Exception as e:  # recorded on the run and shown in the app
                conn.rollback()
                status, error = "error", str(e) or e.__class__.__name__
                log.error("%s sync failed: %s\n%s", source, error, traceback.format_exc())
            for course_id in sorted(ctx.touched_courses):
                rebuild_course(conn, course_id)
            conn.execute(
                "UPDATE sync_runs SET finished_at = ?, status = ?, items_changed = ?, warnings_json = ?, error = ?"
                " WHERE id = ?",
                (now_iso(), status, ctx.changed, json.dumps(ctx.warnings), error, run_id),
            )
            conn.commit()
            return {"source": source, "status": status, "items_changed": ctx.changed,
                    "warnings": ctx.warnings, "error": error}
    finally:
        lock.release()


def configured_sources(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    return [s for s in SOURCES if settings.configured(s)]


def run_all(sources: list[str] | None = None) -> list[dict]:
    """Canvas first: it defines the courses the other sources attach to."""
    return [run_source(s) for s in (sources or configured_sources())]


# How often each source is pulled while the server runs. Gradescope is scraped, so gently.
INTERVALS = {"canvas": 30 * 60, "gradescope": 12 * 3600, "goodnotes": 10 * 60, "granola": 30 * 60,
             "web": 6 * 3600}


def due_sources(conn: sqlite3.Connection, settings: Settings) -> list[str]:
    now = datetime.now(timezone.utc)
    due = []
    for source in configured_sources(settings):
        row = conn.execute("SELECT MAX(started_at) FROM sync_runs WHERE source = ?", (source,)).fetchone()
        last = parse_dt(row[0]) if row and row[0] else None
        if not is_running(source) and (last is None or (now - last).total_seconds() >= INTERVALS[source]):
            due.append(source)
    return due


def auto_sync_forever(stop: threading.Event, check_every: float = 60) -> None:
    """Background loop for the server: run each configured source when it's due."""
    while not stop.is_set():
        settings = get_settings()
        try:
            with session(settings.db_path) as conn:
                due = due_sources(conn, settings)
            for source in due:
                if stop.is_set():
                    break
                run_source(source, settings)
        except Exception:
            log.exception("auto sync failed")
        stop.wait(check_every)


def start_background(sources: list[str]) -> list[str]:
    started = [s for s in sources if not is_running(s)]
    if started:
        threading.Thread(target=run_all, args=(started,), daemon=True, name="studyhub-sync").start()
    return started
