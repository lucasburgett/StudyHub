import type { ResourceDetail } from '../../api/types'
import { formatDay } from '../../lib/format'
import { KIND_NAMES, resourceSize, SOURCE_NAMES } from '../../lib/labels'
import { Markdown } from '../Markdown'
import { EmptyState, ExternalLink } from '../ui'
import { PageTexts } from './PageTexts'
import { PdfView } from './PdfView'
import { TranscriptView } from './TranscriptView'
import { ViewerHeader } from './ViewerHeader'

interface ResourceViewerProps {
  resource: ResourceDetail
  page: number | null
  seconds: number | null
  backTo: string
}

function hasFile(r: ResourceDetail): r is ResourceDetail & { file_url: string } {
  return Boolean(r.file_url)
}

function Body({ resource, page, seconds }: Omit<ResourceViewerProps, 'backTo'>) {
  if (hasFile(resource)) return <PdfView resource={resource} page={page} />
  if (resource.kind === 'transcript' || resource.segments?.length) {
    return <TranscriptView resource={resource} seconds={seconds} />
  }
  return (
    <>
      {resource.summary && (
        <section className="summary-box" aria-label="Summary">
          <h3 className="eyebrow">Summary</h3>
          <Markdown text={resource.summary} />
        </section>
      )}
      {resource.markdown ? (
        <Markdown text={resource.markdown} className="doc" />
      ) : resource.pages?.length ? (
        <PageTexts resourceId={resource.id} pages={resource.pages} target={page} />
      ) : (
        <EmptyState title="Nothing to show yet">
          StudyHub has no text for this item.{resource.url ? ' Open the original instead.' : ''}
        </EmptyState>
      )}
    </>
  )
}

export function ResourceViewer({ resource, page, seconds, backTo }: ResourceViewerProps) {
  const meta = [
    KIND_NAMES[resource.kind],
    resource.occurred_at ? formatDay(resource.occurred_at) : '',
    resourceSize(resource),
  ].filter(Boolean)

  return (
    <article className="viewer">
      <ViewerHeader
        source={resource.source}
        meta={meta}
        title={resource.title}
        backTo={backTo}
        links={
          resource.url || resource.file_url ? (
            <>
              {resource.url && <ExternalLink href={resource.url}>Open in {SOURCE_NAMES[resource.source]}</ExternalLink>}
              {resource.file_url && <ExternalLink href={resource.file_url}>Open PDF</ExternalLink>}
            </>
          ) : null
        }
      />
      <Body resource={resource} page={page} seconds={seconds} />
    </article>
  )
}
