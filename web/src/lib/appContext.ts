import { createContext, useContext, useEffect } from 'react'
import type { CourseSummary } from '../api/types'
import type { ApiState } from './useApi'
import type { SyncState } from './useSyncStatus'

interface AppContextValue {
  sync: SyncState
  courses: ApiState<CourseSummary[]>
}

export const AppContext = createContext<AppContextValue | null>(null)

export function useApp(): AppContextValue {
  const value = useContext(AppContext)
  if (!value) throw new Error('useApp must be used inside AppContext')
  return value
}

/** What the main area is showing, for the chat panel's scope picker. */
export interface ViewContext {
  courseId: number | null
  resource: { id: number; title: string; courseCode: string } | null
}

export const NO_CONTEXT: ViewContext = { courseId: null, resource: null }

/** Lets the view in the main area tell the shell what it shows. */
export const ReportContext = createContext<(ctx: ViewContext) => void>(() => {})

/** Report the current view context; pass null while it is still loading to keep the previous one. */
export function useReportContext(ctx: ViewContext | null) {
  const report = useContext(ReportContext)
  const known = ctx !== null
  const courseId = ctx?.courseId ?? null
  const resourceId = ctx?.resource?.id
  const title = ctx?.resource?.title ?? ''
  const courseCode = ctx?.resource?.courseCode ?? ''
  useEffect(() => {
    if (!known) return
    report({ courseId, resource: resourceId === undefined ? null : { id: resourceId, title, courseCode } })
  }, [report, known, courseId, resourceId, title, courseCode])
}
