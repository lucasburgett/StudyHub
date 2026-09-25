"""Command line: `studyhub check | sync | serve | demo | ask | schedule | index | eval | transcribe`."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from .config import BACKEND_DIR, SOURCES, get_settings
from .db import session

NO_CLAUDE = ("This needs Claude: log in to Claude Code (`claude auth login`) to use your Claude subscription, "
             "or set ANTHROPIC_API_KEY in backend/.env.")


def cmd_check(args: argparse.Namespace) -> int:
    """Log in to every configured source and say what's visible (Phase 0 of the plan)."""
    from .checks import check

    settings = get_settings()
    failed = False
    for target in (*SOURCES, "claude", "search"):
        if target in SOURCES and not settings.configured(target):
            print(f"  {target:<11} not set up (see backend/.env.example)")
            continue
        ok, message = check(target, settings)
        failed |= not ok and target in SOURCES
        print(f"{'✓' if ok else '✗'} {target:<11} {message}")
    return 1 if failed else 0


def cmd_index(args: argparse.Namespace) -> int:
    from .embeddings import get_embedder, index_embeddings

    embedder = get_embedder()
    if embedder is None:
        print("Semantic search is off. Set VOYAGE_API_KEY, or `pip install -e \".[local-embeddings]\"`.")
        return 1
    with session() as conn:
        if args.rebuild:
            conn.execute("UPDATE chunks SET embed_model = NULL")
        n = index_embeddings(conn, embedder)
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"Embedded {n} chunks with {embedder.name}; {total} chunks in the index.")
    return 0


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
        from .sync import index_new_chunks

        index_new_chunks(conn, get_settings())
    print("Loaded the example data set (CS 231N). Run `studyhub serve` and open http://127.0.0.1:8000")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from .agent.chat import run_chat
    from .store import find_course
    from .sync import run_source

    if not get_settings().agent_ready:
        print(NO_CLAUDE)
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

    if not get_settings().api_key_set:
        print("Transcription needs ANTHROPIC_API_KEY in backend/.env; it doesn't run on a Claude subscription yet.")
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
                    print(f"{NO_CLAUDE} Or pass --csv.")
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


def cmd_eval(args: argparse.Namespace) -> int:
    from datetime import datetime

    from .evals import CaseError, load_cases, run_eval

    settings = get_settings()
    backend = settings.agent_backend
    if backend is None:
        print(NO_CLAUDE)
        return 1
    with session() as conn:
        try:
            cases = load_cases(conn, args.cases)
        except CaseError as e:
            print(f"Problem in {args.cases}: {e}")
            return 2
    if args.case:
        cases = [c for c in cases if c.id in args.case]
    n = len(cases) * args.reps
    print(f"{n} questions to ask ({len(cases)} cases × {args.reps} reps) with {settings.model}, effort {args.effort}.")
    if backend == "api":
        print("Each takes a few Claude requests; expect very roughly $0.05–0.40 per question at list prices.")
    else:
        print("They run on your Claude subscription and count against its usage limits.")
    if not args.yes and input("Run them? [y/N] ").strip().lower() not in ("y", "yes"):
        return 1
    out = args.out or settings.data_dir / "evals" / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")

    def on_row(row: dict | None, err: dict | None) -> None:
        if err:
            print(f"  ! {err['case_id']} #{err['rep']}: {err['kind']}: {err['message']}")
            return
        failed = [k for k, ok in row["grades"].items() if not ok]
        mark = "✓" if row["pass"] else "✗"
        detail = f" [{row['status']}]" if row["status"] != "ok" else (f" (failed: {', '.join(failed)})" if failed else "")
        print(f"  {mark} {row['case_id']} #{row['rep']}{detail}  {row['seconds']} s")

    summary = run_eval(args.cases, out, reps=args.reps, effort=args.effort,
                       only=[c.id for c in cases], timeout_s=args.timeout, on_row=on_row)
    low, high = summary["pass_rate_95ci"]
    rate = summary["pass_rate"]
    print(f"\nPassed {summary['passed']} of {summary['scored']} scored"
          + (f" ({rate:.0%}, 95% CI {low:.0%}–{high:.0%})" if rate is not None else "")
          + (f"; {summary['errors']} errors not scored" if summary["errors"] else ""))
    if summary["failing_checks"]:
        print("Failing checks: " + ", ".join(f"{k} ×{v}" for k, v in summary["failing_checks"].items()))
    print(f"Results, transcripts and summary: {out}")
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
    # httpx logs every URL, and Canvas file downloads carry signed tokens in theirs.
    for name in ("claude_agent_sdk", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)
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

    p = sub.add_parser("index", help="build the semantic search index (runs after every sync anyway)")
    p.add_argument("--rebuild", action="store_true", help="re-embed everything, e.g. after changing models")
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("eval", help="ask a set of questions and check the answers' citations and content")
    p.add_argument("cases", type=Path, help="case file, e.g. evals/demo.json or data/evals/mine.json")
    p.add_argument("--reps", type=int, default=1, help="times to ask each question (default 1)")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--case", action="append", help="only this case id (repeatable)")
    p.add_argument("--timeout", type=float, default=300, help="seconds allowed per question (default 300)")
    p.add_argument("--out", type=Path, help="output folder (default data/evals/runs/<time>)")
    p.add_argument("--yes", action="store_true", help="don't ask before spending API credits")
    p.set_defaults(fn=cmd_eval)

    p = sub.add_parser("transcribe", help="transcribe handwritten note pages with Claude vision")
    p.add_argument("--course", help="only this course")
    p.add_argument("--limit", type=int, default=50, help="max pages this run (default 50)")
    p.add_argument("--dry-run", action="store_true", help="count pages without calling Claude")
    p.set_defaults(fn=cmd_transcribe)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
