import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { endpoints, getJson, isAbort } from '../../api/client'
import type { SearchHit } from '../../api/types'
import { href, navigate } from '../../lib/router'
import { toApiError } from '../../lib/useApi'
import { SearchIcon } from '../icons'
import { SourceDot, Spinner } from '../ui'

const DEBOUNCE_MS = 250
const MIN_CHARS = 2

type Results = { query: string; hits: SearchHit[] } | { query: string; error: string }

export function SearchBox() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Results | null>(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const rootRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listId = useId()

  const term = query.trim()
  const ready = term.length >= MIN_CHARS

  useEffect(() => {
    if (!ready) return
    const ctrl = new AbortController()
    const timer = window.setTimeout(() => {
      getJson(endpoints.search(term), ctrl.signal)
        .then((hits) => setResults({ query: term, hits }))
        .catch((err: unknown) => {
          if (!isAbort(err)) setResults({ query: term, error: toApiError(err).message })
        })
    }, DEBOUNCE_MS)
    return () => {
      window.clearTimeout(timer)
      ctrl.abort()
    }
  }, [term, ready])

  // Close when clicking elsewhere.
  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [])

  // "/" focuses search from anywhere outside a text field.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))) return
      e.preventDefault()
      inputRef.current?.focus()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const hits = ready && results && 'hits' in results ? results.hits : []
  const error = ready && results && 'error' in results ? results.error : null
  const stale = results?.query !== term
  const showPanel = open && ready

  const openHit = (hit: SearchHit) => {
    setOpen(false)
    inputRef.current?.blur()
    navigate(href.resource(hit.resource_id, { page: hit.page, seconds: hit.seconds }))
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' && hits.length) {
      e.preventDefault()
      setOpen(true)
      setActive((i) => (i + 1) % hits.length)
    } else if (e.key === 'ArrowUp' && hits.length) {
      e.preventDefault()
      setActive((i) => (i <= 0 ? hits.length - 1 : i - 1))
    } else if (e.key === 'Enter' && hits.length) {
      e.preventDefault()
      openHit(hits[Math.max(active, 0)])
    } else if (e.key === 'Escape') {
      if (open) setOpen(false)
      else setQuery('')
    }
  }

  return (
    <div className="search" ref={rootRef}>
      <span className="search-icon" aria-hidden="true">
        {ready && stale ? <Spinner /> : <SearchIcon />}
      </span>
      <input
        ref={inputRef}
        type="search"
        value={query}
        placeholder="Search all classes…"
        aria-label="Search all classes"
        role="combobox"
        aria-expanded={showPanel}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showPanel && active >= 0 ? `${listId}-${active}` : undefined}
        onChange={(e) => {
          setQuery(e.target.value)
          setActive(-1)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      <kbd className="search-kbd" aria-hidden="true">
        /
      </kbd>
      {showPanel && (
        <div className="search-pop">
          {error && <p className="search-msg error">{error}</p>}
          {!error && hits.length === 0 && (
            <p className="search-msg">{stale ? 'Searching…' : `No matches for “${term}”.`}</p>
          )}
          {hits.length > 0 && (
            <ul id={listId} role="listbox" aria-label="Search results">
              {hits.map((hit, i) => (
                <li
                  key={hit.chunk_id}
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={i === active}
                  className={i === active ? 'hit on' : 'hit'}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => openHit(hit)}
                >
                  <span className="hit-meta">
                    <SourceDot source={hit.source} />
                    <b>{hit.course_code}</b>
                    <span>{hit.label}</span>
                  </span>
                  <span className="hit-title">{hit.title}</span>
                  {/* The server HTML-escapes snippets and only adds <mark> tags. */}
                  <span className="hit-snippet" dangerouslySetInnerHTML={{ __html: hit.snippet }} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
