import { useCallback, useEffect, useRef, useState } from 'react'
import { endpoints } from './api/client'
import { ChatPanel } from './components/chat/ChatPanel'
import { MainView } from './components/MainView'
import { Sidebar } from './components/shell/Sidebar'
import { TopBar } from './components/shell/TopBar'
import { AppContext, NO_CONTEXT, ReportContext, type ViewContext } from './lib/appContext'
import { rememberTab, useRoute } from './lib/router'
import { usePersistentState } from './lib/storage'
import { DataVersion, useApi } from './lib/useApi'
import { useSyncStatus, type SyncState } from './lib/useSyncStatus'

const WIDE_QUERY = '(min-width: 1101px)'

function Shell({ sync }: { sync: SyncState }) {
  const courses = useApi(endpoints.courses())
  const route = useRoute()
  const [context, setContext] = useState<ViewContext>(NO_CONTEXT)
  const [chatOpen, setChatOpen] = usePersistentState('studyhub.chatOpen', () => window.matchMedia(WIDE_QUERY).matches)
  // The mobile class drawer is open for the page it was opened on, so navigating closes it.
  const [navOpenOn, setNavOpenOn] = useState<string | null>(null)
  const mainRef = useRef<HTMLElement>(null)

  if (route.name === 'course') rememberTab(route.courseId, route.tab)

  // A new page (not just a new page number or timestamp) starts scrolled to the top.
  const pageKey =
    route.name === 'course'
      ? `course/${route.courseId}/${route.tab}`
      : route.name === 'resource'
        ? `resource/${route.resourceId}`
        : route.name === 'assignment'
          ? `assignment/${route.assignmentId}`
          : 'home'
  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 })
  }, [pageKey])
  const navOpen = navOpenOn === pageKey
  const closeNav = useCallback(() => setNavOpenOn(null), [])

  useEffect(() => {
    if (!navOpen) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setNavOpenOn(null)
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [navOpen])

  const closeChat = useCallback(() => setChatOpen(false), [setChatOpen])
  const app = { sync, courses }
  const offline = sync.statusError?.offline ?? false

  return (
    <AppContext.Provider value={app}>
      <ReportContext.Provider value={setContext}>
        <div className={`app${chatOpen ? ' chat-open' : ''}${navOpen ? ' nav-open' : ''}`}>
          {/* A button, not an anchor: "#main" would be read as a route by the hash router. */}
          <button type="button" className="skip" onClick={() => mainRef.current?.focus()}>
            Skip to content
          </button>
          <TopBar
            chatOpen={chatOpen}
            onToggleChat={() => setChatOpen(!chatOpen)}
            onToggleNav={() => setNavOpenOn(navOpen ? null : pageKey)}
          />
          {sync.status?.demo && (
            <div className="banner demo" role="note">
              <span>
                You're looking at example data. Add your accounts to <code>backend/.env</code> and run a sync to replace
                it.
              </span>
            </div>
          )}
          {sync.statusError && (
            <div className="banner offline" role="alert">
              <span>
                {offline
                  ? "Can't reach the StudyHub backend. Start it on 127.0.0.1:8000; this page retries every few seconds."
                  : `The backend returned an error: ${sync.statusError.message}`}
              </span>
              <button type="button" className="btn small" onClick={sync.refresh}>
                Retry now
              </button>
            </div>
          )}
          <div className="body">
            <Sidebar
              activeCourseId={route.name === 'course' ? route.courseId : context.courseId}
              homeActive={route.name === 'home'}
              open={navOpen}
              onNavigate={closeNav}
            />
            {navOpen && <button type="button" className="scrim" aria-label="Close classes" onClick={closeNav} />}
            <main className="main" ref={mainRef} tabIndex={-1}>
              <MainView route={route} />
            </main>
            <ChatPanel context={context} open={chatOpen} onClose={closeChat} />
          </div>
        </div>
      </ReportContext.Provider>
    </AppContext.Provider>
  )
}

export default function App() {
  const sync = useSyncStatus()
  return (
    <DataVersion.Provider value={sync.dataVersion}>
      <Shell sync={sync} />
    </DataVersion.Provider>
  )
}
