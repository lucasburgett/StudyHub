import { useState } from 'react'
import { endpoints } from '../../api/client'
import type { CourseSummary } from '../../api/types'
import { useApp } from '../../lib/appContext'
import { formatDateTime, formatDue, parseDate, plural } from '../../lib/format'
import { href, lastTab } from '../../lib/router'
import { useApi } from '../../lib/useApi'
import { useNow } from '../../lib/useNow'
import { EmptyState, ErrorState, Link, Loading, SourceDot, StatusPill } from '../ui'

const UPCOMING_LIMIT = 12

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

function Upcoming() {
  const all = useApi(endpoints.assignments())
  const [expanded, setExpanded] = useState(false)
  const now = useNow()

  if (all.error && !all.data) return <ErrorState error={all.error} what="Upcoming assignments" />
  if (!all.data) return <Loading />

  const upcoming = all.data
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
              <span className="up-code">{a.course_code}</span>
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
      <p>
        <Link to={href.assignments()}>All assignments, including past and undated ones</Link>
      </p>
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
        <Upcoming />
      </section>
    </div>
  )
}
