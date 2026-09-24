# StudyHub handoff

For the next agent picking this up on Lucas's Mac. Read this, then `CLAUDE.md` for the
standing conventions. No pull request is open yet; ask before opening one into `main`.

- First written 2026-09-24 by a cloud agent at commit `d009d03` on branch
  `claude/class-resources-chat-qcay22`, where CI passed.
- Updated 2026-09-24 after a session on Lucas's Mac (clone at `~/dev/StudyHub`, same branch).
  That session got `make setup` and `make test` passing locally, fixed a course-site parser
  bug, and researched running the agent on Lucas's Claude subscription (Task 1 below).
  `git log` shows what has landed since `b405828`.
- Updated 2026-09-24 by a second session on the Mac, which built Task 1 (chat on the Claude
  subscription) and ran it for real on Lucas's Max plan. It also updated `docs/plan.html` and
  the published artifact, including a new `docs/app-chat.png` of a real answer.

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

Everything below is built and tested against fakes: 84 backend tests, plus web lint and a
production build. They pass on Lucas's Mac too (Python 3.13, Node 26; CI uses 3.11 and 22).

**Chat works on Lucas's Claude Max plan; nothing else has run against his real accounts yet.**
The build sandbox had no API key, and its network blocked `canvas.stanford.edu` and the other
source hosts. On the Mac, as of the last session:

- `backend/.env` holds no credentials. There is no `ANTHROPIC_API_KEY` and no `ant` login.
- GoodNotes Auto Backup isn't set up; his Google Drive has no GoodNotes folder.
- Claude Code is logged in with his Max plan again (`claude auth login`, 2026-09-24), and chat
  on the subscription answered real questions on the demo data (Task 1).
- The only real course data tried so far is the public CS 231N schedule page.

Task 1 (the subscription backend) is done. Task 2 (real data) waits for him to fill in
Settings or `backend/.env`.

| Area | State | Files |
|---|---|---|
| Canvas (REST API, personal access token) | Built; tested against a fake API | `backend/studyhub/connectors/canvas.py` |
| Gradescope (unofficial `gradescopeapi`, email + password) | Built. Totals should work; per-question feedback parsing is a guess | `connectors/gradescope.py` |
| GoodNotes (Auto Backup PDFs in a local folder) | Built; tested with generated PDFs | `connectors/goodnotes.py` |
| Granola (public API; needs a Business plan) | Built from the published OpenAPI spec | `connectors/granola.py` |
| Course websites (`COURSE_SITES`) | Built; the parser handles the live CS 231N (Spring 2026) schedule page correctly | `connectors/web.py` |
| Lecture linking (rebuilt after every sync) | Built | `ingest/linking.py` |
| Schedule import (`studyhub schedule`) | Built (Claude structured output, URL or CSV) | `ingest/schedule.py` |
| Handwriting transcription (`studyhub transcribe`) | Built; never run for real | `ingest/handwriting.py` |
| Search: FTS5 BM25 + optional embeddings, merged by reciprocal rank fusion (RRF) | Built. Embeddings come from Voyage `voyage-4` or local fastembed | `search.py`, `embeddings.py` |
| Agent: 8 read-only tools, streaming, citation validation | Built on the Messages API. Tested with a scripted model and with the real SDK against a local stand-in server | `agent/chat.py`, `agent/tools.py` |
| Agent on the Claude subscription (Claude Agent SDK) | Built and working on Lucas's Max plan. Tested with a scripted SDK and with the real Claude Code CLI against a local stand-in server | `agent/subscription.py` |
| Web app: timeline, viewer, chat, search, sync status | Built; checked end to end in headless Chromium on example data | `web/src/` |
| Auto-sync while serving, CI, Makefile | Built | `sync.py`, `.github/workflows/ci.yml`, `Makefile` |
| Settings page (writes `backend/.env`, Test connection per source) | Built; used in a real browser | `envfile.py`, `checks.py`, `web/src/components/settings/` |
| Localhost-only guard (Host and Origin checks) | Built | `api.py` (`local_only`) |
| Eval harness (`studyhub eval`) | Built; run against a stand-in model only | `evals.py`, `backend/evals/demo.json` |

## Task 1 (built): chat on Lucas's Claude subscription

Chat can run on Lucas's Claude Max plan instead of an API key, through the Claude Agent SDK
(`claude-agent-sdk`), which drives Claude Code with his Claude Code login. The API path stays;
the subscription is a second backend.

### Policy (checked 2026-09-24)

