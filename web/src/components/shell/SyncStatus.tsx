import type { Source, SourceStatus } from '../../api/types'
import { useApp } from '../../lib/appContext'
import { formatAgo, formatDateTime, plural } from '../../lib/format'
import { SOURCE_NAMES, SOURCES } from '../../lib/labels'
import { SyncIcon } from '../icons'

type DotState = 'off' | 'idle' | 'running' | 'ok' | 'warn' | 'error'

function describe(source: Source, s: SourceStatus | undefined): { state: DotState; tip: string } {
  const name = SOURCE_NAMES[source]
  if (!s || !s.configured) return { state: 'off', tip: `${name}: not configured` }
  if (s.running) return { state: 'running', tip: `${name}: syncing…` }
  const run = s.last_run
  if (!run) return { state: 'idle', tip: `${name}: not synced yet` }
  const warnings = run.warnings ?? []
  const warningText = warnings.length ? `\n${plural(warnings.length, 'warning')}:\n• ${warnings.join('\n• ')}` : ''
  if (run.status === 'error') {
    const error = run.error ? `\n${run.error}` : ''
    return { state: 'error', tip: `${name}: failed ${formatAgo(run.started_at)}${error}${warningText}` }
  }
  const when = formatDateTime(run.finished_at ?? run.started_at)
  return {
    state: warnings.length ? 'warn' : 'ok',
    tip: `${name}: synced ${when}, ${plural(run.items_changed, 'item')} changed${warningText}`,
  }
}

function lastFinished(sources: SourceStatus[]): string | null {
  const times = sources.map((s) => s.last_run?.finished_at).filter((t): t is string => Boolean(t))
  return times.length ? times.reduce((a, b) => (Date.parse(a) > Date.parse(b) ? a : b)) : null
}

export function SyncStatus() {
  const { sync } = useApp()
  const { status, statusError, syncing, syncError } = sync
  const sources = status?.sources ?? []
  const anyConfigured = sources.some((s) => s.configured)
  const anyFailed = sources.some((s) => s.configured && !s.running && s.last_run?.status === 'error')
  const finished = lastFinished(sources)

  let label: string
  if (statusError) label = 'Offline'
  else if (!status) label = 'Checking…'
  else if (syncing) label = 'Syncing…'
  else if (!anyConfigured) label = 'No sources connected'
  else if (anyFailed) label = 'Sync problem'
  else label = finished ? `Synced ${formatAgo(finished)}` : 'Not synced yet'

  const disabledReason = statusError
    ? "The backend isn't reachable"
    : status && !anyConfigured
      ? 'No sources configured in backend/.env'
      : undefined

  return (
    <div className="sync">
      <ul className="sync-dots" aria-label="Sources">
        {SOURCES.map((source) => {
          const { state, tip } = describe(
            source,
            sources.find((s) => s.source === source),
          )
          return (
            <li key={source}>
              <span className={`dot ${source} sdot ${state}`} tabIndex={0} role="img" aria-label={tip} data-tip={tip} />
            </li>
          )
        })}
      </ul>
      <span
        className={`sync-label${anyFailed || syncError ? ' bad' : ''}`}
        role="status"
        title={syncError ?? undefined}
      >
        {syncError ? 'Sync failed to start' : label}
      </span>
      <button
        type="button"
        className="btn small"
        onClick={() => void sync.syncNow()}
        disabled={syncing || Boolean(disabledReason) || !status}
        title={disabledReason ?? syncError ?? 'Sync every configured source now'}
        aria-label={syncing ? 'Syncing' : 'Sync now'}
      >
        <SyncIcon />
        <span className="btn-label">{syncing ? 'Syncing' : 'Sync now'}</span>
      </button>
    </div>
  )
}
