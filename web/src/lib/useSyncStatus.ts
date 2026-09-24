import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, endpoints, getJson, isAbort, startSync } from '../api/client'
import type { Source, Status } from '../api/types'
import { toApiError } from './useApi'

const POLL_RUNNING_MS = 2000
const POLL_OFFLINE_MS = 5000
const POLL_IDLE_MS = 30000

export interface SyncState {
  status: Status | undefined
  statusError: ApiError | undefined
  /** Increments when fresh data may be available (a sync finished or the backend came back). */
  dataVersion: number
  syncing: boolean
  syncError: string | null
  syncNow: (source?: Source) => Promise<void>
  refresh: () => void
}

/** Polls GET /api/status: every 2 s while a sync runs, 5 s while the backend is unreachable, else 30 s. */
export function useSyncStatus(): SyncState {
  const [status, setStatus] = useState<Status>()
  const [statusError, setStatusError] = useState<ApiError>()
  const [dataVersion, setDataVersion] = useState(0)
  const [pollKey, setPollKey] = useState(0)
  const [syncError, setSyncError] = useState<string | null>(null)
  const [requesting, setRequesting] = useState(false)
  // True from the moment a sync is started or seen running until it settles; then views refetch.
  const awaitingSync = useRef(false)
  const hadError = useRef(false)

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    let ctrl: AbortController | undefined

    const poll = async () => {
      ctrl = new AbortController()
      let next = POLL_IDLE_MS
      try {
        const s = await getJson(endpoints.status(), ctrl.signal)
        if (cancelled) return
        const running = s.sources.some((src) => src.running)
        const settled = awaitingSync.current && !running
        if (settled || hadError.current) setDataVersion((v) => v + 1)
        awaitingSync.current = running
        hadError.current = false
        setStatus(s)
        setStatusError(undefined)
        next = running ? POLL_RUNNING_MS : POLL_IDLE_MS
      } catch (err) {
        if (cancelled || isAbort(err)) return
        hadError.current = true
        setStatusError(toApiError(err))
        next = POLL_OFFLINE_MS
      }
      timer = window.setTimeout(poll, next)
    }

    void poll()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      ctrl?.abort()
    }
  }, [pollKey])

  const refresh = useCallback(() => setPollKey((k) => k + 1), [])

  const syncNow = useCallback(async (source?: Source) => {
    setRequesting(true)
    setSyncError(null)
    try {
      const { started } = await startSync(source)
      if (started.length > 0) awaitingSync.current = true
      else if (!source) setSyncError('No sources are connected yet. Add them in Settings.')
    } catch (err) {
      setSyncError(toApiError(err).message)
    } finally {
      setRequesting(false)
      refresh()
    }
  }, [refresh])

  const syncing = requesting || (status?.sources.some((s) => s.running) ?? false)
  return { status, statusError, dataVersion, syncing, syncError, syncNow, refresh }
}
