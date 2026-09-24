import { useRef } from 'react'
import { endpoints } from '../../api/client'
import type { AssignmentSummary, Lecture, TimelineWeek } from '../../api/types'
import { formatDay, formatDue, parseDate } from '../../lib/format'
import { lectureTitle } from '../../lib/labels'
import { href } from '../../lib/router'
import { useApi } from '../../lib/useApi'
import { useNow } from '../../lib/useNow'
import { EmptyState, ErrorState, Link, Loading, SourceDot } from '../ui'
import { AssignmentStatusChip, GapChip, ResourceChip } from './chips'

const WEEK_MS = 7 * 24 * 60 * 60 * 1000

function isCurrentWeek(week: TimelineWeek, now: number): boolean {
  if (!week.start) return false
  const start = parseDate(week.start).getTime()
  return now >= start && now < start + WEEK_MS
}

function LectureCard({ lecture }: { lecture: Lecture }) {
  return (
    <article className="lec">
      <div className="lec-h">
        <b>{lectureTitle(lecture)}</b>
        {lecture.date && <span className="date">{formatDay(lecture.date)}</span>}
      </div>
      {(lecture.resources.length > 0 || lecture.missing.length > 0) && (
        <div className="chips">
          {lecture.resources.map((r) => (
            <ResourceChip key={r.id} resource={r} range={r.pages} />
          ))}
          {lecture.missing.map((gap) => (
            <GapChip key={gap} gap={gap} />
          ))}
        </div>
      )}
    </article>
  )
}

function AssignmentCard({ assignment }: { assignment: AssignmentSummary }) {
  return (
    <article className="lec assignment">
      <div className="lec-h">
        <Link to={href.assignment(assignment.id)} className="lec-title">
          {assignment.title}
        </Link>
        <span className="date">{formatDue(assignment.due_at)}</span>
      </div>
      <div className="chips">
        {assignment.spec_resource_id !== null && (
          <Link to={href.resource(assignment.spec_resource_id)} className="chip">
            <SourceDot source={assignment.source} />
            Spec
          </Link>
        )}
        <AssignmentStatusChip assignment={assignment} />
      </div>
    </article>
  )
}

export function TimelineTab({ courseId }: { courseId: number }) {
  const timeline = useApi(endpoints.timeline(courseId))
  const currentRef = useRef<HTMLElement>(null)
  const now = useNow()

  if (timeline.error && !timeline.data)
    return <ErrorState error={timeline.error} onRetry={timeline.reload} what="Timeline" />
  if (!timeline.data) return <Loading />

  const weeks = timeline.data.weeks.filter((w) => w.lectures.length || w.assignments.length || w.other.length)
  if (weeks.length === 0) {
    return (
      <EmptyState title="No lectures yet">Lectures, slides, notes, and recordings appear here after a sync.</EmptyState>
    )
  }

  const currentIndex = weeks.findIndex((w) => isCurrentWeek(w, now))

  return (
    <div className="timeline">
      {currentIndex > 0 && (
        <button
          type="button"
          className="btn small jump"
          onClick={() => currentRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
        >
          Jump to this week
        </button>
      )}
      {weeks.map((week, i) => {
        const current = i === currentIndex
        return (
          <section
            key={week.week ?? `undated-${i}`}
            className={`week${current ? ' current' : ''}`}
            ref={current ? currentRef : undefined}
            aria-label={week.label}
          >
            <h2 className="week-h">
              {week.label}
              {week.start && <span> · {formatDay(week.start)}</span>}
              {current && <span className="pill accent">This week</span>}
            </h2>
            {week.lectures.map((l) => (
              <LectureCard key={l.id} lecture={l} />
            ))}
            {week.assignments.map((a) => (
              <AssignmentCard key={a.id} assignment={a} />
            ))}
            {week.other.length > 0 && (
              <div className="lec other">
                <div className="lec-h">
                  <b>{week.week === null ? 'Other items' : 'Also this week'}</b>
                </div>
                <div className="chips">
                  {week.other.map((r) => (
                    <ResourceChip key={r.id} resource={r} full />
                  ))}
                </div>
              </div>
            )}
          </section>
        )
      })}
    </div>
  )
}
