# StudyHub

A local-first study hub: sync Canvas, Gradescope, GoodNotes, Granola and course websites into SQLite, browse
them by lecture, and chat with a Claude agent that cites exact pages and timestamps.

**Picking up the project? Read `docs/HANDOFF.md` first.** It covers current state, what hasn't been tested
on real data, and what to do next.

## Layout

- `backend/studyhub/`: Python 3.11, FastAPI, SQLite (FTS5).
  - `connectors/`: one per source; each implements `check(settings)` and `sync(ctx)`.
  - `ingest/`: PDF/HTML/transcript chunking, lecture linking, schedule import, handwriting transcription.
  - `agent/`: tools (read-only), prompt (instructions + course map), chat loop (streaming, citations).
  - `search.py`, `embeddings.py`: BM25 + optional vectors, merged by reciprocal rank fusion.
  - `api.py`: HTTP API per `docs/API.md`; also serves `web/dist`.
  - `cli.py`: the `studyhub` command.
- `web/`: Vite + React + TypeScript; talks only to `/api` (types in `web/src/api/types.ts`).
- `docs/API.md`: the contract between the two. Change both sides together.

## Commands

```bash
make setup     # venv + deps, backend/.env, database, web build
make test      # backend pytest, web lint + build (CI runs the same on push)
make dev       # backend --reload + Vite dev server on :5173
make serve     # everything on http://127.0.0.1:8000
```

## Conventions

- Tests never hit the network or use real credentials: fake services via `httpx.MockTransport`, scripted
  models for the chat loop. Fixtures from real data must be sanitized (no names, emails, grades, tokens).
- Secrets live only in `backend/.env` (gitignored). `data/` (database and downloaded files) is gitignored.
- Claude API code: default model `claude-opus-5`, adaptive thinking, streaming, server-side refusal
  fallbacks. Load the `claude-api` skill before changing any of it.
- Agent tools stay read-only; lectures stay derived data (see the invariants in `docs/HANDOFF.md`).
- Comments explain why, not what; keep the existing sparse style.
