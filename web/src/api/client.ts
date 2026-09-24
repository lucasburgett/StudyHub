import type {
  AssignmentDetail,
  AssignmentSummary,
  ChatEvent,
  ChatRequest,
  CourseDetail,
  CourseSummary,
  Kind,
  ResourceDetail,
  ResourceSummary,
  SearchHit,
  Status,
  SyncStarted,
  ThreadDetail,
  ThreadSummary,
  Timeline,
} from './types'

/** Thrown for any failed API call. `offline` means the backend could not be reached at all. */
export class ApiError extends Error {
  readonly status: number
  readonly offline: boolean

  constructor(message: string, status: number, offline: boolean) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.offline = offline
  }
}

const OFFLINE_MESSAGE = "Can't reach the StudyHub backend."

/** A typed GET endpoint. `T` is carried only at the type level so hooks can infer the response type. */
export interface Endpoint<T> {
  path: string
  readonly __response?: T
}

function endpoint<T>(path: string, params?: Record<string, string | number | null | undefined>): Endpoint<T> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== null && value !== undefined && value !== '') query.set(key, String(value))
  }
  const qs = query.toString()
  return { path: qs ? `${path}?${qs}` : path }
}

export const endpoints = {
  status: () => endpoint<Status>('/api/status'),
  courses: () => endpoint<CourseSummary[]>('/api/courses'),
  course: (id: number) => endpoint<CourseDetail>(`/api/courses/${id}`),
  timeline: (courseId: number) => endpoint<Timeline>(`/api/courses/${courseId}/timeline`),
  courseResources: (courseId: number, kinds: Kind[]) =>
    endpoint<ResourceSummary[]>(`/api/courses/${courseId}/resources`, { kind: kinds.join(',') }),
  courseAssignments: (courseId: number) => endpoint<AssignmentSummary[]>(`/api/courses/${courseId}/assignments`),
  resource: (id: number) => endpoint<ResourceDetail>(`/api/resources/${id}`),
  assignment: (id: number) => endpoint<AssignmentDetail>(`/api/assignments/${id}`),
  search: (q: string) => endpoint<SearchHit[]>('/api/search', { q, limit: 20 }),
  threads: (courseId: number | null) => endpoint<ThreadSummary[]>('/api/threads', { course_id: courseId }),
  thread: (id: number) => endpoint<ThreadDetail>(`/api/threads/${id}`),
}

async function errorFromResponse(res: Response): Promise<ApiError> {
  const body = await res.text().catch(() => '')
  let detail: string | null = null
  try {
    const parsed: unknown = JSON.parse(body)
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      const d = (parsed as { detail: unknown }).detail
      detail = typeof d === 'string' ? d : JSON.stringify(d)
    }
  } catch {
    // Not JSON. A 5xx without a JSON body comes from the dev proxy when the backend is down.
    if (res.status >= 500) return new ApiError(OFFLINE_MESSAGE, res.status, true)
  }
  return new ApiError(detail ?? `Request failed (${res.status})`, res.status, false)
}

async function send(path: string, init: RequestInit): Promise<Response> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch (err) {
    if (isAbort(err)) throw err
    throw new ApiError(OFFLINE_MESSAGE, 0, true)
  }
  if (!res.ok) throw await errorFromResponse(res)
  return res
}

export async function getJson<T>(ep: Endpoint<T>, signal?: AbortSignal): Promise<T> {
  const res = await send(ep.path, { headers: { Accept: 'application/json' }, signal })
  return (await res.json()) as T
}

/** Starts a background sync of every configured source. */
export async function startSync(): Promise<SyncStarted> {
  const res = await send('/api/sync', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: '{}',
  })
  return (await res.json()) as SyncStarted
}

export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

// ---------------------------------------------------------------------------
// Chat streaming (POST + text/event-stream). EventSource cannot POST, so the
// stream is read with fetch and parsed here.

interface RawSseEvent {
  event: string
  data: string
}

/** Minimal server-sent events parser: yields one event per blank-line-terminated block. */
async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<RawSseEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = ''
  let dataLines: string[] = []

  const takeEvent = (): RawSseEvent | null => {
    const ev = dataLines.length ? { event: eventName || 'message', data: dataLines.join('\n') } : null
    eventName = ''
    dataLines = []
    return ev
  }

  try {
    for (;;) {
      const { value, done } = await reader.read()
      // At end of stream, terminate any unfinished line and event so they are still delivered.
      buffer += done ? `${decoder.decode()}\n\n` : decoder.decode(value, { stream: true })

      let match: RegExpExecArray | null
      const lineBreak = /\r\n|\r|\n/g
      let consumed = 0
      while ((match = lineBreak.exec(buffer)) !== null) {
        // A lone trailing "\r" may be the first half of "\r\n"; wait for more input.
        if (!done && match[0] === '\r' && match.index === buffer.length - 1) break
        const line = buffer.slice(consumed, match.index)
        consumed = match.index + match[0].length

        if (line === '') {
          const ev = takeEvent()
          if (ev) yield ev
        } else if (!line.startsWith(':')) {
          const colon = line.indexOf(':')
          const field = colon === -1 ? line : line.slice(0, colon)
          let value = colon === -1 ? '' : line.slice(colon + 1)
          if (value.startsWith(' ')) value = value.slice(1)
          if (field === 'event') eventName = value
          else if (field === 'data') dataLines.push(value)
        }
      }
      buffer = buffer.slice(consumed)
      if (done) return
    }
  } finally {
    // Closes the connection if the consumer stopped early; a no-op once the stream has ended.
    reader.cancel().catch(() => {})
  }
}

const CHAT_EVENTS = new Set<ChatEvent['event']>(['thread', 'tool', 'sources', 'text', 'done', 'error'])

/**
 * POST /api/chat and deliver each parsed event to `onEvent` in order.
 * Resolves when the stream ends; rejects with ApiError on HTTP/network failure
 * or with an AbortError when `signal` is aborted.
 */
export async function streamChat(
  request: ChatRequest,
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await send('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(request),
    signal,
  })
  if (!res.body) throw new ApiError('The chat response had no body.', res.status, false)

  for await (const raw of readSse(res.body)) {
    if (!CHAT_EVENTS.has(raw.event as ChatEvent['event'])) continue
    let data: unknown
    try {
      data = JSON.parse(raw.data)
    } catch {
      continue
    }
    onEvent({ event: raw.event, data } as ChatEvent)
  }
}
