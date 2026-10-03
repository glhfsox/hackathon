import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

export interface Polled<T> {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => void
}

/** Calls `load` now, whenever `key` changes, and every `intervalMs` (0: once). */
export function usePolled<T>(load: () => Promise<T>, intervalMs: number, key = ''): Polled<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const loadRef = useRef(load)
  // Latest loader without restarting the timer on every render.
  useLayoutEffect(() => {
    loadRef.current = load
  })

  const reload = useCallback(() => {
    loadRef
      .current()
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    reload()
    if (intervalMs <= 0) return
    const id = window.setInterval(reload, intervalMs)
    return () => window.clearInterval(id)
  }, [reload, intervalMs, key])

  return { data, error, loading, reload }
}

export function useClock(): string {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(id)
  }, [])
  return now.toLocaleTimeString('en-GB')
}

export const fmtTime = (iso: string) => new Date(iso).toLocaleTimeString('en-GB')
export const shortId = (id: string | null) => (id ? id.slice(0, 8) : '-')