- Anthropic's help article
  [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
  (last updated 2026-06-16) says Agent SDK, `claude -p` and third-party app usage "still draw
  from your subscription's usage limits".
- The [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) says third-party
  developers may not *offer* claude.ai login in their products without approval. That covers
  apps shipped to other people. StudyHub runs only for Lucas, on his own login.
- This policy changed three times in 2026 (an April ban, a May credit, a June pause). Re-read
  the help article before building more on it. The churn is why the API path stays.

### What's built

- **Choosing the backend:** `STUDYHUB_AGENT=auto|api|subscription`, also on the Settings page
  (Claude → Chat runs on).
  - `auto` uses the API key when one is set, otherwise the subscription when Claude Code is
    logged in.
  - `Settings.agent_backend` picks it. `subscription.claude_login()` runs
    `claude auth status` and caches the answer for 60 s.
  - `/api/status` now reports `agent_backend`.
- **Shared turn handling:** `agent/chat.py` has `begin_turn` / `finish_turn`, used by both
  loops. `run_chat` dispatches: a `client` argument means the API, otherwise it follows the
  configured backend.
- **The loop:** `agent/subscription.py`.
  - The SDK runs on a worker thread with its own event loop, and events come back through a
    queue.
  - Tool handlers run the `Toolbox` in worker threads behind a lock.
  - Closing the stream cancels Claude Code.
- **Lockdown:** `build_options` sets `tools=[]`, `strict_mcp_config`, `setting_sources=[]`,
  `skills=[]`, `permission_mode="dontAsk"`, `verbatim_prompts` (so `@path` in a question
  can't read a file), and `cwd=data/agent`.
  - At the start of every answer, the loop stops if the init message offers any tool that
    isn't `mcp__studyhub__*`.
  - Tested in `tests/test_subscription.py`, including the actual CLI flags.
- **Multi-turn:** `threads.backend` and `threads.agent_session_id` (migration in `db.py`).
  - A follow-up resumes the session.
  - If the session is gone, or the API answered last, a new session gets the earlier messages
    as `<earlier_conversation>` text.
  - Subscription answers are stored in `api_json` as plain text, so the API can pick the
    thread up.
- **Errors:** not logged in (`kind: "auth"`, says to run `claude auth login`), the usage limit
  (`kind: "limit"`, with the reset time when Claude Code sends one), too many steps, a missing
  CLI.
- **Other places:**
  - `studyhub check` and the Settings page's Test connection ask Claude one word through the
    same locked-down setup.
  - `studyhub eval` runs on either backend.
  - Schedule import and transcription still need an API key, and say so.

### Real runs (2026-09-24, demo data, Max plan)

- `studyhub check`: "Logged in to Claude Code (max plan). Chat runs on your subscription with
  claude-opus-5."
- `studyhub ask "What's due this week?"` (no API key) answered in 7 s. It used
  `list_assignments` and `list_announcements`, and its three citations (`a2`, `r14`, `r15`)
  all validated.
- A follow-up in the web chat continued the same thread and resumed the same Claude Code
  session. It used `get_feedback` and `search` and gave 8 valid citations, including slide
  pages and recording timestamps.
- Prompt caching works: a later question read 7,657 input tokens from cache.

### Found along the way

- **Lucas's standalone Claude Code login had expired.** The earlier "logged in" came from
  running `claude auth status` inside the Claude desktop app, which passes its own login to
  child processes. From a plain shell it said "Not logged in". He logged in again with
  `claude auth login`.
- **Starting StudyHub from inside Claude Code** (its terminal, or a session like this one)
  passes on `CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH`, which tells the child to wait for its
  parent to refresh the login. `CLI_ENV` in `subscription.py` blanks it; the CLI only checks
  it for truthiness. The chat above ran from such an environment.
- **The SDK runs its bundled CLI** (2.1.281 with SDK 0.2.159), not `~/.local/bin/claude`
  (2.1.185). Both use the same keychain login ("Claude Code-credentials").
- **`tools=[]` leaves exactly our tools.** The init message and the API request list the 8
  `mcp__studyhub__*` tools and nothing else, with no ToolSearch.
  - Claude Code still lists its built-in skills and slash commands in the init message.
    Without the Skill tool they can't run.
  - The requests to the model also carry a line Claude Code adds ("You are a Claude agent,
    built on Anthropic's Claude Agent SDK"), an environment note with the working directory,
    and `<total_tokens>` system messages. All harmless.
- **Big tool results:** Claude Code caps MCP output at 25k tokens, and above a size it saves
  the result to a file and shows the model a ~2k-character preview. With no Read tool, the
  model would never see the rest.
  - `MAX_MCP_OUTPUT_TOKENS=100000` and `maxResultSizeChars` on each tool fix this.
  - `tests/test_subscription_cli.py` checks that a ~100k-character lecture arrives whole.
- **The request Claude Code sends:** `model=claude-opus-5`, adaptive thinking, effort
  `medium`, `max_tokens` 64000, and `clear_thinking` context management.
- **Usage limit:** the SDK reports it as a `RateLimitEvent` with `status="rejected"` and
  `resets_at`, then an `AssistantMessage` with `error="rate_limit"`, and/or a `ResultMessage`
  with `api_error_status=429`. This comes from the SDK's types; not seen live yet.

### Still to do

1. **Schedule import and transcription on the subscription.**
   - Schedule import: `output_format={"type": "json_schema", ...}`, then read
     `ResultMessage.structured_output`.
   - Transcription: send page images as image blocks in a streaming-input prompt.
   - Every `query()` starts a CLI process, so batch several pages per call.
2. **`fallback_model`.** The SDK has it, but what triggers it is undocumented, so it's unused;
   the API path's `fallbacks="default"` has no equivalent here.

## Task 2: make it work on real data

Do this with Lucas at the keyboard; it needs his credentials. As of the last session he
hadn't filled in `backend/.env` yet.

1. `make setup` (again, to install `claude-agent-sdk`), `make serve`, then open **Settings** (the sliders icon) and fill in what he has;
   **Test connection** checks each one. It writes `backend/.env`; editing that file by hand works too. Never commit `.env`, and
   never paste secrets into files that are tracked.
   - He shared a Canvas token in a chat on 2026-09-24. Suggest he revoke it (Canvas →
     Account → Settings → Approved Integrations) and create a new one with an expiry date for
     `.env`.
   - `GOODNOTES_DIR` is usually
     `~/Library/CloudStorage/GoogleDrive-<email>/My Drive/<backup folder>` once GoodNotes Auto
     Backup is set to Google Drive in PDF format. Google Drive for Desktop is installed, but
     Auto Backup isn't on yet, so he has to turn it on first.
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

   The SDK accepts all of this (tested), and on 2026-09-24 it matched the `claude-api` skill's
   documented shape for Opus 5, including which blocks `echo_content` drops after a fallback.
   The live API still hasn't confirmed it, and it needs an API key; the subscription backend
   (Task 1) doesn't exercise this code. If anything 400s, fix it here. Load the `claude-api`
   skill before touching this code; don't rely on memory.
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
6. **Course websites.** CS 231N runs in the spring, so there is no autumn schedule page to
   point at. On 2026-09-24, `parse_links` (`connectors/web.py:65`) got the numbers, dates and
   titles right for all 16 lectures on the live Spring 2026 page. That session also fixed
   section and review-session slides, which were titled with the whole table row; they now
   take their cell's name ("Backprop Review Session"), with a regression test in
   `tests/test_web.py`. Ask Lucas which courses he's taking this autumn and which post slides
   on their own site, then add those to `COURSE_SITES` and check `parse_links` on each page.
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
  - Subscription turns are the exception: Claude Code keeps the real history in its session
    (`threads.agent_session_id`, files under `~/.claude/projects/`), and `api_json` gets the
    answer as plain text. An API turn clears the session id, so the next subscription turn
    starts a fresh session from the stored text.
- **The subscription agent is locked down.** No built-in Claude Code tools, settings, hooks,
  skills or other MCP servers (`subscription.build_options`), and the loop stops if Claude Code
  offers anything but `mcp__studyhub__*`. Keep both, and keep the tests that check them.
- **Prompt caching.** Tool definitions (fixed order) and the system prompt (instructions plus
  course map) form the cached prefix. Anything that changes per message (the date, what's on
  screen) goes in the user turn.
- **Deadlines and grades come from tables, not search.**
  - A Gradescope assignment matching a Canvas one hides the Canvas row
    (`sync.merge_assignment_twins`).
- **Keep local copies.** Never overwrite a stored transcript with an empty one: Granola can
  auto-delete transcripts.
- **Tests never touch the network or real credentials.** Connectors are tested with
  `httpx.MockTransport` fakes. `conftest.py` blanks every credential variable, turns
  embeddings off, and stubs the Claude Code login check. `test_subscription_cli.py` runs the
  real CLI with a dummy key against localhost, with `CLAUDE_CONFIG_DIR` in a temp dir.

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
