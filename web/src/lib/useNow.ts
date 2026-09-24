import { useEffect, useState } from 'react'

/** The current time, refreshed every minute so "this week" and "upcoming" stay right in a long-open tab. */
export function useNow(intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])
  return now
}
