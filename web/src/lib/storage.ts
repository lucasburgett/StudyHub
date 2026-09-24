import { useCallback, useState } from 'react'

// Per-browser UI preferences only (panel open, PDF layout). Storage may be unavailable, so every access is guarded.

function read<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key)
    return raw === null ? fallback : (JSON.parse(raw) as T)
  } catch {
    return fallback
  }
}

function write(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Ignore: the preference just won't persist.
  }
}

export function usePersistentState<T>(key: string, initial: T | (() => T)): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() =>
    read(key, typeof initial === 'function' ? (initial as () => T)() : initial),
  )
  const set = useCallback(
    (next: T) => {
      setValue(next)
      write(key, next)
    },
    [key],
  )
  return [value, set]
}
