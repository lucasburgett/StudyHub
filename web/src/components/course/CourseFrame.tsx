import type { ReactNode } from 'react'
import { endpoints } from '../../api/client'
import { useApp } from '../../lib/appContext'
import { href, TABS, type Tab } from '../../lib/router'
import { useApi } from '../../lib/useApi'
import { ExternalLink, Link } from '../ui'

interface CourseFrameProps {
  courseId: number
  activeTab: Tab
  children: ReactNode
}

/** Course header and tab strip around the tab content or a viewer. */
export function CourseFrame({ courseId, activeTab, children }: CourseFrameProps) {
  const { courses } = useApp()
  const course = useApi(endpoints.course(courseId))
  const summary = courses.data?.find((c) => c.id === courseId)
  const code = course.data?.code ?? summary?.code ?? (course.error?.status === 404 ? 'Unknown course' : '…')
  const subtitle = [course.data?.title ?? summary?.title, course.data?.term ?? summary?.term]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="course">
      <header className="course-h">
        <div>
          <h1>{code}</h1>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {(course.data?.canvas_url || course.data?.site_url) && (
          <div className="course-links">
            {course.data.canvas_url && <ExternalLink href={course.data.canvas_url}>Canvas</ExternalLink>}
            {course.data.site_url && <ExternalLink href={course.data.site_url}>Course site</ExternalLink>}
          </div>
        )}
      </header>
      <nav className="tabs" aria-label="Course sections">
        {TABS.map((t) => (
          <Link
            key={t.id}
            to={href.course(courseId, t.id)}
            className={t.id === activeTab ? 'on' : undefined}
            aria-current={t.id === activeTab ? 'page' : undefined}
          >
            {t.label}
          </Link>
        ))}
      </nav>
      <div className="tab-body">{children}</div>
    </div>
  )
}
