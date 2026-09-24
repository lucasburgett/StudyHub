import { useCallback, useEffect, useRef, useState } from 'react'
import { endpoints, getJson, isAbort, streamChat } from '../../api/client'
import type { ChatScope, Citations, ThreadDetail, ToolEvent, ToolStatus } from '../../api/types'
import { toApiError } from '../../lib/useApi'

interface ChatTool {
  id: string
  label: string
  status: ToolStatus
  summary?: string
}

export interface ChatMessage {
  key: string
  role: 'user' | 'assistant'
  text: string
  tools: ChatTool[]
  citations: Citations
  streaming: boolean
  stopped: boolean
  error: string | null
}

let keySeq = 0
const nextKey = () => `local-${++keySeq}`

function message(role: ChatMessage['role'], text: string, extra: Partial<ChatMessage> = {}): ChatMessage {
  return {
    key: nextKey(),
    role,
    text,
    tools: [],
    citations: {},
    streaming: false,
    stopped: false,
    error: null,
    ...extra,
  }
}

/** `tool` events arrive twice per call (running, then done/error); update in place by id. */
function upsertTool(tools: ChatTool[], ev: ToolEvent): ChatTool[] {
  const next: ChatTool = { id: ev.id, label: ev.label || ev.name, status: ev.status, summary: ev.summary }
  const i = tools.findIndex((t) => t.id === ev.id)
  if (i === -1) return [...tools, next]
  return tools.map((t, j) => (j === i ? { ...t, ...next, summary: next.summary ?? t.summary } : t))
}

function fromThread(thread: ThreadDetail): ChatMessage[] {
  return thread.messages.map((m) => ({
    key: `m${m.id}`,
    role: m.role,
    text: m.text,
    tools: m.tools.map((t, i) => ({
      id: `${m.id}-${i}`,
      label: t.label || t.name,
      status: 'done',
      summary: t.summary,
    })),
    citations: m.citations ?? {},
    streaming: false,
    stopped: false,
    error: null,
  }))
}

interface ChatController {
  threadId: number | null
  messages: ChatMessage[]
  streaming: boolean
  loadingThread: boolean
  threadError: string | null
  send: (text: string, scope: ChatScope | undefined) => void
  stop: () => void
  newChat: () => void
  openThread: (id: number) => void
}

/** Conversation state plus the SSE chat stream. `onThreadsChanged` runs after each answer finishes. */
export function useChat(onThreadsChanged: () => void): ChatController {
  const [threadId, setThreadId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [loadingThread, setLoadingThread] = useState(false)
  const [threadError, setThreadError] = useState<string | null>(null)

  const threadIdRef = useRef<number | null>(null)
  const streamAbort = useRef<AbortController | null>(null)
  const loadAbort = useRef<AbortController | null>(null)

  useEffect(
    () => () => {
      streamAbort.current?.abort()
      loadAbort.current?.abort()
    },
    [],
  )

  const setThread = (id: number | null) => {
    threadIdRef.current = id
    setThreadId(id)
  }

  const send = useCallback(
    (text: string, scope: ChatScope | undefined) => {
      const body = text.trim()
      if (!body || streamAbort.current) return
      const ctrl = new AbortController()
      streamAbort.current = ctrl
      const answer = message('assistant', '', { streaming: true })
      const patch = (fn: (m: ChatMessage) => ChatMessage) =>
        setMessages((all) => all.map((m) => (m.key === answer.key ? fn(m) : m)))

      setMessages((all) => [...all, message('user', body), answer])
      setStreaming(true)
      setThreadError(null)

      let finished = false
      streamChat(
        { thread_id: threadIdRef.current ?? undefined, message: body, scope },
        (ev) => {
          if (ctrl.signal.aborted) return
          switch (ev.event) {
            case 'thread':
              setThread(ev.data.thread_id)
              break
            case 'tool':
              patch((m) => ({ ...m, tools: upsertTool(m.tools, ev.data) }))
              break
            case 'sources':
              patch((m) => ({ ...m, citations: { ...m.citations, ...ev.data.citations } }))
              break
            case 'text':
              patch((m) => ({ ...m, text: m.text + ev.data.delta }))
              break
            case 'done':
              finished = true
              patch((m) => ({ ...m, streaming: false }))
              break
            case 'error':
              finished = true
              patch((m) => ({ ...m, streaming: false, error: ev.data.message }))
              break
          }
        },
        ctrl.signal,
      )
        .then(() => {
          if (!finished) patch((m) => ({ ...m, streaming: false, error: 'The answer was cut off before it finished.' }))
        })
        .catch((err: unknown) => {
          if (isAbort(err)) patch((m) => ({ ...m, streaming: false, stopped: true }))
          else patch((m) => ({ ...m, streaming: false, error: toApiError(err).message }))
        })
        .finally(() => {
          if (streamAbort.current === ctrl) streamAbort.current = null
          setStreaming(false)
          onThreadsChanged()
        })
    },
    [onThreadsChanged],
  )

  const stop = useCallback(() => streamAbort.current?.abort(), [])

  const newChat = useCallback(() => {
    streamAbort.current?.abort()
    loadAbort.current?.abort()
    setThread(null)
    setMessages([])
    setThreadError(null)
    setLoadingThread(false)
  }, [])

  const openThread = useCallback((id: number) => {
    streamAbort.current?.abort()
    loadAbort.current?.abort()
    const ctrl = new AbortController()
    loadAbort.current = ctrl
    setThread(id)
    setMessages([])
    setThreadError(null)
    setLoadingThread(true)
    getJson(endpoints.thread(id), ctrl.signal)
      .then((thread) => setMessages(fromThread(thread)))
      .catch((err: unknown) => {
        if (!isAbort(err)) setThreadError(toApiError(err).message)
      })
      .finally(() => {
        if (loadAbort.current === ctrl) {
          loadAbort.current = null
          setLoadingThread(false)
        }
      })
  }, [])

  return { threadId, messages, streaming, loadingThread, threadError, send, stop, newChat, openThread }
}
