import { endpoints } from '../../api/client'
import type { Kind } from '../../api/types'
import { formatDay } from '../../lib/format'
import { KIND_NAMES, resourceSize } from '../../lib/labels'
import { href, navigate } from '../../lib/router'
import { useApi } from '../../lib/useApi'
import { EmptyState, ErrorState, Link, Loading, SourceDot } from '../ui'

type ListTab = 'files' | 'notes' | 'recordings'

const LISTS: Record<ListTab, { kinds: Kind[]; sizeHeader: string; empty: string; emptyHint: string }> = {
  files: {
    kinds: ['slides', 'file', 'page'],
    sizeHeader: 'Pages',
    empty: 'No files yet',
    emptyHint: 'Slides, handouts, and Canvas pages appear here after a Canvas sync.',
  },
  notes: {
    kinds: ['notes'],
    sizeHeader: 'Pages',
    empty: 'No notes yet',
    emptyHint: 'GoodNotes notebooks appear here after a GoodNotes sync.',
  },
  recordings: {
    kinds: ['transcript'],
    sizeHeader: 'Length',
    empty: 'No recordings yet',
    emptyHint: 'Granola lecture recordings appear here after a Granola sync.',
  },
}

export function ResourceListTab({ courseId, tab }: { courseId: number; tab: ListTab }) {
  const config = LISTS[tab]
  const resources = useApi(endpoints.courseResources(courseId, config.kinds))
  const showKind = config.kinds.length > 1

  if (resources.error && !resources.data) {
    return <ErrorState error={resources.error} onRetry={resources.reload} what="This list" />
  }
  if (!resources.data) return <Loading />
  if (resources.data.length === 0) return <EmptyState title={config.empty}>{config.emptyHint}</EmptyState>

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">Title</th>
            {showKind && <th scope="col">Type</th>}
            <th scope="col">Date</th>
            <th scope="col" className="num">
              {config.sizeHeader}
            </th>
          </tr>
        </thead>
        <tbody>
          {resources.data.map((r) => (
            <tr key={r.id} className="clickable" onClick={() => navigate(href.resource(r.id))}>
              <td>
                <Link to={href.resource(r.id)} className="row-title" onClick={(e) => e.stopPropagation()}>
                  <SourceDot source={r.source} />
                  {r.title}
                </Link>
              </td>
              {showKind && <td>{KIND_NAMES[r.kind]}</td>}
              <td className="date">{r.occurred_at ? formatDay(r.occurred_at) : '—'}</td>
              <td className="num">{resourceSize(r) || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
