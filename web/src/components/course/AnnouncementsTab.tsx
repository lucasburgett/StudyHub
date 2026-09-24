import { useState } from 'react'
import { endpoints } from '../../api/client'
import type { ResourceSummary } from '../../api/types'
import { formatDateTime, parseDate } from '../../lib/format'
import { href } from '../../lib/router'
import { useApi } from '../../lib/useApi'
import { Markdown } from '../Markdown'
import { EmptyState, ErrorState, ExternalLink, Link, Loading } from '../ui'

const PAGE_SIZE = 10

function newestFirst(a: ResourceSummary, b: ResourceSummary): number {
  const ta = a.occurred_at ? parseDate(a.occurred_at).getTime() : 0
  const tb = b.occurred_at ? parseDate(b.occurred_at).getTime() : 0
  return tb - ta
}

/** Summaries carry no body, so each card loads its announcement's markdown. */
function AnnouncementCard({ item }: { item: ResourceSummary }) {
  const detail = useApi(endpoints.resource(item.id))
  return (
    <article className="card announcement">
      <header>
        <h3>
          <Link to={href.resource(item.id)}>{item.title}</Link>
        </h3>
        {item.occurred_at && <span className="date">{formatDateTime(item.occurred_at)}</span>}
      </header>
      {detail.data ? (
        detail.data.markdown ? (
          <Markdown text={detail.data.markdown} />
        ) : (
          <p className="muted">No text in this announcement.</p>
        )
      ) : detail.error ? (
        <p className="muted">Couldn't load this announcement. {detail.error.message}</p>
      ) : (
        <Loading />
      )}
      {item.url && (
        <footer>
          <ExternalLink href={item.url}>Open in Canvas</ExternalLink>
        </footer>
      )}
    </article>
  )
}

export function AnnouncementsTab({ courseId }: { courseId: number }) {
  const list = useApi(endpoints.courseResources(courseId, ['announcement']))
  const [shown, setShown] = useState(PAGE_SIZE)

  if (list.error && !list.data) return <ErrorState error={list.error} onRetry={list.reload} what="Announcements" />
  if (!list.data) return <Loading />
  if (list.data.length === 0) {
    return <EmptyState title="No announcements yet">Canvas announcements appear here after a Canvas sync.</EmptyState>
  }

  const items = [...list.data].sort(newestFirst)
  return (
    <div className="stack">
      {items.slice(0, shown).map((item) => (
        <AnnouncementCard key={item.id} item={item} />
      ))}
      {items.length > shown && (
        <button type="button" className="btn more" onClick={() => setShown((n) => n + PAGE_SIZE)}>
          Show older announcements ({items.length - shown} more)
        </button>
      )}
    </div>
  )
}
