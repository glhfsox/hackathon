import type { CSSProperties, ReactNode } from 'react'
import type { Action, DecidedBy } from '../api/types'

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

/** Rule outcomes are categorical; only AI judgments have a meaningful risk score. */
export function CheckOutcome({ check, decided_by, action, score }: { check: string; decided_by: DecidedBy; action: Action; score: number }) {
  if (check !== 'jev' && decided_by === 'rules') {
    const labels = { allow: '✓ Passed', block: 'Blocked', redact: 'Redacted', flag: 'Flagged' }
    return <span className={`rule-outcome ${action === 'allow' ? 'bright' : 'accent'}`}>{labels[action]}</span>
  }
  return <span className="risk-score" title="AI risk score: 0 is low risk, 1 is high risk">
    {score.toFixed(2)} <TextBar value={score} width={5} className={score >= 0.7 ? 'accent' : 'bright'} />
  </span>
}
