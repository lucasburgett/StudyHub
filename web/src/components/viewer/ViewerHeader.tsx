import type { ReactNode } from 'react'
import type { Source } from '../../api/types'
import { SOURCE_NAMES } from '../../lib/labels'
import { goBack } from '../../lib/router'
import { BackIcon } from '../icons'
import { SourceDot } from '../ui'

interface ViewerHeaderProps {
  source: Source
  /** Mono meta line after the source name, e.g. "Slides · Tue Sep 29 · 72 pp". */
  meta: string[]
  title: string
  /** Where Back goes when there is no in-app history to return to. */
  backTo: string
  links?: ReactNode
}

export function ViewerHeader({ source, meta, title, backTo, links }: ViewerHeaderProps) {
  return (
    <header className="viewer-h">
      <button type="button" className="btn ghost small back" onClick={() => goBack(backTo)}>
        <BackIcon />
        Back
      </button>
      <p className="eyebrow viewer-meta">
        <SourceDot source={source} />
        {[SOURCE_NAMES[source], ...meta].join(' · ')}
      </p>
      <h2>{title}</h2>
      {links && <div className="viewer-links">{links}</div>}
    </header>
  )
}
