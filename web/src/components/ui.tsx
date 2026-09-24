import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from 'react'
import type { ApiError } from '../api/client'
import type { AssignmentStatus, Source } from '../api/types'
import { SOURCE_NAMES, STATUS_INFO } from '../lib/labels'
import { navigate } from '../lib/router'
import { ExternalIcon } from './icons'

type AnchorProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'>

/** In-app link: a real href (so middle-click works) that navigates through the hash router. */
export function Link({ to, replace, onClick, ...rest }: AnchorProps & { to: string; replace?: boolean }) {
  const handleClick = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e)
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    navigate(to, { replace })
  }
  return <a href={to} onClick={handleClick} {...rest} />
}

export function ExternalLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" className={`ext ${className ?? ''}`}>
      {children}
      <ExternalIcon />
    </a>
  )
}

export function SourceDot({ source, label = false }: { source: Source; label?: boolean }) {
  return (
    <span
      className={`dot ${source}`}
      role={label ? 'img' : undefined}
      aria-label={label ? SOURCE_NAMES[source] : undefined}
      aria-hidden={label ? undefined : true}
    />
  )
}

export function StatusPill({ status }: { status: AssignmentStatus }) {
  const info = STATUS_INFO[status]
  return <span className={`pill ${info.tone}`}>{info.label}</span>
}

export function Spinner({ label }: { label?: string }) {
  return <span className="spinner" role={label ? 'status' : undefined} aria-label={label} />
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <Spinner />
      <span>{label}</span>
    </div>
  )
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  )
}

export function ErrorState({ error, onRetry, what }: { error: ApiError; onRetry?: () => void; what?: string }) {
  const title = error.offline
    ? "Can't reach the StudyHub backend"
    : error.status === 404
      ? `${what ?? 'This item'} wasn't found`
      : `Couldn't load ${what?.toLowerCase() ?? 'this'}`
  return (
    <div className="empty error" role="alert">
      <h3>{title}</h3>
      <p>
        {error.offline
          ? 'Start the backend (it listens on 127.0.0.1:8000). This page reconnects on its own.'
          : error.message}
      </p>
      {onRetry && !error.offline && error.status !== 404 && (
        <button type="button" className="btn" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}
