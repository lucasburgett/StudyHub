import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { ApiError, getJson, isAbort, type Endpoint } from '../api/client'

/** Bumped after a sync finishes (or the backend comes back) so every mounted view refetches. */
export const DataVersion = createContext(0)

export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err
  return new ApiError(err instanceof Error ? err.message : String(err), 0, false)
}

export interface ApiState<T> {
  data: T | undefined
  error: ApiError | undefined
  loading: boolean
  reload: () => void
}

interface Loaded<T> {
  /** The request (path + refresh counters) this result answers. */
  key: string | null
  path: string | null
  data?: T
  error?: ApiError
}

/**
 * GET an endpoint and keep the result. Passing null skips the request.
 * Data from a previous load of the same path stays visible while refetching.
 */
export function useApi<T>(ep: Endpoint<T> | null): ApiState<T> {
  const version = useContext(DataVersion)
  const [nonce, setNonce] = useState(0)
  const path = ep?.path ?? null
  const key = path === null ? null : `${path}|${version}|${nonce}`
  const [state, setState] = useState<Loaded<T>>({ key: null, path: null })

  useEffect(() => {
    if (path === null || key === null) return
    const ctrl = new AbortController()
    getJson<T>({ path }, ctrl.signal).then(
      (data) => setState({ key, path, data }),
      (err: unknown) => {
        if (isAbort(err)) return
        setState((s) => ({ key, path, data: s.path === path ? s.data : undefined, error: toApiError(err) }))
      },
    )
    return () => ctrl.abort()
  }, [path, key])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  const samePath = state.path === path
  return {
    data: samePath ? state.data : undefined,
    error: samePath ? state.error : undefined,
    loading: key !== null && state.key !== key,
    reload,
  }
}
