# StudyHub handoff

For the next agent picking this up on Lucas's Mac. Read this, then `CLAUDE.md` for the
standing conventions. Written 2026-09-24 at commit `d009d03` on branch
`claude/class-resources-chat-qcay22`, where CI passed. No pull request is open yet; ask before
opening one into `main`.

## What StudyHub is

A local-first study hub for a Stanford student, Autumn 2026. It syncs Canvas, Gradescope,
GoodNotes (via Auto Backup PDFs), Granola (lecture recordings) and course websites into one
SQLite database. The web app shows each course as a timeline of lectures. Next to it is a
Claude chat agent that searches and reads everything and cites the exact slide page,
recording timestamp or notes page.

- **Design rationale** (why retrieval is used to *find*, and whole documents are read to
  *understand*): `docs/plan.html`, also published as
  https://claude.ai/artifact/1UeyZEkxntGZKVgPnxtM7L.
- **Web ↔ backend contract:** `docs/API.md`. When you change it, change both sides.
- **Setup and commands:** `README.md`.

## Where things stand

Everything below is built and tested against fakes: 50 backend tests, plus web lint and a
production build.

**Nothing has run against Lucas's real accounts or the real Claude API yet.** The build
sandbox had no API key, and its network blocked `canvas.stanford.edu` and the other source
hosts. Your first job is to make it work on real data (section below).

| Area | State | Files |
|---|---|---|
| Canvas (REST API, personal access token) | Built; tested against a fake API | `backend/studyhub/connectors/canvas.py` |
| Gradescope (unofficial `gradescopeapi`, email + password) | Built. Totals should work; per-question feedback parsing is a guess | `connectors/gradescope.py` |
| GoodNotes (Auto Backup PDFs in a local folder) | Built; tested with generated PDFs | `connectors/goodnotes.py` |
| Granola (public API; needs a Business plan) | Built from the published OpenAPI spec | `connectors/granola.py` |
| Course websites (`COURSE_SITES`) | Built; the parser is modeled on the CS 231N schedule table | `connectors/web.py` |
| Lecture linking (rebuilt after every sync) | Built | `ingest/linking.py` |
| Schedule import (`studyhub schedule`) | Built (Claude structured output, URL or CSV) | `ingest/schedule.py` |
| Handwriting transcription (`studyhub transcribe`) | Built; never run for real | `ingest/handwriting.py` |
| Search: FTS5 BM25 + optional embeddings, merged by reciprocal rank fusion (RRF) | Built. Embeddings come from Voyage `voyage-4` or local fastembed | `search.py`, `embeddings.py` |
| Agent: 8 read-only tools, streaming, citation validation | Built. Tested with a scripted model and with the real SDK against a local stand-in server | `agent/` |
| Web app: timeline, viewer, chat, search, sync status | Built; checked end to end in headless Chromium on example data | `web/src/` |
| Auto-sync while serving, CI, Makefile | Built | `sync.py`, `.github/workflows/ci.yml`, `Makefile` |
| Settings page (writes `backend/.env`, Test connection per source) | Built; used in a real browser | `envfile.py`, `checks.py`, `web/src/components/settings/` |
| Localhost-only guard (Host and Origin checks) | Built | `api.py` (`local_only`) |
| Eval harness (`studyhub eval`) | Built; run against a stand-in model only | `evals.py`, `backend/evals/demo.json` |

## First task: make it work on real data

Do this with Lucas at the keyboard; it needs his credentials.

1. `make setup`, `make serve`, then open **Settings** (the sliders icon) and fill in what he has;
   **Test connection** checks each one. It writes `backend/.env`; editing that file by hand works too. Never commit `.env`, and
   never paste secrets into files that are tracked.
   - He shared a Canvas token in a chat on 2026-09-24. Suggest he revoke it (Canvas →
     Account → Settings → Approved Integrations) and create a new one with an expiry date for
     `.env`.
   - `GOODNOTES_DIR` is usually
     `~/Library/CloudStorage/GoogleDrive-<email>/My Drive/<backup folder>` once GoodNotes Auto
     Backup is set to Google Drive in PDF format.
2. `make check`. Each configured source should log in and list what it sees.
3. `backend/.venv/bin/studyhub sync canvas`, then look at the result:
   - `make serve` and browse the timeline, or
   - query the database directly: `sqlite3 data/studyhub.db 'select code,title,term from courses'`.

Then go down the list of **untested assumptions**, most likely to break first:

1. **Canvas course names → codes.** `util.course_codes` (`backend/studyhub/util.py:22`) must
   pull "CS 231N" out of Stanford's real `course_code` and `name` values.
   - A course without a recognizable code is skipped silently (logged at INFO).
   - Also check: modules or files that say "Lecture N", the Files tab being hidden (403/401
     handling in `canvas.py:44`), and whether downloads redirect to a file-store host.
2. **Claude API request shape.** Run `studyhub ask "What's due this week?"`. The request uses:
   - `client.beta.messages.stream` with `model="claude-opus-5"`
   - `thinking={"type": "adaptive"}` and `output_config={"effort": "medium"}`
   - top-level `cache_control`
   - `betas=["server-side-fallback-2026-07-01"]` and `fallbacks="default"`
     (`agent/chat.py:27`)
   - tools with `eager_input_streaming: true`

   The SDK accepts all of this (tested), but the live API hasn't confirmed it. If anything
   400s, fix it here. Load the `claude-api` skill before touching this code; don't rely on
   memory.
3. **Gradescope.**
   - Login with a Gradescope password; Stanford SSO users set one via "Forgot password".
   - `parse_submission_props` (`connectors/gradescope.py:64`) guesses at the JSON embedded in
     the submission page (`data-react-props` on `AssignmentSubmissionViewer`). Look at a real
     graded submission's HTML and fix the parser.
   - Then add a sanitized fixture test, with names and emails removed.
