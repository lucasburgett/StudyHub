import { useState } from 'react'
import { endpoints } from '../../api/client'
import type { AssignmentWithCourse } from '../../api/types'
import { formatDateTime, formatDay, formatScore, parseDate, plural } from '../../lib/format'
import { href, navigate } from '../../lib/router'
import { useApi } from '../../lib/useApi'
import { useNow } from '../../lib/useNow'
import { DoneToggle } from '../course/DoneToggle'
import { EmptyState, ErrorState, Link, Loading, SourceDot, StatusPill } from '../ui'

type View = 'upcoming' | 'past'

const VIEWS: { id: View; label: string }[] = [
  { id: 'upcoming', label: 'Upcoming' },
  { id: 'past', label: 'Past' },
]

const DAY_MS = 24 * 60 * 60 * 1000

function startOfDay(t: number): Date {
  const d = new Date(t)
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

/** Monday 00:00 of the week `date` falls in. */
function mondayOf(date: Date): Date {
  const day = startOfDay(date.getTime())
  return new Date(day.getFullYear(), day.getMonth(), day.getDate() - ((day.getDay() + 6) % 7))
}

function weekLabel(monday: Date, thisMonday: Date): string {
  const weeks = Math.round((monday.getTime() - thisMonday.getTime()) / (7 * DAY_MS))
  if (weeks === 0) return 'This week'
  if (weeks === 1) return 'Next week'
  if (weeks === -1) return 'Last week'
  return `Week of ${formatDay(monday.toISOString())}`
}

interface Group {
  key: string
  label: string
  items: AssignmentWithCourse[]
}

/** Assignments in due-date order, grouped by week; undated ones last, under their own heading. */
function groupByWeek(items: AssignmentWithCourse[], now: number): Group[] {
  const thisMonday = mondayOf(new Date(now))
  const groups: Group[] = []
  for (const a of items) {
    const monday = a.due_at ? mondayOf(parseDate(a.due_at)) : null
    const key = monday ? String(monday.getTime()) : 'undated'
    let group = groups.at(-1)
    if (!group || group.key !== key) {
      group = { key, label: monday ? weekLabel(monday, thisMonday) : 'No due date', items: [] }
      groups.push(group)
    }
    group.items.push(a)
  }
  return groups
}

export function AllAssignments() {
  const all = useApi(endpoints.assignments())
  const now = useNow()
  const [view, setView] = useState<View>('upcoming')

  if (all.error && !all.data) return <ErrorState error={all.error} onRetry={all.reload} what="Your assignments" />
  if (!all.data) return <Loading />

  // Due before today counts as past; anything due today stays in Upcoming all day.
  const today = startOfDay(now).getTime()
  const isPast = (a: AssignmentWithCourse) => a.due_at !== null && parseDate(a.due_at).getTime() < today
  const time = (a: AssignmentWithCourse) => (a.due_at ? parseDate(a.due_at).getTime() : Infinity)
  const items =
    view === 'upcoming'
      ? all.data.filter((a) => !isPast(a)).sort((a, b) => time(a) - time(b))
      : all.data.filter(isPast).sort((a, b) => time(b) - time(a))
  const classes = plural(new Set(items.map((a) => a.course_id)).size, 'class', 'classes')
  const summary =
    items.length === 0
      ? view === 'upcoming'
        ? 'Nothing coming up.'
        : 'Nothing past yet.'
      : `${plural(items.length, `${view} assignment`)} across ${classes}`

  return (
    <div className="home all-assignments-page">
      <header className="page-h">
        <h1>All assignments</h1>
        <p>{summary}</p>
      </header>

      <div className="segmented" role="radiogroup" aria-label="Which assignments">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            type="button"
            role="radio"
            aria-checked={view === v.id}
            className={view === v.id ? 'on' : undefined}
            onClick={() => setView(v.id)}
          >
            {v.label}
          </button>
        ))}
      </div>

      {all.data.length === 0 ? (
        <EmptyState title="No assignments yet">
          Assignments from Canvas, Gradescope and class pages appear here after a sync.
        </EmptyState>
      ) : (
        items.length > 0 && (
          <div className="table-wrap">
            <table className="all-assignments">
              <thead>
                <tr>
                  <th scope="col">Due</th>
                  <th scope="col">Class</th>
                  <th scope="col">Assignment</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="num">
                    Score
                  </th>
                </tr>
              </thead>
              {groupByWeek(items, now).map((g) => (
                <tbody key={g.key}>
                  <tr className="group-row">
                    <th scope="colgroup" colSpan={5}>
                      {g.label}
                    </th>
                  </tr>
                  {g.items.map((a) => (
                    <tr key={a.id} className="clickable" onClick={() => navigate(href.assignment(a.id))}>
                      <td className="date">{a.due_at ? formatDateTime(a.due_at) : 'No due date'}</td>
                      <td className="mono">{a.course_code}</td>
                      <td className="title-cell">
                        <Link to={href.assignment(a.id)} className="row-title" onClick={(e) => e.stopPropagation()}>
                          <SourceDot source={a.source} />
                          {a.title}
                        </Link>
                      </td>
                      <td>
                        <span className="status-cell">
                          <StatusPill status={a.status} />
                          {a.checkable && (
                            // The row opens the assignment; ticking the box shouldn't.
                            <span onClick={(e) => e.stopPropagation()}>
                              <DoneToggle assignment={a} onChange={all.reload} />
                            </span>
                          )}
                        </span>
                      </td>
                      <td className="num">{formatScore(a.score, a.points) || <span className="no-score">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              ))}
            </table>
          </div>
        )
      )}
    </div>
  )
}
