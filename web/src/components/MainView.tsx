import type { ReactNode } from 'react'
import { endpoints } from '../api/client'
import { NO_CONTEXT, useReportContext } from '../lib/appContext'
import { href, lastTab, tabForKind, type Route, type Tab } from '../lib/router'
import { useApi } from '../lib/useApi'
import { AnnouncementsTab } from './course/AnnouncementsTab'
import { AssignmentsTab } from './course/AssignmentsTab'
import { CourseFrame } from './course/CourseFrame'
import { ResourceListTab } from './course/ResourceListTab'
import { TimelineTab } from './course/TimelineTab'
import { Home } from './overview/Home'
import { SettingsView } from './settings/SettingsView'
import { ErrorState, Link, Loading } from './ui'
import { AssignmentView } from './viewer/AssignmentView'
import { ResourceViewer } from './viewer/ResourceViewer'

function HomeRoute() {
  useReportContext(NO_CONTEXT)
  return <Home />
}

function SettingsRoute() {
  useReportContext(NO_CONTEXT)
  return <SettingsView />
}

function TabContent({ courseId, tab }: { courseId: number; tab: Tab }) {
  switch (tab) {
    case 'timeline':
      return <TimelineTab courseId={courseId} />
    case 'assignments':
      return <AssignmentsTab courseId={courseId} />
    case 'announcements':
      return <AnnouncementsTab courseId={courseId} />
    default:
      return <ResourceListTab courseId={courseId} tab={tab} />
  }
}

function CourseRoute({ courseId, tab }: { courseId: number; tab: Tab }) {
  useReportContext({ courseId, resource: null })
  return (
    <CourseFrame courseId={courseId} activeTab={tab}>
      <TabContent key={courseId} courseId={courseId} tab={tab} />
    </CourseFrame>
  )
}

function NotLoaded({ children }: { children: ReactNode }) {
  return (
    <div className="not-loaded">
      {children}
      <Link to={href.home()}>Go to all classes</Link>
    </div>
  )
}

function ResourceRoute({
  resourceId,
  page,
  seconds,
}: {
  resourceId: number
  page: number | null
  seconds: number | null
}) {
  const { data: r, error, reload } = useApi(endpoints.resource(resourceId))
  useReportContext(
    r
      ? { courseId: r.course_id, resource: { id: r.id, title: r.title, courseCode: r.course_code } }
      : error
        ? NO_CONTEXT
        : null,
  )

  if (error && !r) {
    return (
      <NotLoaded>
        <ErrorState error={error} onRetry={reload} what="This item" />
      </NotLoaded>
    )
  }
  if (!r) return <Loading />
  // Keep Timeline highlighted when the item was opened from it; otherwise show the tab that lists it.
  const tab = lastTab(r.course_id) === 'timeline' ? 'timeline' : tabForKind(r.kind)
  return (
    <CourseFrame courseId={r.course_id} activeTab={tab}>
      <ResourceViewer resource={r} page={page} seconds={seconds} backTo={href.course(r.course_id, tab)} />
    </CourseFrame>
  )
}

function AssignmentRoute({ assignmentId, question }: { assignmentId: number; question: number | null }) {
  const { data: a, error, reload } = useApi(endpoints.assignment(assignmentId))
  useReportContext(a ? { courseId: a.course_id, resource: null } : error ? NO_CONTEXT : null)

  if (error && !a) {
    return (
      <NotLoaded>
        <ErrorState error={error} onRetry={reload} what="This assignment" />
      </NotLoaded>
    )
  }
  if (!a) return <Loading />
  return (
    <CourseFrame courseId={a.course_id} activeTab="assignments">
      <AssignmentView assignment={a} question={question} backTo={href.course(a.course_id, 'assignments')} />
    </CourseFrame>
  )
}

export function MainView({ route }: { route: Route }) {
  switch (route.name) {
    case 'home':
      return <HomeRoute />
    case 'course':
      return <CourseRoute courseId={route.courseId} tab={route.tab} />
    case 'resource':
      return <ResourceRoute resourceId={route.resourceId} page={route.page} seconds={route.seconds} />
    case 'assignment':
      return <AssignmentRoute assignmentId={route.assignmentId} question={route.question} />
    case 'settings':
      return <SettingsRoute />
  }
}