4. **Granola.** The public API needs a Business plan; ask Lucas which plan he has.
   - The paged transcript endpoint `/v1/notes/{id}/transcript` isn't in the docs we could
     fetch. `GranolaClient.transcript` (`connectors/granola.py:70`) accepts
     `transcript` / `items` / `data` keys. Confirm against a long recording.
   - If he's on the free plan, see "Granola without Business" below.
5. **GoodNotes.**
   - Check whether the exported PDF's text layer includes the recognized handwriting.
   - Check whether dates written on the first two lines of a page are detected
     (`util.find_date`, `util.py:133`). That detection is how note pages attach to lectures.
   - Try `studyhub transcribe --dry-run`, then `--limit 5`, and read the output quality.
6. **Course websites.** Point `COURSE_SITES` at this term's CS 231N schedule page. The one
   used to design the parser was Spring 2026. Check that `parse_links`
   (`connectors/web.py:58`) gets the lecture numbers, dates and titles right.
7. **Lecture linking on real data.** Open the timeline:
   - Lectures should not be duplicated.
   - Slides should sit on the right day.
   - "Not recorded" gaps should make sense.

   If lecture numbers come out wrong, `studyhub schedule "CS 231N" --url <schedule page>`
   anchors them.
8. **Embeddings.** Set either `VOYAGE_API_KEY` or `pip install -e "./backend[local-embeddings]"`,
   then run `studyhub index`. Confirm that `studyhub check` shows semantic search as on.

For every fix: add a regression test with a small, **sanitized** fixture (no real names,
emails, grades or tokens), and run `make test`.

## After that, in priority order

1. **Evals (Phase 4 of the plan).** The harness is built (`studyhub eval`; format in
   `backend/evals/demo.json`). What's missing is the questions:
   - Collect about 40 of Lucas's real questions with him, each with the source a good answer
     should cite. Keep them in `data/evals/` (not committed).
   - Cover both directions: include questions the materials *don't* answer.
   - Run with `--reps 3`, then use the results to tune retrieval and prompts. The `claude-api`
     skill's `build-eval` and `hillclimb` flows fit here; get his OK on cost first.
2. **Granola without Business.** Granola's MCP server (`https://mcp.granola.ai/mcp`, OAuth)
   works on every plan. On the free plan it only returns AI notes (no raw transcripts), and
   only for the last 30 days, so sync must run at least weekly.
3. **Gradescope graded PDFs,** so rubric marks drawn on the page can be read (Claude vision).
4. **More in the UI.** Settings is done. Still CLI-only: schedule import and handwriting
   transcription. Buttons for both would be useful.
5. **Ed Discussion** (Stanford CS courses use it heavily). It has user API tokens; good
   candidate for a sixth source. Ask first.
6. **Smaller:**
   - pdf.js instead of an `<iframe>` for PDFs.
   - Restore scroll position when going back.
   - `strftime("%-d")` is Unix-only (fine on macOS).

## Invariants: don't break these

- **Lectures are derived data.** `rebuild_lectures` deletes and recreates them after every
  sync.
  - Anything a person decides (lecture numbers, dates, titles) goes in the `schedule` table,
    never in `lectures`.
  - Signal order: schedule > recording date > dated note pages > "Lecture N" in
    Canvas/site items.
- **Citations are locators:**

  | Locator | Meaning |
  |---|---|
  | `r42` | resource 42 |
  | `r42#p27` | resource 42, page 27 |
  | `r42@41:12` | resource 42 at 41:12 |
  | `a7` | assignment 7 |
  | `a7/q2` | assignment 7, question 2 |

  - Tools print locators, and the model cites them as `[[r42@41:12]]`.
  - The server keeps only locators a tool (or the course map) actually returned
    (`agent/chat.py`).
  - Locators outlive a sync only while resource IDs are stable. Upserts keep IDs, keyed on
    `(source, external_id)`.
- **Local only.** `api.local_only` refuses requests whose `Host` isn't localhost (DNS rebinding)
  and non-GET requests from other origins (CSRF). Keep it in front of every endpoint, especially
  now that `PUT /api/settings` writes credentials.
- **Agent tools are read-only.** That's the prompt-injection defense for course content. Don't
  add tools that write, post or submit without talking to Lucas.
- **Chat history is append-only.** `messages.api_json` stores the raw API turns, including
  thinking blocks with signatures. Replay them unchanged; never edit earlier turns.
- **Prompt caching.** Tool definitions (fixed order) and the system prompt (instructions plus
  course map) form the cached prefix. Anything that changes per message (the date, what's on
  screen) goes in the user turn.
- **Deadlines and grades come from tables, not search.**
  - A Gradescope assignment matching a Canvas one hides the Canvas row
    (`sync.merge_assignment_twins`).
- **Keep local copies.** Never overwrite a stored transcript with an empty one: Granola can
  auto-delete transcripts.
- **Tests never touch the network or real credentials.** Connectors are tested with
  `httpx.MockTransport` fakes. `conftest.py` blanks every credential variable and turns
  embeddings off.

## Useful commands

```bash
make setup | serve | dev | demo | check | sync | test
backend/.venv/bin/studyhub sync canvas            # one source
backend/.venv/bin/studyhub ask "…" --course "CS 231N"
backend/.venv/bin/studyhub schedule "CS 231N" --url <course schedule page>
backend/.venv/bin/studyhub transcribe --dry-run
backend/.venv/bin/studyhub index --rebuild
sqlite3 data/studyhub.db                          # the whole store; schema in backend/studyhub/schema.sql
```

`studyhub demo` loads example data, flagged in the database and shown with a banner in the
app. The first real sync wipes it.
