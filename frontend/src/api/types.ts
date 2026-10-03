// Mirrors contracts/models.md and contracts/http-api.md. Change those first, then this file.

export type Checkpoint = 'input' | 'tool_call' | 'tool_result' | 'output'
export type Action = 'allow' | 'redact' | 'block' | 'flag'
export type Mode = 'off' | 'monitor' | 'redact' | 'block'
export type DecidedBy = 'rules' | 'jev' | 'fallback'
export type Verdict = 'allow' | 'redact' | 'block' | 'error'

export const CHECKPOINTS: Checkpoint[] = ['input', 'tool_call', 'tool_result', 'output']
export const ACTIONS: Action[] = ['allow', 'redact', 'block', 'flag']

export interface Redaction {
  kind: string
  start: number
  end: number
  replacement: string
  message_index: number
}

export interface CheckResult {
  check: string
  checkpoint: Checkpoint
  verdict: Verdict
  action: Action
  score: number
  reason: string
  redactions: Redaction[]
  latency_ms: number
  decided_by: DecidedBy
}

export interface Decision {
  request_id: string
  checkpoint: Checkpoint
  action: Action
  blocked_by: string | null
  results: CheckResult[]
}

export interface ControlTrace {
  request_id: string
  decisions: Decision[]
}

export interface AuditRecord {
  id: number | null
  ts: string
  request_id: string | null
  caller_id: string | null
  model: string | null
  checkpoint: Checkpoint | null
  check: string
  action: Action
  reason: string
  score: number
  latency_ms: number
  decided_by: DecidedBy
  tokens: number
  cost: number
  policy_version: string
  conversation_id: string | null
  step: number | null
  tools: string | null
  overhead_ms: number | null
  upstream_latency_ms: number | null
  blocked_by: string | null
}

export interface AuditPage {
  total: number
  items: AuditRecord[]
}

export interface AuditFilters {
  caller_id?: string
  check?: string
  action?: Action
  checkpoint?: Checkpoint
  since?: string
  until?: string
}

export interface PolicyDocument {
  yaml: string
  version: string
  loaded_at: string
}

export interface PolicyError {
  loc: string
  msg: string
}

export interface PolicyValidation {
  valid: boolean
  errors: PolicyError[]
}

export interface PolicySaved {
  version: string
  loaded_at: string
}

export interface Health {
  status: 'ok'
  policy_version: string | null
  jev: 'up' | 'down'
  fallback: 'up' | 'down'
}

export interface Percentiles {
  p50: number
  p95: number
}

export interface CallerBudget {
  tokens_today: number
  tokens_limit: number
  cost_today: number
  cost_limit: number
}

export interface Totals {
  requests: number
  allowed: number
  redacted: number
  blocked: number
  flagged: number
}

export interface TimelineBucket {
  minute: string
  requests: number
  blocked: number
  redacted: number
  flagged: number
}

export interface BlockReason {
  check: string
  reason: string
  count: number
}

// The contract fields plus the additive ones the backend sends (app/core/metrics.py).
export interface Metrics {
  active_profile: string
  totals: Totals
  blocks_by_check: Record<string, number>
  latency_ms_by_check: Record<string, Percentiles>
  overhead_ms: Percentiles
  budget_by_caller: Record<string, CallerBudget>
  enabled_checks: Record<string, Record<Checkpoint, Mode>>
  timeline: TimelineBucket[]
  top_block_reasons: BlockReason[]
  tokens_total: number
  cost_total: number
  system_events: Record<string, number>
}

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string | null
}

export interface ChatCompletion {
  id: string
  model: string
  choices: { index: number; finish_reason: string; message: ChatMessage }[]
  usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number }
  control?: ControlTrace
}
