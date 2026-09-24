# StudyHub

Hub for all my classes. StudyHub copies everything from Canvas, Gradescope, GoodNotes and
Granola into one local database, shows it as a timeline of lectures per course, and puts a
Claude agent next to it that can search and read all of it and cite exactly where each answer
came from (a slide page, a minute of a lecture recording, a page of handwritten notes).

- The plan and the reasoning behind the design, including why this uses retrieval to *find*
  material and whole-document reading to *understand* it: [`docs/plan.html`](docs/plan.html)
  (open it in a browser).
- The HTTP API the web app uses: [`docs/API.md`](docs/API.md).

Everything runs on your own computer. Course files, grades and credentials stay there; the
only thing sent anywhere is the material the agent reads to answer a question, which goes to
the Claude API.

## Quick start

Requirements: Python 3.11+, Node 20+.

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # or: uv venv && uv pip install -e ".[dev]"
.venv/bin/studyhub init        # creates backend/.env and the database
.venv/bin/studyhub demo        # optional: load an example CS 231N data set to look around

cd ../web && npm install && npm run build

cd ../backend && .venv/bin/studyhub serve
# open http://127.0.0.1:8000
```

The example data set is clearly marked in the app, and your first real sync replaces it.

For development, run `studyhub serve --reload` in `backend/` and `npm run dev` in `web/`
(Vite on http://localhost:5173, proxying `/api` to the backend).

## Connecting your accounts

Fill in `backend/.env` (see `backend/.env.example`), then run `studyhub check` to confirm
each login works and `studyhub sync` to pull everything. While `studyhub serve` runs it keeps
syncing on its own (Canvas and Granola every 30 minutes, GoodNotes every 10, Gradescope twice a
day), and the **Sync now** button pulls immediately.

| Source | What to set up | Notes |
|---|---|---|
| **Claude** | `ANTHROPIC_API_KEY` | Needed for chat and handwriting transcription. The agent uses `claude-opus-5`. |
| **Canvas** | `CANVAS_TOKEN` from Canvas → Account → Settings → **+ New access token**. Give it an expiry date. | A token can do anything your account can, so keep `.env` private. StudyHub only reads. |
| **Gradescope** | `GRADESCOPE_EMAIL` and `GRADESCOPE_PASSWORD` | No official API; this uses the unofficial `gradescopeapi` client. With Stanford SSO, set a Gradescope password first via **Forgot password**. Per-question feedback is read best-effort; totals always sync. |
| **GoodNotes** | Turn on **Settings → Automatic Backup**, choose Google Drive and **PDF**. Set `GOODNOTES_DIR` to the local copy of that folder (Google Drive for Desktop). | Keep one GoodNotes folder per class named after the course code, e.g. `CS 231N`. Write the date (`9/24`) at the top of each day's first page: that's how pages get matched to lectures. |
| **Granola** | `GRANOLA_API_KEY` from Granola → Settings → Connectors → API keys | The API needs a Granola Business plan. Record each lecture into a folder named after the course code. StudyHub keeps its own copy of transcripts, so Granola's auto-deletion can't take them away. |

Courses are matched across sources by course code (`CS 231N`, `cs231n` and `CS-231N` all
match). Canvas defines the course list when it's connected.

## Commands

Run from `backend/` (or put `backend/.venv/bin` on your `PATH`):

| Command | What it does |
|---|---|
| `studyhub check` | Log in to each configured source and report what it can see |
| `studyhub sync [canvas gradescope goodnotes granola]` | Pull new and changed material (all configured sources by default) |
| `studyhub serve [--port 8000] [--reload]` | Run the API and the web app |
| `studyhub ask "question" [--course "CS 231N"]` | Ask the agent from the terminal |
| `studyhub schedule "CS 231N" [--url … \| --csv … \| --show]` | Import the lecture schedule (from the synced syllabus, the course website, or a `number,date,title` CSV) so every lecture has a number, date and topic, recorded or not |
| `studyhub transcribe [--course …] [--limit 50] [--dry-run]` | Transcribe handwritten note pages (math as LaTeX) with Claude vision; only new or changed pages are sent |
| `studyhub demo [--force]` | Load the example data set |

## How it works

```
Canvas / Gradescope / GoodNotes PDFs / Granola
        │  connectors (backend/studyhub/connectors)
        ▼
  normalize: PDF text per page, transcript windows with timestamps, HTML → Markdown
        ▼
  data/studyhub.db (SQLite): courses, lectures, resources, chunks + FTS5 index,
                              assignments, feedback, chat threads
        ▼
  agent (backend/studyhub/agent): Claude with read-only tools
        search · read · get_lecture · list_assignments · get_feedback ·
        list_resources · list_announcements · sync_now
        ▼
  web app (web/): course timelines, a viewer, and the chat panel
```

- **Lectures are rebuilt after every sync.** An imported schedule comes first. Then a Granola
  recording on a date marks a lecture that day; dated pages in your notes attach to it; Canvas files named "Lecture 3" (or in a
  "Lecture 3" module) become lecture 3 and join the recorded lecture right after their upload.
- **Citations are locators** such as `r42@41:12` (resource 42 at 41:12) or `r17#p27` (page 27).
  Tools print them next to everything they return, the agent cites them as `[[r42@41:12]]`,
  and the server drops any the agent didn't actually get from a tool.
- **Deadlines and grades come from tables, not search.** A Gradescope assignment that matches a
  Canvas one replaces it (Gradescope has the grade; Canvas lends the description).
- **The agent can't change anything.** No tool writes, posts or submits, so instructions hidden
  in course content have nothing to trigger.

## Tests

```bash
cd backend && .venv/bin/pytest
```

The connectors are tested against fake Canvas, Granola and Gradescope responses, the chat loop
against a scripted model and against the real SDK talking to a local stand-in server.

## Not built yet

- Vector embeddings next to keyword search (the plan's Phase 2); search is BM25 over FTS5 today.
- Granola without a Business plan (its MCP server, which needs an OAuth flow).
- Graded-submission PDFs from Gradescope.
- An eval set of your real questions (Phase 4).
