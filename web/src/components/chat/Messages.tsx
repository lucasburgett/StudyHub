import { useLayoutEffect, useMemo, useRef } from 'react'
import { Markdown } from '../Markdown'
import { Loading, Spinner } from '../ui'
import { linkCitations } from './citations'
import type { ChatMessage } from './useChat'

function ToolTrail({ message }: { message: ChatMessage }) {
  return (
    <ul className="trail" aria-label="What the assistant looked at">
      {message.tools.map((tool) => {
        const active = tool.status === 'running' && message.streaming
        return (
          <li key={tool.id} className={`tool ${tool.status}`}>
            {active ? <Spinner /> : <span className="tool-mark" aria-hidden="true" />}
            <span>
              {tool.label}
              {tool.summary ? ` · ${tool.summary}` : ''}
              {tool.status === 'error' && !tool.summary ? ' · failed' : ''}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

function AssistantMessage({ message, onCitationClick }: { message: ChatMessage; onCitationClick: () => void }) {
  const text = useMemo(
    () => linkCitations(message.text, message.citations, message.streaming),
    [message.text, message.citations, message.streaming],
  )
  const hasBody = text || message.streaming || message.error || message.stopped
  return (
    <div className="msg-a-wrap">
      {message.tools.length > 0 && <ToolTrail message={message} />}
      {hasBody && (
        <div className="msg-a">
          {text ? (
            <Markdown text={text} citations={message.citations} onCitationClick={onCitationClick} />
          ) : message.streaming ? (
            <span className="typing" role="status" aria-label="Working on an answer">
              <i />
              <i />
              <i />
            </span>
          ) : null}
          {message.error && (
            <p className="msg-error" role="alert">
              {message.error}
            </p>
          )}
          {message.stopped && <p className="msg-note">Stopped.</p>}
        </div>
      )}
    </div>
  )
}

interface MessagesProps {
  messages: ChatMessage[]
  loading: boolean
  error: string | null
  streaming: boolean
  onCitationClick: () => void
}

/** Scrollable message list that follows new content while the reader is at the bottom. */
export function Messages({ messages, loading, error, streaming, onCitationClick }: MessagesProps) {
  const listRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)
  const countRef = useRef(0)

  useLayoutEffect(() => {
    const el = listRef.current
    if (!el) return
    if (messages.length > countRef.current) followRef.current = true
    countRef.current = messages.length
    if (followRef.current) el.scrollTop = el.scrollHeight
  }, [messages])

  const onScroll = () => {
    const el = listRef.current
    if (el) followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }

  return (
    <div className="messages" ref={listRef} onScroll={onScroll} aria-label="Conversation" aria-busy={streaming}>
      {loading && <Loading label="Loading conversation…" />}
      {error && (
        <p className="msg-error" role="alert">
          {error}
        </p>
      )}
      {!loading && !error && messages.length === 0 && (
        <div className="chat-empty">
          <p>Ask anything about your classes. Answers cite the exact slide, page, or minute they came from.</p>
        </div>
      )}
      {messages.map((m) =>
        m.role === 'user' ? (
          <div key={m.key} className="msg-u">
            {m.text}
          </div>
        ) : (
          <AssistantMessage key={m.key} message={m} onCitationClick={onCitationClick} />
        ),
      )}
    </div>
  )
}
