import { href } from '../../lib/router'
import { ChatIcon, MenuIcon } from '../icons'
import { Link } from '../ui'
import { SearchBox } from './SearchBox'
import { SyncStatus } from './SyncStatus'

interface TopBarProps {
  chatOpen: boolean
  onToggleChat: () => void
  onToggleNav: () => void
}

export function TopBar({ chatOpen, onToggleChat, onToggleNav }: TopBarProps) {
  return (
    <header className="topbar">
      <button type="button" className="icon-btn nav-toggle" onClick={onToggleNav} aria-label="Show classes">
        <MenuIcon />
      </button>
      <Link to={href.home()} className="wordmark">
        Study<span>Hub</span>
      </Link>
      <SearchBox />
      <SyncStatus />
      <button
        type="button"
        className={`btn small chat-toggle${chatOpen ? ' on' : ''}`}
        onClick={onToggleChat}
        aria-pressed={chatOpen}
        title={chatOpen ? 'Hide chat' : 'Show chat'}
        aria-label="Chat"
      >
        <ChatIcon />
        <span className="btn-label">Chat</span>
      </button>
    </header>
  )
}
