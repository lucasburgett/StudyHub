import { useCallback, useState } from 'react'
import { endpoints } from '../../api/client'
import type { ChatScope } from '../../api/types'
import { useApp, type ViewContext } from '../../lib/appContext'
import { formatAgo } from '../../lib/format'
import { useApi } from '../../lib/useApi'
import { CloseIcon } from '../icons'
import { Composer } from './Composer'
import { Messages } from './Messages'
import { useChat } from './useChat'

type Level = 'all' | 'course' | 'item'

// Below this width the panel overlays the page, so it closes when a citation opens the viewer.
const OVERLAY_QUERY = '(max-width: 1100px)'

interface ChatPanelProps {
  context: ViewContext
  open: boolean
  onClose: () => void
}

export function ChatPanel({ context, open, onClose }: ChatPanelProps) {
  const { sync, courses } = useApp()
  const { courseId, resource } = context
  const listedCode = courses.data?.find((c) => c.id === courseId)?.code
  // Only fetch the course when its code isn't already known from the list or the open item.
  const course = useApi(courseId !== null && !listedCode && !resource ? endpoints.course(courseId) : null)
  const courseCode =
    resource?.courseCode ?? listedCode ?? course.data?.code ?? (courseId !== null ? 'This course' : null)

  // Scope defaults to the most specific context on screen; a manual pick holds until the context changes.
  const levels: Level[] = ['all']
  if (courseId !== null) levels.push('course')
  if (resource) levels.push('item')
  const contextKey = `${courseId ?? ''}:${resource?.id ?? ''}`
  const [picked, setPicked] = useState<{ key: string; level: Level } | null>(null)
  const level =
    picked && picked.key === contextKey && levels.includes(picked.level) ? picked.level : levels[levels.length - 1]

  let scope: ChatScope | undefined
  if (level === 'course' && courseId !== null) scope = { course_id: courseId }
  if (level === 'item' && resource) scope = { course_id: courseId ?? undefined, resource_id: resource.id }

  const threads = useApi(endpoints.threads(level === 'all' ? null : courseId))
  const chat = useChat(threads.reload)

  const onCitationClick = useCallback(() => {
    if (window.matchMedia(OVERLAY_QUERY).matches) onClose()
  }, [onClose])

  const scopeLabel = (l: Level) =>
    l === 'all' ? 'All classes' : l === 'course' ? (courseCode ?? 'Course') : 'This item'
  const placeholder =
    level === 'all'
      ? 'Ask about all your classes…'
      : level === 'course'
        ? `Ask about ${courseCode}…`
        : 'Ask about this item…'

  const threadList = threads.data ?? []
  const currentMissing = chat.threadId !== null && !threadList.some((t) => t.id === chat.threadId)
  const status = sync.status
  const unavailable = !status || !status.agent_ready || Boolean(sync.statusError)

  return (
    <aside className="chat" hidden={!open} aria-label="Chat">
      <div className="chat-head">
        <div className="chat-title">
          <h2>Ask StudyHub</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close chat" title="Close chat">
            <CloseIcon />
          </button>
        </div>

        <div className="scope">
          <span id="scope-label">Scope</span>
          <div className="scope-pills" role="radiogroup" aria-labelledby="scope-label">
            {levels.map((l) => (
              <button
                key={l}
                type="button"
                role="radio"
                aria-checked={l === level}
                className={l === level ? 'on' : undefined}
                title={l === 'item' && resource ? resource.title : undefined}
                onClick={() => setPicked({ key: contextKey, level: l })}
              >
                {scopeLabel(l)}
              </button>
            ))}
          </div>
        </div>

        <div className="thread-row">
          <select
            aria-label="Conversation"
            value={chat.threadId ?? ''}
            disabled={chat.streaming}
            onChange={(e) => (e.target.value ? chat.openThread(Number(e.target.value)) : chat.newChat())}
          >
            <option value="">{chat.threadId === null ? 'New conversation' : 'Start a new conversation'}</option>
            {currentMissing && <option value={chat.threadId ?? ''}>Current conversation</option>}
            {threadList.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title || 'Untitled conversation'} · {formatAgo(t.updated_at)}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn small"
            onClick={chat.newChat}
            disabled={chat.threadId === null && !chat.messages.length}
          >
            New chat
          </button>
        </div>
      </div>

      <Messages
        messages={chat.messages}
        loading={chat.loadingThread}
        error={chat.threadError}
        streaming={chat.streaming}
        onCitationClick={onCitationClick}
      />

      {status && !status.agent_ready && (
        <p className="chat-note">
          Chat needs <code>ANTHROPIC_API_KEY</code> in <code>backend/.env</code>. Add it and restart the backend.
        </p>
      )}
      {sync.statusError && <p className="chat-note">Chat is unavailable while the backend can't be reached.</p>}

      <Composer
        placeholder={placeholder}
        unavailable={unavailable}
        streaming={chat.streaming}
        onSend={(text) => chat.send(text, scope)}
        onStop={chat.stop}
      />
    </aside>
  )
}
