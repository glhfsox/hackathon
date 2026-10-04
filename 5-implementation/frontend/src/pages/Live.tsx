import { useEffect, useRef, useState } from 'react'
import { subscribeEvents } from '../api/client'
import type { Action, AuditRecord, Checkpoint } from '../api/types'
import { ActionTag, Pane } from '../components/ui'

// Each agent step is one request through the layer: checks on what the agent sent (input or
// tool_result), the model, checks on the model's reply (tool_call or output). The audit rows of
// one request share its request_id; its turn_summary rows carry the timings.
const MAX_TURNS = 60
const PRE: Checkpoint[] = ['input', 'tool_result']
const SUMMARY = 'turn_summary'

interface Stage {
  checkpoint: Checkpoint | null
  checks: AuditRecord[]
  summary: AuditRecord | null
}

interface Turn {
  id: string
  ts: string
  caller: string
  model: string | null
  conversation: string | null
  step: number | null
  pre: Stage
  post: Stage
  rbac: AuditRecord[]
  failure: AuditRecord | null // auth_failed, upstream_unavailable, bad_request
}

const emptyStage = (): Stage => ({ checkpoint: null, checks: [], summary: null })
const STRENGTH: Record<Action, number> = { allow: 0, flag: 1, redact: 2, block: 3 }

function add(turns: Map<string, Turn>, r: AuditRecord): void {
  if (!r.request_id) return
  let t = turns.get(r.request_id)
  if (!t) {
    t = {
      id: r.request_id,
      ts: r.ts,
      caller: r.caller_id ?? '?',
      model: r.model,
      conversation: null,
      step: null,
      pre: emptyStage(),
      post: emptyStage(),
      rbac: [],
      failure: null,
    }
    turns.set(r.request_id, t)
  }
  if (r.check === 'rbac') {
    t.rbac.push(r)
    return
  }
  if (!r.checkpoint) {
    t.failure = r
    return
  }
  const stage = PRE.includes(r.checkpoint) ? t.pre : t.post
  stage.checkpoint = r.checkpoint
  if (r.check === SUMMARY) {
    stage.summary = r
    t.conversation ??= r.conversation_id
    t.step ??= r.step
  } else {
    stage.checks.push(r)
  }
}

interface Timing {
  pre: number | null
  model: number | null
  post: number | null
  total: number | null
}

function timing(t: Turn): Timing {
  const pre = t.pre.summary?.overhead_ms ?? null
  const model = t.post.summary?.upstream_latency_ms ?? null
  const post = t.post.summary?.overhead_ms ?? null
  const blockedEarly = t.pre.summary?.action === 'block'
  const done = blockedEarly || t.post.summary !== null
  return { pre, model, post, total: done ? (pre ?? 0) + (model ?? 0) + (post ?? 0) : null }
}

function outcome(t: Turn): Action | 'running' | 'failed' {
  if (t.failure) return 'failed'
  const summaries = [t.pre.summary, t.post.summary].filter((s): s is AuditRecord => s !== null)
  if (summaries.some((s) => s.action === 'block')) return 'block'
  if (!t.post.summary) return 'running'
  return summaries.reduce<Action>((a, s) => (STRENGTH[s.action] > STRENGTH[a] ? s.action : a), 'allow')
}

