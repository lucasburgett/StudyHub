import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { SendIcon, StopIcon } from '../icons'

const QUICK_ACTIONS = [
  "What's due this week?",
  'Catch me up on the last lecture',
  'Quiz me on this week',
  'Explain my deductions',
]

interface ComposerProps {
  placeholder: string
  /** True when chat can't be used at all (no way to reach Claude, backend down). */
  unavailable: boolean
  streaming: boolean
  onSend: (text: string) => void
  onStop: () => void
}

export function Composer({ placeholder, unavailable, streaming, onSend, onStop }: ComposerProps) {
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const refocus = useRef(false)
  const disabled = unavailable || streaming

  // The textarea is disabled while an answer streams, which drops focus; give it back afterwards.
  useEffect(() => {
    if (!streaming && refocus.current) {
      refocus.current = false
      inputRef.current?.focus()
    }
  }, [streaming])

  const send = (value: string) => {
    if (!value.trim() || disabled) return
    refocus.current = document.activeElement === inputRef.current
    onSend(value)
    setText('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      send(text)
    }
  }

  return (
    <form
      className="compose"
      onSubmit={(e) => {
        e.preventDefault()
        send(text)
      }}
    >
      <div className="compose-box">
        <textarea
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          aria-label="Message"
          rows={2}
          disabled={disabled}
        />
        {streaming ? (
          <button type="button" className="send stop" onClick={onStop} aria-label="Stop answering" title="Stop">
            <StopIcon />
          </button>
        ) : (
          <button type="submit" className="send" disabled={disabled || !text.trim()} aria-label="Send" title="Send">
            <SendIcon />
          </button>
        )}
      </div>
      <div className="quick" role="group" aria-label="Quick questions">
        {QUICK_ACTIONS.map((q) => (
          <button key={q} type="button" className="quick-chip" disabled={disabled} onClick={() => send(q)}>
            {q}
          </button>
        ))}
      </div>
    </form>
  )
}
