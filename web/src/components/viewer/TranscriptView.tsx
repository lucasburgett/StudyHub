import { useEffect, useRef } from 'react'
import type { ResourceDetail, Segment } from '../../api/types'
import { href } from '../../lib/router'
import { Markdown } from '../Markdown'
import { EmptyState, Link } from '../ui'

/** Index of the segment with the greatest start time ≤ target, or -1. */
function segmentAt(segments: Segment[], target: number): number {
  let best = -1
  segments.forEach((s, i) => {
    if (s.seconds <= target && (best === -1 || s.seconds >= segments[best].seconds)) best = i
  })
  return best
}

export function TranscriptView({ resource, seconds }: { resource: ResourceDetail; seconds: number | null }) {
  const segments = resource.segments ?? []
  const active = seconds === null ? -1 : segmentAt(segments, seconds)
  const listRef = useRef<HTMLOListElement>(null)

  useEffect(() => {
    if (active < 0) return
    listRef.current?.querySelector<HTMLElement>('[aria-current="true"]')?.scrollIntoView({ block: 'center' })
  }, [active, resource.id])

  return (
    <div className="transcript">
      {resource.summary && (
        <section className="summary-box" aria-label="Summary">
          <h3 className="eyebrow">Summary</h3>
          <Markdown text={resource.summary} />
        </section>
      )}
      {segments.length > 0 ? (
        <ol className="segments" ref={listRef} aria-label="Transcript">
          {segments.map((s, i) => (
            <li
              key={`${s.seconds}-${i}`}
              className={i === active ? 'seg on' : 'seg'}
              aria-current={i === active || undefined}
            >
              <Link to={href.resource(resource.id, { seconds: s.seconds })} replace className="seg-time">
                {s.label}
              </Link>
              <p>{s.text}</p>
            </li>
          ))}
        </ol>
      ) : resource.markdown ? (
        <Markdown text={resource.markdown} />
      ) : (
        !resource.summary && <EmptyState title="No transcript yet">This recording has no transcript text.</EmptyState>
      )}
    </div>
  )
}
