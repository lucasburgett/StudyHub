// A tiny hash router. Routes:
//   #/                          all classes
//   #/course/3/timeline         course tab
//   #/resource/42?page=27       viewer at a PDF page
//   #/resource/42?t=2472        viewer at a transcript time (seconds)
//   #/assignment/7?q=2          assignment detail, optionally highlighting feedback question 2
//   #/settings                  connect sources and keys
//   #/assignments               every class's assignments by due date
import { useMemo, useSyncExternalStore } from 'react'
import type { Citation, Kind } from '../api/types'

export const TABS = [
  { id: 'timeline', label: 'Timeline' },
  { id: 'assignments', label: 'Assignments' },
  { id: 'files', label: 'Files' },
  { id: 'notes', label: 'Notes' },
  { id: 'recordings', label: 'Recordings' },
  { id: 'announcements', label: 'Announcements' },
] as const

export type Tab = (typeof TABS)[number]['id']

export type Route =
  | { name: 'home' }
  | { name: 'course'; courseId: number; tab: Tab }
  | { name: 'resource'; resourceId: number; page: number | null; seconds: number | null }
  | { name: 'assignment'; assignmentId: number; question: number | null }
  | { name: 'settings' }
  | { name: 'assignments' }

function isTab(value: string | undefined): value is Tab {
  return TABS.some((t) => t.id === value)
}

function toId(value: string | null | undefined): number | null {
  if (!value) return null
  const n = Number(value)
  return Number.isSafeInteger(n) && n >= 0 ? n : null
}

function toSeconds(value: string | null): number | null {
  if (value === null || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) && n >= 0 ? n : null
}

function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, '')
  const q = raw.indexOf('?')
  const path = q === -1 ? raw : raw.slice(0, q)
  const params = new URLSearchParams(q === -1 ? '' : raw.slice(q + 1))
  const [section, idPart, tabPart] = path.split('/').filter(Boolean)
  if (section === 'settings') return { name: 'settings' }
  if (section === 'assignments') return { name: 'assignments' }
  const id = toId(idPart)
  if (id !== null) {
    if (section === 'course') return { name: 'course', courseId: id, tab: isTab(tabPart) ? tabPart : 'timeline' }
    if (section === 'resource') {
      return { name: 'resource', resourceId: id, page: toId(params.get('page')), seconds: toSeconds(params.get('t')) }
    }
    if (section === 'assignment') return { name: 'assignment', assignmentId: id, question: toId(params.get('q')) }
  }
  return { name: 'home' }
}

export const href = {
  home: () => '#/',
  settings: () => '#/settings',
  assignments: () => '#/assignments',
  course: (courseId: number, tab: Tab = 'timeline') => `#/course/${courseId}/${tab}`,
  resource: (resourceId: number, at: { page?: number | null; seconds?: number | null } = {}) => {
    if (at.page != null) return `#/resource/${resourceId}?page=${at.page}`
    if (at.seconds != null) return `#/resource/${resourceId}?t=${Math.floor(at.seconds)}`
    return `#/resource/${resourceId}`
  },
  assignment: (assignmentId: number, question?: number | null) =>
    question != null ? `#/assignment/${assignmentId}?q=${question}` : `#/assignment/${assignmentId}`,
}

/** Where a citation chip leads: the resource at its page/time, or the assignment (and question for `a7/q2`). */
export function citationHref(c: Citation): string | null {
  if (c.resource_id !== null) return href.resource(c.resource_id, { page: c.page, seconds: c.seconds })
  if (c.assignment_id !== null) {
    const q = /\/q(\d+)$/.exec(c.locator)
    return href.assignment(c.assignment_id, q ? Number(q[1]) : null)
  }
  return null
}

/** The course tab a resource of this kind is listed under. */
export function tabForKind(kind: Kind): Tab {
  switch (kind) {
    case 'notes':
      return 'notes'
    case 'transcript':
      return 'recordings'
    case 'announcement':
      return 'announcements'
    case 'spec':
    case 'submission':
      return 'assignments'
    default:
      return 'files'
  }
}

// ---------------------------------------------------------------------------
// History integration. Entries pushed by the app are marked so the viewer's
// back button knows whether history.back() stays inside the app.

const listeners = new Set<() => void>()
const notify = () => listeners.forEach((l) => l())
window.addEventListener('popstate', notify)
window.addEventListener('hashchange', notify)

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

const getHash = () => window.location.hash

export function navigate(to: string, options: { replace?: boolean } = {}) {
  if (options.replace) {
    window.history.replaceState(window.history.state, '', to)
  } else {
    if (to === window.location.hash) return
    window.history.pushState({ studyhub: true }, '', to)
  }
  notify()
}

/** Go back if the previous entry belongs to the app, otherwise go to `fallback`. */
export function goBack(fallback: string) {
  const state: unknown = window.history.state
  if (state && typeof state === 'object' && (state as { studyhub?: unknown }).studyhub === true) {
    window.history.back()
  } else {
    navigate(fallback)
  }
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, getHash)
  return useMemo(() => parseHash(hash), [hash])
}

// The last tab visited per course: the sidebar returns to it, and a viewer opened from the Timeline
// keeps Timeline highlighted.
const lastTabs = new Map<number, Tab>()

export function rememberTab(courseId: number, tab: Tab) {
  lastTabs.set(courseId, tab)
}

export function lastTab(courseId: number): Tab | undefined {
  return lastTabs.get(courseId)
}
