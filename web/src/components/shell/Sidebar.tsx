import { useApp } from '../../lib/appContext'
import { formatDay, formatDue } from '../../lib/format'
import { href, lastTab } from '../../lib/router'
import { Link } from '../ui'

interface SidebarProps {
  activeCourseId: number | null
  homeActive: boolean
  open: boolean
  /** Called when a link is chosen, so the mobile drawer can close even if the page doesn't change. */
  onNavigate: () => void
}

export function Sidebar({ activeCourseId, homeActive, open, onNavigate }: SidebarProps) {
  const { courses } = useApp()
  const list = courses.data ?? []

  return (
    <nav
      className={`side${open ? ' open' : ''}`}
      aria-label="Classes"
      onClick={(e) => (e.target as Element).closest('a') && onNavigate()}
    >
      <Link to={href.home()} className={`it${homeActive ? ' on' : ''}`} aria-current={homeActive ? 'page' : undefined}>
        All classes
      </Link>
      <div className="sep" role="separator" />
      {list.map((c) => {
        const on = c.id === activeCourseId
        return (
          <Link
            key={c.id}
            to={href.course(c.id, lastTab(c.id))}
            className={`it course-it${on ? ' on' : ''}`}
            aria-current={on ? 'page' : undefined}
          >
            <b>{c.code}</b>
            {c.title && <span className="course-it-title">{c.title}</span>}
            {c.next_due && (
              <span className="course-it-due" title={`${c.next_due.title}, ${formatDue(c.next_due.due_at)}`}>
                Next due {formatDay(c.next_due.due_at)}
              </span>
            )}
          </Link>
        )
      })}
      {courses.data && list.length === 0 && <p className="hint">Your courses appear here after the first sync.</p>}
    </nav>
  )
}
