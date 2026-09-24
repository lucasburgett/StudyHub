import { lazy, Suspense } from 'react'
import type { Citations } from '../api/types'

// The renderer and its plugins (KaTeX alone is several hundred kB) load the first time any
// Markdown is shown, so the timeline and home pages start without them.
const MarkdownRenderer = lazy(() => import('./MarkdownRenderer'))

export interface MarkdownProps {
  text: string
  /** When given, cite:LOCATOR links render as chips that open the cited item. */
  citations?: Citations
  onCitationClick?: () => void
  className?: string
}

export function Markdown(props: MarkdownProps) {
  // Until the renderer arrives, show the plain text (without citation markers) in the same box.
  const fallback = (
    <div className={props.className ? `md md-plain ${props.className}` : 'md md-plain'}>
      {props.text.replace(/\[\[[^\]]*\]\]/g, '')}
    </div>
  )
  return (
    <Suspense fallback={fallback}>
      <MarkdownRenderer {...props} />
    </Suspense>
  )
}
