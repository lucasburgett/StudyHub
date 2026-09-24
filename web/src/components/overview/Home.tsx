import { useContext, useEffect, useState } from 'react'
import { ApiError, endpoints, getJson, isAbort } from '../../api/client'
import type { AssignmentSummary, CourseSummary } from '../../api/types'
import { useApp } from '../../lib/appContext'
import { formatDateTime, formatDue, parseDate, plural } from '../../lib/format'
import { href, lastTab } from '../../lib/router'
import { DataVersion, toApiError } from '../../lib/useApi'
import { useNow } from '../../lib/useNow'
import { EmptyState, ErrorState, Link, Loading, SourceDot, StatusPill } from '../ui'

const UPCOMING_LIMIT = 12

/** Every course's assignments, loaded in parallel. Courses that fail to load are skipped. */
function useAllAssignments(courseIds: number[]) {
  const version = useContext(DataVersion)
  const key = courseIds.join(',')
  const [state, setState] = useState<{ key: string; items?: AssignmentSummary[]; error?: ApiError }>({ key: '' })

  useEffect(() => {
    const ctrl = new AbortController()
    const ids = key ? key.split(',').map(Number) : []
    Promise.allSettled(ids.map((id) => getJson(endpoints.courseAssignments(id), ctrl.signal))).then((results) => {
      if (ctrl.signal.aborted) return
      const loaded = results.flatMap((r) => (r.status === 'fulfilled' ? r.value : []))
      const failed = results.find((r): r is PromiseRejectedResult => r.status === 'rejected')
      if (failed && results.every((r) => r.status === 'rejected')) {
        if (!isAbort(failed.reason)) setState({ key, error: toApiError(failed.reason) })
      } else {
        setState({ key, items: loaded })
      }
    })
    return () => ctrl.abort()
  }, [key, version])

  return state.key === key ? state : { key, items: undefined, error: undefined }
}

function CourseCard({ course }: { course: CourseSummary }) {
  const { counts, next_due: next } = course
  return (
    <li>
      <Link to={href.course(course.id, lastTab(course.id))} className="course-card">
        <span className="course-card-code">{course.code}</span>
        {course.title && <span className="course-card-title">{course.title}</span>}
        <span className="course-card-next">
          {next ? (
            <>
              <b>{next.title}</b>
              <span className="date">{formatDue(next.due_at)}</span>
            </>
          ) : (
            <span className="muted">Nothing due</span>
          )}
        </span>
        <span className="course-card-counts">
          {[
            plural(counts.lectures, 'lecture'),
            plural(counts.resources, 'item'),
            plural(counts.assignments, 'assignment'),
          ].join(' · ')}
        </span>
      </Link>
    </li>
  )
}

function Upcoming({ courses }: { courses: CourseSummary[] }) {
  const all = useAllAssignments(courses.map((c) => c.id))
  const [expanded, setExpanded] = useState(false)
  const now = useNow()
  const codes = new Map(courses.map((c) => [c.id, c.code]))

  if (all.error) return <ErrorState error={all.error} what="Upcoming assignments" />
  if (!all.items) return <Loading />

  const upcoming = all.items
    .filter((a) => a.due_at && parseDate(a.due_at).getTime() >= now && a.status !== 'graded')
    .sort((a, b) => parseDate(a.due_at as string).getTime() - parseDate(b.due_at as string).getTime())
  if (upcoming.length === 0) return <p className="muted">Nothing due. Enjoy it.</p>

  const shown = expanded ? upcoming : upcoming.slice(0, UPCOMING_LIMIT)
  return (
    <>
      <ul className="upcoming">
        {shown.map((a) => (
          <li key={a.id}>
            <Link to={href.assignment(a.id)} className="up-row">
              <span className="date">{formatDateTime(a.due_at as string)}</span>
              <span className="up-code">{codes.get(a.course_id) ?? '—'}</span>
              <span className="up-title">
                <SourceDot source={a.source} />
                {a.title}
              </span>
              <StatusPill status={a.status} />
            </Link>
          </li>
        ))}
      </ul>
      {upcoming.length > UPCOMING_LIMIT && (
        <button type="button" className="btn more" onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Show fewer' : `Show all ${upcoming.length}`}
        </button>
      )}
    </>
  )
}

export function Home() {
  const { courses } = useApp()

  if (courses.error && !courses.data)
    return <ErrorState error={courses.error} onRetry={courses.reload} what="Your classes" />
  if (!courses.data) return <Loading />
  if (courses.data.length === 0) {
    return (
      <EmptyState title="No classes yet">
        <Link to={href.settings()}>Connect a source in Settings</Link>, then press Sync now.
      </EmptyState>
    )
  }

  const terms = [...new Set(courses.data.map((c) => c.term).filter(Boolean))]
  return (
    <div className="home">
      <header className="page-h">
        <h1>All classes</h1>
        {terms.length > 0 && <p>{terms.join(' · ')}</p>}
      </header>
      <ul className="course-grid">
        {courses.data.map((c) => (
          <CourseCard key={c.id} course={c} />
        ))}
      </ul>
      <section className="section" aria-labelledby="upcoming-h">
        <h2 id="upcoming-h" className="section-h">
          Upcoming across all classes
        </h2>
        <Upcoming courses={courses.data} />
      </section>
    </div>
  )
}
