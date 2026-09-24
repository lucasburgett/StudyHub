import type { SVGProps } from 'react'

// A handful of 16px stroke icons. Decorative by default; give the parent control an accessible name.

function Svg(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    />
  )
}

export const MenuIcon = () => (
  <Svg>
    <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />
  </Svg>
)

export const ChatIcon = () => (
  <Svg>
    <path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" />
  </Svg>
)

export const CloseIcon = () => (
  <Svg>
    <path d="M4 4l8 8M12 4l-8 8" />
  </Svg>
)

export const BackIcon = () => (
  <Svg>
    <path d="M9.5 3.5L5 8l4.5 4.5M5.5 8H13" />
  </Svg>
)

export const ExternalIcon = () => (
  <Svg width="12" height="12">
    <path d="M6.5 3.5h-3v9h9v-3M9 3h4v4M13 3L7.5 8.5" />
  </Svg>
)

export const SearchIcon = () => (
  <Svg>
    <circle cx="7" cy="7" r="4.25" />
    <path d="M10.2 10.2L13.5 13.5" />
  </Svg>
)

export const SyncIcon = () => (
  <Svg width="14" height="14">
    <path d="M13 3.5v3h-3M3 12.5v-3h3" />
    <path d="M12.6 6.5A5 5 0 0 0 3.8 5M3.4 9.5a5 5 0 0 0 8.8 1.5" />
  </Svg>
)

export const StopIcon = () => (
  <Svg width="14" height="14">
    <rect x="4" y="4" width="8" height="8" rx="1.5" fill="currentColor" stroke="none" />
  </Svg>
)

export const SendIcon = () => (
  <Svg width="14" height="14">
    <path d="M8 13V3.5M3.8 7.5L8 3.3l4.2 4.2" />
  </Svg>
)

export const SettingsIcon = () => (
  <Svg>
    <path d="M2.5 4h6M11.5 4h2M2.5 8h1.5M7 8h6.5M2.5 12h7.5M13 12h.5" />
    <circle cx="10" cy="4" r="1.5" />
    <circle cx="5.5" cy="8" r="1.5" />
    <circle cx="11.5" cy="12" r="1.5" />
  </Svg>
)
