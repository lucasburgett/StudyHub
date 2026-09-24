# StudyHub HTTP API

The backend (FastAPI, `backend/`) serves JSON under `/api`. In development the
web app runs on Vite (port 5173) and proxies `/api` to the backend on port 8000.
In production the backend also serves the built web app from `web/dist`.

The server only answers requests whose `Host` is `127.0.0.1`, `localhost` or `::1` (plus any
names in `STUDYHUB_ALLOWED_HOSTS`), and rejects non-GET requests whose `Origin` is another site
or whose `Sec-Fetch-Site` is `cross-site`. That keeps other web pages from reading or changing it.

All timestamps are ISO 8601 strings in UTC (`2026-09-24T17:30:00Z`). Dates
without a time are `YYYY-MM-DD`. IDs are integers unless noted.

## Shared types

```ts
type Source = "canvas" | "gradescope" | "goodnotes" | "granola" | "web"; // web = course websites

type Kind =
  | "slides"        // lecture slide deck (PDF)
  | "file"          // any other course file
  | "page"          // Canvas wiki page or syllabus
  | "spec"          // assignment description
  | "notes"         // GoodNotes notebook (PDF)
  | "transcript"    // Granola lecture recording (transcript + summary)
  | "submission"    // graded Gradescope submission
  | "announcement"; // Canvas announcement

interface ResourceSummary {
  id: number;
  course_id: number;
  source: Source;
  kind: Kind;
  title: string;
  occurred_at: string | null;   // when it happened (lecture time, post time, upload time)
  lecture_id: number | null;
  url: string | null;           // link to the item on its own site (Canvas, Granola…)
  has_file: boolean;            // true when GET /api/resources/{id}/file returns a PDF
  page_count: number | null;    // PDFs only
  duration_min: number | null;  // transcripts only
}

interface AssignmentSummary {
  id: number;
  course_id: number;
  source: "canvas" | "gradescope";
  title: string;
  due_at: string | null;
  points: number | null;        // max points
  score: number | null;         // your score, when graded
  status: "upcoming" | "submitted" | "graded" | "missing" | "unknown";
  url: string | null;
  spec_resource_id: number | null; // the "spec" resource with the full description, if any
}

interface Lecture {
  id: number;
  number: number | null;
  title: string | null;
  date: string | null;          // YYYY-MM-DD
  resources: (ResourceSummary & {
    pages: [number, number] | null; // for a notebook spanning many lectures: the pages for this lecture
  })[];
  missing: ("recording" | "notes" | "slides")[]; // gaps worth showing
}
```

## Status and sync

`GET /api/status`

```ts
{
  demo: boolean;               // true when the database holds the example data set
  agent_ready: boolean;        // true when chat can reach Claude
  agent_backend: "api" | "subscription" | null; // an API key, or the Claude plan Claude Code is logged in with
  sources: {
    source: Source;
    configured: boolean;       // credentials/paths present in .env
    running: boolean;
    last_run: {
      status: "ok" | "error";
      started_at: string;
      finished_at: string | null;
      items_changed: number;
      warnings: string[];      // non-fatal problems, e.g. notebooks not matched to a course
      error: string | null;
    } | null;
  }[];
}
```

`POST /api/sync` with body `{ "source"?: Source }` starts a sync in the
background (all configured sources when `source` is omitted). Returns
`{ "started": Source[] }`. Poll `GET /api/status` for progress.

## Courses

`GET /api/courses`

```ts
{
  id: number;
  code: string;                // "CS 231N"
  title: string | null;        // "Deep Learning for Computer Vision"
  term: string | null;         // "Autumn 2026"
  counts: { lectures: number; resources: number; assignments: number };
  next_due: { id: number; title: string; due_at: string } | null;
}[]
```

`GET /api/courses/{id}` returns one course with the same fields plus
`term_start: string | null`, `site_url: string | null`, `canvas_url: string | null`.

`GET /api/courses/{id}/timeline`

```ts
{
  weeks: {
    week: number | null;       // 1-based week of term; null = undated
    label: string;             // "Week 1" or "Undated"
    start: string | null;      // Monday of that week, YYYY-MM-DD
    lectures: Lecture[];       // sorted by date/number
    assignments: AssignmentSummary[]; // due that week
    other: ResourceSummary[];  // dated items not tied to a lecture
  }[];
}
```

`GET /api/courses/{id}/resources?kind=&source=` → `ResourceSummary[]`, newest first.
`kind` and `source` accept comma-separated lists.

`GET /api/courses/{id}/assignments` → `AssignmentSummary[]` sorted by due date.

## Resources

`GET /api/resources/{id}`

```ts
ResourceSummary & {
  course_code: string;
  markdown: string | null;     // normalized text of the whole item
  summary: string | null;      // AI or source-provided summary (transcripts)
  pages: { page: number; text: string }[] | null;             // PDFs
  segments: { seconds: number; label: string; text: string }[] | null; // transcripts, label "41:12"
  file_url: string | null;     // "/api/resources/{id}/file" when has_file
}
```

