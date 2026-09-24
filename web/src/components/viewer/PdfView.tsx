import type { ResourceDetail } from '../../api/types'
import { usePersistentState } from '../../lib/storage'
import { PageTexts } from './PageTexts'

type Layout = 'split' | 'pdf' | 'text'

const LAYOUTS: { id: Layout; label: string }[] = [
  { id: 'split', label: 'Side by side' },
  { id: 'pdf', label: 'PDF' },
  { id: 'text', label: 'Text' },
]

export function PdfView({ resource, page }: { resource: ResourceDetail & { file_url: string }; page: number | null }) {
  const [layout, setLayout] = usePersistentState<Layout>('studyhub.pdfLayout', 'split')
  const pages = resource.pages ?? []
  const hasText = pages.length > 0
  const shown: Layout = hasText ? layout : 'pdf'

  return (
    <div className="pdf">
      <div className="pdf-bar">
        {hasText && (
          <div className="segmented" role="radiogroup" aria-label="Layout">
            {LAYOUTS.map((l) => (
              <button
                key={l.id}
                type="button"
                role="radio"
                aria-checked={shown === l.id}
                className={shown === l.id ? 'on' : undefined}
                onClick={() => setLayout(l.id)}
              >
                {l.label}
              </button>
            ))}
          </div>
        )}
        {page !== null && (
          <span className="mono muted">
            p. {page}
            {resource.page_count ? ` of ${resource.page_count}` : ''}
          </span>
        )}
      </div>
      <div className={`pdf-split ${shown}`}>
        {shown !== 'text' && (
          // Remount on page change: some PDF viewers ignore fragment-only src updates.
          <iframe
            key={page ?? 0}
            className="pdf-frame"
            src={`${resource.file_url}#page=${page ?? 1}`}
            title={resource.title}
          />
        )}
        {shown !== 'pdf' && <PageTexts resourceId={resource.id} pages={pages} target={page} pane />}
      </div>
    </div>
  )
}
