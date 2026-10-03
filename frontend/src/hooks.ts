import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

export interface Polled<T> {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => void
}

/** Calls `load` now, whenever `key` changes, and every `intervalMs` (0: once). */
export function usePolled<T>(load: () => Promise<T>, intervalMs: number, key = ''): Polled<T> {
  const [data, setData] = useState<{ key: string; value: T } | null>(null)
  const [error, setError] = useState<{ key: string; value: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const loadRef = useRef(load)
  const keyRef = useRef(key)
  const generation = useRef(0)
  const pending = useRef(false)
  // Latest loader without restarting the timer on every render.
  useLayoutEffect(() => {
    loadRef.current = load
    keyRef.current = key
  })

  const reload = useCallback(() => {
    if (pending.current) return
    pending.current = true
    const current = ++generation.current
    const queryKey = keyRef.current
    setLoading(true)
    loadRef
      .current()
      .then((d) => {
        if (current !== generation.current) return
        setData({ key: queryKey, value: d })
        setError(null)
      })
      .catch((e: unknown) => {
        if (current === generation.current) setError({ key: queryKey, value: e instanceof Error ? e.message : String(e) })
      })
      .finally(() => {
        if (current === generation.current) {
          pending.current = false
          setLoading(false)
        }
      })
  }, [])

  const invalidate = useCallback(() => {
    ++generation.current
    pending.current = false
  }, [])

  useEffect(() => {
    reload()
    const id = intervalMs > 0 ? window.setInterval(reload, intervalMs) : undefined
    return () => {
      invalidate()
      if (id !== undefined) window.clearInterval(id)
    }
  }, [reload, invalidate, intervalMs, key])

  return {
    data: data?.key === key ? data.value : null,
    error: error?.key === key ? error.value : null,
    loading: loading || (data?.key !== key && error?.key !== key),
    reload,
  }
}

export const fmtTime = (iso: string) => new Date(iso).toLocaleTimeString('en-GB')
export const shortId = (id: string | null) => (id ? id.slice(0, 8) : '-')
