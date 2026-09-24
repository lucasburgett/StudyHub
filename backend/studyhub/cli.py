"""Command line: `studyhub check | sync | serve | demo | ask | schedule | transcribe`."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from .config import BACKEND_DIR, SOURCES, get_settings
from .db import session


def cmd_check(args: argparse.Namespace) -> int:
    """Log in to every configured source and say what's visible (Phase 0 of the plan)."""
    from .connectors import get_connector

    settings = get_settings()
    ok = True
    for source in SOURCES:
        if not settings.configured(source):
            print(f"  {source:<11} not set up (see backend/.env.example)")
            continue
        try:
            print(f"✓ {source:<11} {get_connector(source).check(settings)}")
        except Exception as e:
            ok = False
            print(f"✗ {source:<11} {e}")
    agent = "ready" if settings.agent_ready else "needs ANTHROPIC_API_KEY"
    print(f"  {'claude':<11} {agent}")
    return 0 if ok else 1


def cmd_sync(args: argparse.Namespace) -> int:
    from .sync import configured_sources, run_source

    unknown = [s for s in args.sources if s not in SOURCES]
    if unknown:
        print(f"Unknown source: {', '.join(unknown)}. Choose from {', '.join(SOURCES)}.")
        return 2
    sources = args.sources or configured_sources()
    if not sources:
        print("No sources are set up. Copy backend/.env.example to backend/.env and fill it in.")
        return 1
    failed = False
    for source in sources:
        print(f"Syncing {source}…", flush=True)
        result = run_source(source)
        if result["status"] == "busy":
            print("  already running")
            continue
        print(f"  {result['status']}: {result['items_changed']} items changed")
        for w in result["warnings"]:
            print(f"  warning: {w}")
        if result["error"]:
            failed = True
            print(f"  error: {result['error']}")
    return 1 if failed else 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("studyhub.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import has_real_data, load_demo

    with session() as conn:
        if has_real_data(conn) and not args.force:
            print("The database already has synced data. Use --force to replace it with the example set.")
            return 1
        conn.execute("DELETE FROM courses")
        conn.execute("DELETE FROM threads")
        load_demo(conn)
    print("Loaded the example data set (CS 231N). Run `studyhub serve` and open http://127.0.0.1:8000")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from .agent.chat import run_chat
    from .store import find_course
    from .sync import run_source

    if not get_settings().agent_ready:
        print("Set ANTHROPIC_API_KEY in backend/.env first.")
        return 1
    with session() as conn:
        scope = {}
        if args.course:
            scope["course_id"] = find_course(conn, args.course)
        for event, data in run_chat(conn, " ".join(args.question), scope=scope, run_sync=run_source,
                                    effort=args.effort):
            if event == "text":
                print(data["delta"], end="", flush=True)
            elif event == "tool" and data["status"] != "running":
                print(f"\n  · {data['label']} ({data.get('summary', '')})", file=sys.stderr, flush=True)
            elif event == "error":
                print(f"\n[error] {data['message']}", file=sys.stderr)
        print()
    return 0


def cmd_transcribe(args: argparse.Namespace) -> int:
    from .ingest.handwriting import transcribe_notes

    if not get_settings().agent_ready:
        print("Set ANTHROPIC_API_KEY in backend/.env first.")
        return 1
    with session() as conn:
        done = transcribe_notes(conn, course=args.course, limit=args.limit, dry_run=args.dry_run)
    print(f"{'Would transcribe' if args.dry_run else 'Transcribed'} {done} pages.")
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    from .ingest import schedule
    from .store import find_course

    with session() as conn:
        course_id = find_course(conn, args.course)
        if course_id is None:
            print(f"No course matches {args.course!r}. Sync first, or check the code.")
            return 1
        if not args.show:
            if args.csv:
                rows = schedule.read_csv(args.csv)
            else:
                if not get_settings().agent_ready:
                    print("Reading a schedule needs ANTHROPIC_API_KEY in backend/.env (or use --csv).")
                    return 1
                material = schedule.fetch_page(args.url) if args.url else schedule.course_material(conn, course_id)
                if not material.strip():
                    print("No syllabus or pages synced for this course. Pass --url (the course website) or --csv.")
                    return 1
                rows = schedule.extract_schedule(conn, course_id, material)
            if not rows:
                print("No lecture schedule found.")
                return 1
            schedule.save_schedule(conn, course_id, rows)
        for r in conn.execute("SELECT * FROM schedule WHERE course_id = ? ORDER BY number", (course_id,)):
            print(f"  L{r['number']:<3} {r['date'] or 'no date':<11} {r['title'] or ''}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    env = BACKEND_DIR / ".env"
    if not env.exists():
        shutil.copy(BACKEND_DIR / ".env.example", env)
        print(f"Created {env}. Fill in the sources you use.")
    with session():
        pass
    print(f"Database ready at {get_settings().db_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="studyhub", description="Your classes in one place.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create backend/.env and the database").set_defaults(fn=cmd_init)
    sub.add_parser("check", help="test access to each configured source").set_defaults(fn=cmd_check)

    p = sub.add_parser("sync", help="copy new material from your sources")
    p.add_argument("sources", nargs="*", metavar="SOURCE",
                   help=f"any of: {', '.join(SOURCES)} (default: all configured)")
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser("serve", help="run the web app and API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("demo", help="load an example data set to try the app")
    p.add_argument("--force", action="store_true", help="replace synced data")
    p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("ask", help="ask the agent a question from the terminal")
    p.add_argument("question", nargs="+")
    p.add_argument("--course", help="course code to focus on, e.g. 'CS 231N'")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("schedule", help="import a course's lecture schedule (numbers, dates, topics)")
    p.add_argument("course", help="course code, e.g. 'CS 231N'")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--url", help="read the schedule from the course website instead of the synced syllabus")
    src.add_argument("--csv", type=Path, help="a CSV of number,date,title")
    src.add_argument("--show", action="store_true", help="print the imported schedule")
    p.set_defaults(fn=cmd_schedule)

    p = sub.add_parser("transcribe", help="transcribe handwritten note pages with Claude vision")
    p.add_argument("--course", help="only this course")
    p.add_argument("--limit", type=int, default=50, help="max pages this run (default 50)")
    p.add_argument("--dry-run", action="store_true", help="count pages without calling Claude")
    p.set_defaults(fn=cmd_transcribe)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
