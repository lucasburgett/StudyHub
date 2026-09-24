import { useEffect, useRef } from 'react'
import type { PageText } from '../../api/types'
import { href } from '../../lib/router'
import { Link } from '../ui'

interface PageTextsProps {
  resourceId: number
  pages: PageText[]
  target: number | null
  /** True when this list is its own scroll pane (next to the PDF) rather than part of the page. */
  pane?: boolean
}

/** Extracted text per page. The target page is highlighted and scrolled into view. */
export function PageTexts({ resourceId, pages, target, pane = false }: PageTextsProps) {
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const root = rootRef.current
    if (!root || target === null) return
    const el = root.querySelector<HTMLElement>(`[data-page="${target}"]`)
    if (!el) return
    if (pane) root.scrollTo({ top: el.offsetTop - 8 })
    else el.scrollIntoView({ block: 'start' })
  }, [target, pane, resourceId])

  return (
    <div className={pane ? 'page-texts pane' : 'page-texts'} ref={rootRef} aria-label="Page text">
      {pages.map((p) => (
        <section key={p.page} data-page={p.page} className={p.page === target ? 'page-text on' : 'page-text'}>
          <h3>
            <Link to={href.resource(resourceId, { page: p.page })} replace className="page-no">
              p. {p.page}
            </Link>
          </h3>
          {p.text.trim() ? <p>{p.text}</p> : <p className="muted">No text on this page.</p>}
        </section>
      ))}
    </div>
  )
}
