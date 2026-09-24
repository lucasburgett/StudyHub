// Compact date and number formatting in the browser's local time zone.

const dayFmt = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
const dayYearFmt = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  year: 'numeric',
})
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })

/** Parses an ISO timestamp, or a YYYY-MM-DD date as local midnight (not UTC). */
export function parseDate(value: string): Date {
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (dateOnly) return new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
  return new Date(value)
}

/** "Tue Sep 29" (adds the year when it is not the current one). */
export function formatDay(value: string): string {
  const date = parseDate(value)
  if (Number.isNaN(date.getTime())) return value
  const fmt = date.getFullYear() === new Date().getFullYear() ? dayFmt : dayYearFmt
  // Join the parts with spaces so "Tue, Sep 29" reads as "Tue Sep 29".
  return fmt
    .formatToParts(date)
    .filter((p) => p.type !== 'literal')
    .map((p) => p.value)
    .join(' ')
}

/** "Fri Oct 9, 11:59 PM" */
export function formatDateTime(value: string): string {
  const date = parseDate(value)
  if (Number.isNaN(date.getTime())) return value
  return `${formatDay(value)}, ${timeFmt.format(date)}`
}

/** "Due Fri Oct 9, 11:59 PM" */
export function formatDue(value: string | null): string {
  return value ? `Due ${formatDateTime(value)}` : 'No due date'
}

/** "just now", "12 min ago", "3 h ago", "2 d ago", then the date. */
export function formatAgo(value: string, now = Date.now()): string {
  const then = parseDate(value).getTime()
  if (Number.isNaN(then)) return value
  const minutes = Math.round((now - then) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days} d ago`
  return formatDay(value)
}

/** 18.5 → "18.5", 20 → "20" */
export function formatNumber(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
}

/** "18 / 20", "– / 20", "18", or "" */
export function formatScore(score: number | null, points: number | null): string {
  if (score === null && points === null) return ''
  if (points === null) return formatNumber(score as number)
  return `${score === null ? '–' : formatNumber(score)} / ${formatNumber(points)}`
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}