const ms = (v: number | null) => (v === null ? '…' : v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${Math.round(v)}ms`)

function median(values: number[]): number | null {
  if (!values.length) return null
  const s = [...values].sort((a, b) => a - b)
  return s[Math.floor((s.length - 1) / 2)]
}

function CheckChip({ r }: { r: AuditRecord }) {
  const judge = r.check === 'jev'
  return (
    <div className="live-check" title={r.reason}>
      <span className={`tag tag-${r.action}`}>{r.action === 'allow' ? '✓' : r.action}</span>{' '}
      <span className={r.action === 'allow' ? '' : 'bright'}>{r.check}</span>
      {judge && (
        <span className="dim">
          {' '}
          {r.score.toFixed(2)} {r.decided_by}
        </span>
      )}
      {r.action !== 'allow' && <div className="live-reason">{r.reason}</div>}
    </div>
  )
}

function StageBox({ stage, label, waiting }: { stage: Stage; label: string; waiting: boolean }) {
  const blocked = stage.summary?.action === 'block'
  return (
    <div className={`live-stage ${blocked ? 'live-blocked' : ''}`}>
      <div className="live-stage-title">
        <span>{stage.checkpoint ?? label}</span>
        <span className="dim">{stage.summary ? ms(stage.summary.overhead_ms) : ''}</span>
      </div>
      {stage.checks.map((r) => (
        <CheckChip key={r.id ?? `${r.check}-${r.ts}`} r={r} />
      ))}
      {waiting && !stage.checks.length && <div className="dim blink">waiting…</div>}
    </div>
  )
}

function TurnCard({ t }: { t: Turn }) {
  const time = timing(t)
  const result = outcome(t)
  const stoppedBefore = t.pre.summary?.action === 'block'
  const tools = t.post.summary?.tools
  const denied = t.rbac.filter((r) => r.action === 'block')
  return (
    <div className={`live-turn live-${result}`}>
      <div className="live-turn-head">
        <span className="dim">{t.ts.slice(11, 19)}</span>
        <span className="bright bold">{t.caller}</span>
        <span className="dim">
          {t.model ?? ''}
          {t.conversation ? ` · session ${t.conversation.slice(0, 6)}` : ''}
          {t.step !== null ? ` · step ${t.step}` : ''}
        </span>
        <span className="grow" />
        {result === 'running' && <span className="accent blink">● running</span>}
        {result === 'failed' && <ActionTag action="block">{` ${t.failure?.check}`}</ActionTag>}
        {result !== 'running' && result !== 'failed' && <ActionTag action={result} />}
        <span className="bright" title="time to first token: replies are not streamed, so it is the whole step">
          TTFT {ms(time.total)}
        </span>
      </div>
      {t.failure ? (
        <div className="live-reason">{t.failure.reason}</div>
      ) : (
        <div className="live-flow">
          <StageBox stage={t.pre} label="input" waiting={!t.pre.summary} />
          <div className="live-arrow">→</div>
          <div className={`live-stage live-model ${stoppedBefore ? 'live-skipped' : ''}`}>
            <div className="live-stage-title">
              <span>model</span>
              <span className="dim">{stoppedBefore ? '' : ms(time.model)}</span>
            </div>
            {stoppedBefore ? (
              <div className="dim">not called</div>
            ) : t.post.summary ? (
              <div>{tools ? `asks: ${tools}` : 'answers'}</div>
            ) : (
              t.pre.summary && <div className="accent blink">thinking…</div>
            )}
            {denied.map((r) => (
              <div key={r.id ?? r.ts} className="live-reason" title={r.reason}>
                rbac: {r.tools} denied
              </div>
            ))}
          </div>
          <div className="live-arrow">→</div>
          {stoppedBefore ? (
            <div className="live-stage live-skipped">
              <div className="live-stage-title">reply</div>
              <div className="dim">—</div>
            </div>
          ) : (
            <StageBox stage={t.post} label="reply" waiting={!!t.pre.summary && !t.post.summary} />
          )}
        </div>
      )}
      <div className="live-timing dim">
        checks before {ms(time.pre)} · model {stoppedBefore ? '—' : ms(time.model)} · checks after{' '}
        {stoppedBefore ? '—' : ms(time.post)} · total {ms(time.total)}
      </div>
    </div>
  )
}

export default function Live() {
  const turns = useRef(new Map<string, Turn>())
  const pausedRef = useRef(false)
  const [list, setList] = useState<Turn[]>([])
  const [connected, setConnected] = useState(false)
  const [paused, setPaused] = useState(false)

  useEffect(() => {
    pausedRef.current = paused
  }, [paused])

  useEffect(
    () =>
      subscribeEvents(
        (r) => {
          add(turns.current, r)
          // Keep the newest requests only.
          while (turns.current.size > MAX_TURNS) turns.current.delete(turns.current.keys().next().value!)
          // A new array of fresh card objects, so React re-renders the cards that changed.
          if (!pausedRef.current) setList([...turns.current.values()].reverse().map((t) => ({ ...t })))
        },
        setConnected,
      ),
    [],
  )

  const done = list.filter((t) => timing(t).total !== null)
  const results = done.map(outcome)
  const ttft = median(done.map((t) => timing(t).total!))
  const model = median(done.flatMap((t) => (timing(t).model === null ? [] : [timing(t).model!])))
  const control = median(done.map((t) => (timing(t).pre ?? 0) + (timing(t).post ?? 0)))

  return (
    <div className="live">
      <Pane title="Live">
        <div className="live-stats">
          <span className={connected ? 'bright' : 'accent bold'}>{connected ? '● connected' : '○ reconnecting'}</span>
          <span>{list.length} requests</span>
          <span>{results.filter((r) => r === 'block').length} blocked</span>
          <span>{results.filter((r) => r === 'redact').length} redacted</span>
          <span>TTFT p50 {ms(ttft)}</span>
          <span>model p50 {ms(model)}</span>
          <span>control p50 {ms(control)}</span>
          <span className="grow" />
          <button
            className="btn"
            onClick={() => {
              if (paused) setList([...turns.current.values()].reverse().map((t) => ({ ...t })))
              setPaused((p) => !p)
            }}
          >
            {paused ? 'resume' : 'pause'}
          </button>
          <button
            className="btn"
            onClick={() => {
              turns.current.clear()
              setList([])
            }}
          >
            clear
          </button>
        </div>
      </Pane>
      <div className="live-list">
        {list.length === 0 && (
          <div className="dim">Waiting for agent traffic. Run e.g. `docker compose run --rm test-app --scenario all`.</div>
        )}
        {list.map((t) => (
          <TurnCard key={t.id} t={t} />
        ))}
      </div>
    </div>
  )
}
