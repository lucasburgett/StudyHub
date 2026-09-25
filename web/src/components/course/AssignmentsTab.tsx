import { endpoints } from '../../api/client'
import { formatDateTime, formatScore } from '../../lib/format'
import { href, navigate } from '../../lib/router'
import { useApi } from '../../lib/useApi'
import { EmptyState, ErrorState, Link, Loading, SourceDot, StatusPill } from '../ui'
import { DoneToggle } from './DoneToggle'

export function AssignmentsTab({ courseId }: { courseId: number }) {
  const assignments = useApi(endpoints.courseAssignments(courseId))

  if (assignments.error && !assignments.data) {
    return <ErrorState error={assignments.error} onRetry={assignments.reload} what="Assignments" />
  }
  if (!assignments.data) return <Loading />
  if (assignments.data.length === 0) {
    return (
      <EmptyState title="No assignments yet">
        Assignments from Canvas and Gradescope appear here after a sync.
      </EmptyState>
    )
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">Assignment</th>
            <th scope="col">Due</th>
            <th scope="col">Status</th>
            <th scope="col" className="num">
              Score
            </th>
          </tr>
        </thead>
        <tbody>
          {assignments.data.map((a) => (
            <tr key={a.id} className="clickable" onClick={() => navigate(href.assignment(a.id))}>
              <td>
                <Link to={href.assignment(a.id)} className="row-title" onClick={(e) => e.stopPropagation()}>
                  <SourceDot source={a.source} />
                  {a.title}
                </Link>
              </td>
              <td className="date">{a.due_at ? formatDateTime(a.due_at) : '—'}</td>
              <td>
                <span className="status-cell">
                  <StatusPill status={a.status} />
                  {a.checkable && (
                    // The row opens the assignment; ticking the box shouldn't.
                    <span onClick={(e) => e.stopPropagation()}>
                      <DoneToggle assignment={a} onChange={assignments.reload} />
                    </span>
                  )}
                </span>
              </td>
              <td className="num">{formatScore(a.score, a.points) || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