`GET /api/resources/{id}/file` streams the original PDF (`Content-Disposition: inline`).
Append `#page=N` in an `<iframe>` to open at a page.

## Assignments

`GET /api/assignments/{id}`

```ts
AssignmentSummary & {
  course_code: string;
  description: string | null;  // markdown
  feedback: {
    question: string;
    score: number | null;
    max_score: number | null;
    rubric_items: string[];    // rubric lines applied
    comment: string | null;
  }[];
}
```

## Search

`GET /api/search?q=…&course_id=&source=&limit=20`

```ts
{
  chunk_id: number;
  resource_id: number;
  course_code: string;
  source: Source;
  kind: Kind;
  title: string;
  locator: string;             // see Citations
  label: string;               // "L3 slides · p.27"
  snippet: string;             // HTML-escaped text, matches wrapped in <mark>…</mark>
  lecture_id: number | null;
  page: number | null;         // open the viewer here
  seconds: number | null;
}[]
```

## Chat

`POST /api/chat` with body

```ts
{
  thread_id?: number;          // omit to start a new thread
  message: string;
  scope?: { course_id?: number; resource_id?: number };
}
```

Responds with `text/event-stream`. Events, in order:

| event       | data                                                                 |
|-------------|----------------------------------------------------------------------|
| `thread`    | `{ "thread_id": number }` (first event)                              |
| `tool`      | `{ "id": string, "name": string, "label": string, "status": "running" \| "done" \| "error", "summary"?: string }` — sent twice per call: running, then done/error with a short summary such as `"7 hits"` |
| `sources`   | `{ "citations": Record<string, Citation> }` — citable locators returned by tools so far; merge into a map |
| `text`      | `{ "delta": string }` — answer text as it streams                    |
| `done`      | `{ "message_id": number, "usage"?: object }`                         |
| `error`     | `{ "message": string, "kind"?: string }` — `kind` is one of `api`, `auth` (Claude Code isn't logged in), `limit` (the subscription's usage limit), `refusal`, `truncated`, `steps`, `tool_json`, `setup` |

```ts
interface Citation {
  locator: string;
  label: string;               // short chip text: "L3 · 41:12", "L3 slides · p.27", "A1 · Q2"
  resource_id: number | null;
  assignment_id: number | null;
  page: number | null;         // open PDF at this page
  seconds: number | null;      // open transcript at this time
}
```

The answer text cites sources inline as `[[locator]]`, for example
`…negative log probability [[r42@41:12]] [[r17#p27]].` Render each marker whose
locator is in the citations map as a clickable chip that opens the viewer at
`page` / `seconds` (or the assignment). Drop markers whose locator is not in the
map; the server never sends citations a tool did not return.

### Locators

| Form        | Meaning                                 |
|-------------|-----------------------------------------|
| `r42`       | whole resource 42                       |
| `r42#p27`   | resource 42, page 27                    |
| `r42@41:12` | resource 42 at 41 min 12 s (transcripts) |
| `a7`        | assignment 7                            |
| `a7/q2`     | assignment 7, feedback for question 2   |

### Threads

`GET /api/threads?course_id=` → `{ id, title, scope, updated_at }[]`

`GET /api/threads/{id}` →

```ts
{
  id: number;
  title: string;
  scope: { course_id?: number; resource_id?: number };
  messages: {
    id: number;
    role: "user" | "assistant";
    text: string;              // with [[locator]] markers
    tools: { name: string; label: string; summary?: string }[];
    citations: Record<string, Citation>;
    created_at: string;
  }[];
}
```

## Settings

`GET /api/settings` describes the editable parts of `backend/.env`, grouped for the settings page.
Secrets are never returned: `value` is `null`, `is_set` says whether one is saved, and `hint` shows
its last four characters.

```ts
{
  env_file: string;            // absolute path of backend/.env
  groups: {
    id: string;                // a Source, or "claude" | "search" | "general"
    title: string;
    intro: string;
    checkable: boolean;        // has a Test connection button
    fields: {
      key: string;             // the .env variable, e.g. "CANVAS_TOKEN"
      label: string;
      kind: "text" | "secret" | "path" | "lines" | "select" | "bool";
      help: string;
      placeholder: string;
      options: string[];       // for "select"
      value: string | null;    // null for secrets; "lines" values are newline-separated
      is_set: boolean;
      hint?: string | null;    // secrets: "…ab12"
      locked: boolean;         // set by an environment variable, which overrides the file
    }[];
  }[];
}
```

`PUT /api/settings` with `{ "values": { "CANVAS_TOKEN": "…", "GRANOLA_API_KEY": null } }` writes the
given keys (`null` = leave as is, `""` = clear) and returns the same shape as GET. Invalid values
return 400 with a `detail` message. Changes apply without a restart.

`POST /api/settings/check/{target}` (a Source, `claude` or `search`) tries the saved settings and
returns `{ "ok": boolean, "message": string }`.
