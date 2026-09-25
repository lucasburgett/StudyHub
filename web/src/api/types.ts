// Types mirroring docs/API.md. Timestamps are ISO 8601 UTC strings; plain dates are YYYY-MM-DD.

export type Source = 'canvas' | 'gradescope' | 'goodnotes' | 'granola' | 'web'

export type Kind = 'slides' | 'file' | 'page' | 'spec' | 'notes' | 'transcript' | 'submission' | 'announcement'

export interface ResourceSummary {
  id: number
  course_id: number
  source: Source
  kind: Kind
  title: string
  occurred_at: string | null
  lecture_id: number | null
  url: string | null
  has_file: boolean
  page_count: number | null
  duration_min: number | null
}

/** done / past: homework from class pages, which you tick off in StudyHub. */
export type AssignmentStatus = 'upcoming' | 'submitted' | 'graded' | 'missing' | 'unknown' | 'done' | 'past'

export interface AssignmentSummary {
  id: number
  course_id: number
  source: 'canvas' | 'gradescope'
  title: string
  due_at: string | null
  points: number | null
  score: number | null
  status: AssignmentStatus
  url: string | null
  spec_resource_id: number | null
  /** Homework from a class page: no submission to go by, so it has a Done checkbox. */
  checkable: boolean
}

/** From GET /api/assignments, which spans every class. */
export interface AssignmentWithCourse extends AssignmentSummary {
  course_code: string
}

export type LectureGap = 'recording' | 'notes' | 'slides'

export interface LectureResource extends ResourceSummary {
  /** For a notebook spanning many lectures: the first and last page for this lecture. */
  pages: [number, number] | null
}

export interface Lecture {
  id: number
  number: number | null
  title: string | null
  date: string | null
  resources: LectureResource[]
  missing: LectureGap[]
}

// Status and sync

export interface SyncRun {
  status: 'ok' | 'error'
  started_at: string
  finished_at: string | null
  items_changed: number
  /** Non-fatal problems, e.g. notebooks not matched to a course. */
  warnings: string[]
  error: string | null
}

export interface SourceStatus {
  source: Source
  configured: boolean
  running: boolean
  last_run: SyncRun | null
}

export interface Status {
  demo: boolean
  agent_ready: boolean
  /** How chat reaches Claude: an API key, or the Claude subscription Claude Code is logged in with. */
  agent_backend: 'api' | 'subscription' | null
  sources: SourceStatus[]
}

export interface SyncStarted {
  started: Source[]
}

// Courses

export interface CourseSummary {
  id: number
  code: string
  title: string | null
  term: string | null
  counts: { lectures: number; resources: number; assignments: number }
  next_due: { id: number; title: string; due_at: string } | null
}

export interface CourseDetail extends CourseSummary {
  term_start: string | null
  site_url: string | null
  canvas_url: string | null
}

export interface TimelineWeek {
  week: number | null
  label: string
  start: string | null
  lectures: Lecture[]
  assignments: AssignmentSummary[]
  other: ResourceSummary[]
}

export interface Timeline {
  weeks: TimelineWeek[]
}

// Resources

export interface PageText {
  page: number
  text: string
}

export interface Segment {
  seconds: number
  label: string
  text: string
}

export interface ResourceDetail extends ResourceSummary {
  course_code: string
  markdown: string | null
  summary: string | null
  pages: PageText[] | null
  segments: Segment[] | null
  file_url: string | null
}

// Assignments

export interface Feedback {
  question: string
  score: number | null
  max_score: number | null
  rubric_items: string[]
  comment: string | null
}

export interface AssignmentDetail extends AssignmentSummary {
  course_code: string
  description: string | null
  feedback: Feedback[]
}

// Search

export interface SearchHit {
  chunk_id: number
  resource_id: number
  course_code: string
  source: Source
  kind: Kind
  title: string
  locator: string
  label: string
  snippet: string
  lecture_id: number | null
  page: number | null
  seconds: number | null
}

// Chat

export interface ChatScope {
  course_id?: number
  resource_id?: number
}

export interface ChatRequest {
  thread_id?: number
  message: string
  scope?: ChatScope
}

export interface Citation {
  locator: string
  label: string
  resource_id: number | null
  assignment_id: number | null
  page: number | null
  seconds: number | null
}

export type Citations = Record<string, Citation>

export type ToolStatus = 'running' | 'done' | 'error'

export interface ToolEvent {
  id: string
  name: string
  label: string
  status: ToolStatus
  summary?: string
}

export type ChatEvent =
  | { event: 'thread'; data: { thread_id: number } }
  | { event: 'tool'; data: ToolEvent }
  | { event: 'sources'; data: { citations: Citations } }
  | { event: 'text'; data: { delta: string } }
  | { event: 'done'; data: { message_id: number; usage?: Record<string, unknown> } }
  | { event: 'error'; data: { message: string } }

export interface ThreadSummary {
  id: number
  title: string
  scope: ChatScope
  updated_at: string
}

export interface ThreadMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
  tools: { name: string; label: string; summary?: string }[]
  citations: Citations
  created_at: string
}

export interface ThreadDetail {
  id: number
  title: string
  scope: ChatScope
  messages: ThreadMessage[]
}

// ---------------------------------------------------------------------------
// Settings (GET/PUT /api/settings)

export type SettingKind = 'text' | 'secret' | 'path' | 'lines' | 'select' | 'bool'

export interface SettingField {
  key: string
  label: string
  kind: SettingKind
  help: string
  placeholder: string
  options: string[]
  /** Always null for secrets: the server never sends them back. */
  value: string | null
  is_set: boolean
  /** Secrets only: "…ab12" when set. */
  hint?: string | null
  /** Set by an environment variable, which wins over backend/.env. */
  locked: boolean
}

/** A source ("canvas", …) or "claude" / "search" / "general". */
export interface SettingGroup {
  id: string
  title: string
  intro: string
  checkable: boolean
  fields: SettingField[]
}

export interface SettingsPage {
  env_file: string
  groups: SettingGroup[]
}

export interface CheckResult {
  ok: boolean
  message: string
}
