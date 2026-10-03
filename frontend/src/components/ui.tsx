import type { CSSProperties, ReactNode } from 'react'
import type { Action } from '../api/types'

export function Pane(props: {
  title: ReactNode
  children: ReactNode
  className?: string
  contentClassName?: string
  style?: CSSProperties
}) {
  return (
    <section className={`pane ${props.className ?? ''}`} style={props.style}>
      <h2 className="pane-title">{props.title}</h2>
      <div className={`pane-content ${props.contentClassName ?? ''}`}>{props.children}</div>
    </section>
  )
}

export function ActionTag({ action, children }: { action: Action; children?: ReactNode }) {
  return (
    <span className={`tag tag-${action}`}>
      {action}
      {children}
    </span>
  )
}

/** A text bar such as `███████░░░` for a value in 0..1. */
export function TextBar({ value, width = 10, className }: { value: number; width?: number; className?: string }) {
  const full = Math.max(0, Math.min(width, Math.round(value * width)))
  return (
    <span className={className}>
      {'█'.repeat(full)}
      <span className="empty">{'░'.repeat(width - full)}</span>
    </span>
  )
}

/** Loading and error states shared by every pane that fetches. */
export function Status({ error, loading }: { error: string | null; loading: boolean }) {
  if (error) return <div className="error-text status">E {error}</div>
  if (loading) return <div className="dim status">loading…</div>
  return null
}
