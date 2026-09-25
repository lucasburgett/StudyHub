# StudyHub

Hub for all my classes. StudyHub copies everything from Canvas, Gradescope, GoodNotes and
Granola into one local database, shows it as a timeline of lectures per course, and puts a
Claude agent next to it that can search and read all of it and cite exactly where each answer
came from (a slide page, a minute of a lecture recording, a page of handwritten notes).

- The plan and the reasoning behind the design, including why this uses retrieval to *find*
  material and whole-document reading to *understand* it: [`docs/plan.html`](docs/plan.html)
  (open it in a browser).
- The HTTP API the web app uses: [`docs/API.md`](docs/API.md).

Everything runs on your own computer, and course files, grades and credentials are stored
there. Two things leave it: what the agent reads to answer a question (and note pages you ask it
to transcribe) goes to Claude, either through the Claude API or through Claude Code on your
Claude subscription; and if you turn on Voyage for semantic search, course text goes to Voyage to
be embedded.

## Quick start

Requirements: Python 3.11+, Node 22, `make` (on macOS: `xcode-select --install`).

```bash
make setup     # installs everything, creates backend/.env and the database, builds the web app
make demo      # optional: load an example CS 231N data set to look around
make serve     # open http://127.0.0.1:8000
```

The example data set is clearly marked in the app, and your first real sync replaces it.
`make help` lists the other tasks (`make dev` runs the backend with reload plus the Vite dev
server on http://localhost:5173; `make test` runs the tests, lint and build).

<details><summary>Without make</summary>

```bash
python3 -m venv backend/.venv && backend/.venv/bin/pip install -e "./backend[dev]"
backend/.venv/bin/studyhub init
(cd web && npm install && npm run build)
backend/.venv/bin/studyhub serve
```
</details>

## Connecting your accounts

Open **Settings** in the app (the sliders icon in the top bar) and fill in the sources you use;
**Test connection** confirms each login. The page writes `backend/.env`, which you can also edit by
hand (see `backend/.env.example`); `studyhub check` runs the same tests from the terminal and
`studyhub sync` pulls everything. While `studyhub serve` runs it keeps
syncing on its own (Canvas and Granola every 30 minutes, GoodNotes every 10, Gradescope twice a
day, course websites every 6 hours), and the **Sync now** button pulls immediately.

| Source | What to set up | Notes |
|---|---|---|
| **Claude** | Either log in to Claude Code with your Claude plan (`claude auth login` in a terminal), or set `ANTHROPIC_API_KEY` | Chat runs on whichever you have; with both, the key wins unless `STUDYHUB_AGENT=subscription` (Settings → Claude → **Chat runs on**). On a subscription, questions count against the plan's usage limits, like Claude Code does. Handwriting transcription and schedule import still need the API key. The agent uses `claude-opus-5`. |
| **Canvas** | `CANVAS_TOKEN` from Canvas → Account → Settings → **+ New access token**. Give it an expiry date. | A token can do anything your account can, so keep `.env` private. StudyHub only reads. |
| **Gradescope** | `GRADESCOPE_EMAIL` and `GRADESCOPE_PASSWORD` | No official API; this uses the unofficial `gradescopeapi` client. With Stanford SSO, set a Gradescope password first via **Forgot password**. Per-question feedback is read best-effort; totals always sync. |
| **GoodNotes** | Turn on **Settings → Automatic Backup**, choose Google Drive and **PDF**. Set `GOODNOTES_DIR` to the local copy of that folder (Google Drive for Desktop). | Keep one GoodNotes folder per class named after the course code, e.g. `CS 231N`. Write the date (`9/24`) at the top of each day's first page: that's how pages get matched to lectures. |
| **Semantic search** (optional) | `VOYAGE_API_KEY` from dash.voyageai.com (200M free tokens), or `pip install -e ".[local-embeddings]"` for a model that runs on your machine | Finds material phrased differently from your question (e.g. "chain rule trick" → the backpropagation lecture). With Voyage, course text is sent to Voyage to be embedded; the local model keeps it on your computer. Without either, search is keyword-only. |
| **Class schedules** (optional) | `CLASS_SCHEDULES=FRENLANG 1=Mon-Fri 9:30 from 2026-09-21` (Settings → Class schedules) | For classes that post homework on a Canvas page per class ("Week 1, Day 3", under "Devoirs" or "Before class"). Each such page becomes an assignment due at the start of that class, with a Done box to tick it off. Without a line, the homework shows up undated. |
| **Course websites** | `COURSE_SITES=CS 231N=https://cs231n.stanford.edu/schedule.html` (semicolon-separated) | For courses that post slides on their own site. StudyHub reads the page, downloads the slide PDFs it links (and public Google Slides), and uses each schedule row's "Lecture N" and date to place them. |
| **Granola** | `GRANOLA_API_KEY` from Granola → Settings → Connectors → API keys | The API needs a Granola Business plan. Record each lecture into a folder named after the course code. StudyHub keeps its own copy of transcripts, so Granola's auto-deletion can't take them away. |

Courses are matched across sources by course code (`CS 231N`, `cs231n` and `CS-231N` all
match). Canvas defines the course list when it's connected.

## Commands

Run as `backend/.venv/bin/studyhub …` (or put `backend/.venv/bin` on your `PATH`):

| Command | What it does |
|---|---|
| `studyhub check` | Log in to each configured source and report what it can see |
| `studyhub sync [canvas gradescope goodnotes granola web]` | Pull new and changed material (all configured sources by default) |
| `studyhub serve [--port 8000] [--reload]` | Run the API and the web app |
| `studyhub ask "question" [--course "CS 231N"]` | Ask the agent from the terminal |
| `studyhub schedule "CS 231N" [--url … \| --csv … \| --show]` | Import the lecture schedule (from the synced syllabus, the course website, or a `number,date,title` CSV) so every lecture has a number, date and topic, recorded or not |
| `studyhub index [--rebuild]` | Build the semantic search index (it also updates after every sync) |
| `studyhub eval CASES.json [--reps 3]` | Ask a set of questions and check each answer's citations and content (see Evals below) |
| `studyhub transcribe [--course …] [--limit 50] [--dry-run]` | Transcribe handwritten note pages (math as LaTeX) with Claude vision; only new or changed pages are sent |
| `studyhub demo [--force]` | Load the example data set |

## How it works

```
Canvas / Gradescope / GoodNotes PDFs / Granola / course websites
        │  connectors (backend/studyhub/connectors)
        ▼
  normalize: PDF text per page, transcript windows with timestamps, HTML → Markdown
        ▼
  data/studyhub.db (SQLite): courses, lectures, resources, chunks + FTS5 index + vectors,
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
- **Search is hybrid.** The agent's search merges keyword (BM25) and semantic results by
  reciprocal rank fusion; each chunk is embedded with a header saying which course, lecture
  and source it came from. The search box in the app stays keyword-only so it's instant.
- **Citations are locators** such as `r42@41:12` (resource 42 at 41:12) or `r17#p27` (page 27).
  Tools print them next to everything they return, the agent cites them as `[[r42@41:12]]`,
  and the server drops any the agent didn't actually get from a tool.
- **Deadlines and grades come from tables, not search.** A Gradescope assignment that matches a
  Canvas one replaces it (Gradescope has the grade; Canvas lends the description).
- **Only this computer can talk to it.** The server answers requests addressed to `localhost`
  only, and refuses changes coming from other websites, so a page you have open elsewhere can't
  read your courses, change your settings or spend your API credits.
- **The agent can't change anything.** No tool writes, posts or submits, so instructions hidden
  in course content have nothing to trigger.
- **Two ways to reach Claude.** With an API key, StudyHub runs the chat loop itself on the
  Messages API. On a Claude subscription, Claude Code runs it (through the Claude Agent SDK),
  with StudyHub's tools served to it in-process. Everything else Claude Code can do is switched
  off: no shell, file or web tools, no settings, hooks, skills or other MCP servers, so the same
  read-only tools are all the agent has. Chat checks this at the start of every answer.

## Evals

To know whether a change to search or prompts actually helps, keep a set of real questions with
the source a good answer should cite, and run it before and after:

```bash
backend/.venv/bin/studyhub eval backend/evals/demo.json          # the example set, on `studyhub demo` data
backend/.venv/bin/studyhub eval data/evals/mine.json --reps 3    # your own; data/ isn't committed
```

A case names the sources the way you'd recognize them (`{"title": "CS 231N Lecture 3", "at":
"47:30"}`, `{"assignment": "Quiz 1", "question": 2}`), plus words the answer must or must not
contain and tools it should use; `backend/evals/demo.json` shows every option. Runs use a copy
of the database, say how many questions they'll ask before starting (paid per request with an API
key; counted against your plan's limits on a subscription), and write
`results.jsonl`, `errors.jsonl` (API failures, never scored), full transcripts and `summary.json`
(pass rate with a 95% interval) under `data/evals/runs/`.

## Tests

```bash
make test
```

GitHub Actions runs the same checks on every push (`.github/workflows/ci.yml`).

The connectors are tested against fake Canvas, Granola and Gradescope responses. The API chat loop
is tested against a scripted model and against the real SDK talking to a local stand-in server;
the subscription loop against a scripted Agent SDK, and through the real Claude Code CLI talking to
a local stand-in server (with a dummy key, never your login).

## Not built yet

- Granola without a Business plan (its MCP server, which needs an OAuth flow).
- Graded-submission PDFs from Gradescope.
- An eval set of your real questions (Phase 4).
