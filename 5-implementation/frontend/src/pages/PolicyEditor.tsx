import { useRef, useState, type KeyboardEvent } from 'react'
import { getAudit, getMetrics, getPolicy, PolicyRejected, savePolicy, validatePolicy } from '../api/client'
import { CHECKPOINTS, type AuditRecord, type Mode, type PolicyError } from '../api/types'
import { Pane, Status } from '../components/ui'
import { fmtTime, usePolled } from '../hooks'

// Jev blocks at or above the threshold, so a lower one is stricter.
const THRESHOLDS = [
  { value: '0.4', label: 'strict' },
  { value: '0.6', label: 'balanced' },
  { value: '0.8', label: 'permissive' },
]
const MODE_LETTER: Record<Mode, string> = { off: '-', monitor: 'F', redact: 'R', block: 'B' }
const JEV_THRESHOLD = /^jev_threshold:\s*(\S+)/m

function MatrixCell({ mode }: { mode: Mode | undefined }) {
  const m = mode ?? 'off'
  const cls = m === 'block' ? 'tag tag-block' : m === 'redact' ? 'tag tag-redact' : m === 'monitor' ? 'tag tag-flag' : 'dim'
  return (
    <td style={{ textAlign: 'center' }}>
      <span className={cls}>{MODE_LETTER[m]}</span>
    </td>
  )
}

async function loadHistory(): Promise<AuditRecord[]> {
  const [loaded, rejected] = await Promise.all([
    getAudit({ check: 'policy_loaded' }, 20),
    getAudit({ check: 'policy_rejected' }, 20),
  ])
  return [...loaded.items, ...rejected.items].sort((a, b) => b.ts.localeCompare(a.ts)).slice(0, 20)
}

type Msg = { kind: 'ok' | 'error'; text: string } | null

export default function PolicyEditor({ onSaved }: { onSaved: () => void }) {
  const policy = usePolled(getPolicy, 0)
  const metrics = usePolled(() => getMetrics(), 0)
  const history = usePolled(loadHistory, 0)
  // null: no local edit, the editor shows the policy in force
  const [draft, setDraft] = useState<string | null>(null)
  const [errors, setErrors] = useState<PolicyError[]>([])
  const [msg, setMsg] = useState<Msg>(null)
  const [busy, setBusy] = useState(false)
  const gutter = useRef<HTMLDivElement>(null)

  const original = policy.data?.yaml ?? ''
  const text = draft ?? original
  const modified = draft !== null && draft !== original
  const lines = text.split('\n').length
  const threshold = JEV_THRESHOLD.exec(text)?.[1]

  const reportErrors = (errs: PolicyError[]) => {
    setErrors(errs)
    setMsg({ kind: 'error', text: errs.map((e) => `${e.loc}: ${e.msg}`).join(' · ') })
  }

  const validate = async () => {
    setBusy(true)
    try {
      const v = await validatePolicy(text)
      if (v.valid) {
        setErrors([])
        setMsg({ kind: 'ok', text: 'valid' })
      } else reportErrors(v.errors)
    } catch (e) {
      setMsg({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    setBusy(true)
    try {
      const saved = await savePolicy(text)
      setErrors([])
      setDraft(null)
      setMsg({ kind: 'ok', text: `written & applied: ${saved.version}` })
      policy.reload()
      metrics.reload()
      history.reload()
      onSaved()
    } catch (e) {
      if (e instanceof PolicyRejected) {
        reportErrors(e.errors)
        history.reload()
      } else setMsg({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const revert = () => {
    setDraft(null)
    setErrors([])
    setMsg(null)
  }

  const setThreshold = (t: string) => {
    if (JEV_THRESHOLD.test(text)) setDraft(text.replace(JEV_THRESHOLD, `jev_threshold: ${t}`))
  }

  const onKey = (e: KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault()
      void save()
    }
  }

  if (!policy.data) return <Status error={policy.error} loading={policy.loading} />

  const enabled = metrics.data?.enabled_checks ?? {}

  return (
    <>
      <div className="row grow">
        <Pane title={`Policy${modified ? ' · unsaved' : ''}`} style={{ flex: 3 }} contentClassName="flush">
          <div style={{ display: 'flex', height: '100%', background: 'var(--bg)' }}>
            <div
              ref={gutter}
              className="dim"
              style={{ overflow: 'hidden', textAlign: 'right', padding: '8px 8px 8px 16px', borderRight: '1px solid var(--line)', background: 'var(--bg-pane)', userSelect: 'none' }}
            >
              {Array.from({ length: lines }, (_, i) => (
                <div key={i}>{i + 1}</div>
              ))}
            </div>
            <textarea
              aria-label="policy yaml"
              value={text}
              spellCheck={false}
              wrap="off"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKey}
              onScroll={(e) => {
                if (gutter.current) gutter.current.scrollTop = e.currentTarget.scrollTop
              }}
              style={{ flex: 1, resize: 'none', border: 0, outline: 'none', background: 'transparent', color: 'var(--fg-bright)', padding: '8px 16px', lineHeight: '20px', whiteSpace: 'pre', caretColor: 'var(--accent)' }}
            />
          </div>
        </Pane>

        <div className="col" style={{ width: 320 }}>
          <Pane title="Active checks" className="grow">
            <Status error={metrics.error} loading={metrics.loading && !metrics.data} />
            <table>
              <thead>
                <tr>
                  <th />
                  {CHECKPOINTS.map((c) => (
                    <th key={c} style={{ textAlign: 'center' }} title={c}>
                      {c === 'tool_call' ? 'TC' : c === 'tool_result' ? 'TR' : c[0].toUpperCase()}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(enabled).map(([check, modes]) => (
                  <tr key={check} style={{ height: 28, borderTop: '1px solid var(--line)' }}>
                    <td style={{ paddingLeft: 0 }}>{check}</td>
                    {CHECKPOINTS.map((c) => (
                      <MatrixCell key={c} mode={modes[c]} />
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="dim" style={{ fontSize: 10, marginTop: 8 }}>
              F: monitor · R: redact · B: block · –: off
            </div>
          </Pane>
          <Pane title="Jev threshold" style={{ flexShrink: 0 }}>
            {THRESHOLDS.map(({ value, label }) => (
              <div key={value}>
                <button
                  className={`btn ${Number(value) === Number(threshold) ? 'accent' : ''}`}
                  onClick={() => setThreshold(value)}
                >
                  ({Number(value) === Number(threshold) ? '•' : ' '}) {value} {label}
                </button>
              </div>
            ))}
            <div className="dim" style={{ fontSize: 11 }}>
              now {threshold ?? '?'}; lower is stricter. Edits the text; write to apply
            </div>
          </Pane>
          <Pane title="Policy history" className="grow">
            <Status error={history.error} loading={history.loading && !history.data} />
            {(history.data ?? []).map((r, i) => {
              const current = r.check === 'policy_loaded' && r.policy_version === policy.data?.version
              return (
                <div
                  key={`${r.ts}-${i}`}
                  title={r.reason}
                  className={current ? 'accent' : r.check === 'policy_rejected' ? 'accent bold' : 'dim'}
                  style={{ background: current ? 'var(--bg-sel)' : undefined, padding: '0 4px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
                >
                  {fmtTime(r.ts)} {r.check === 'policy_rejected' ? '(rejected)' : r.policy_version}
                  {current && ' (current)'}
                </div>
              )
            })}
          </Pane>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 16, flexShrink: 0 }}>
        <button className="btn" disabled={busy} onClick={() => void validate()}>
          Validate
        </button>
        <button className="btn" disabled={busy || !modified} onClick={() => void save()}>
          Save &amp; apply <span className="dim">(ctrl+s)</span>
        </button>
        <button className="btn" disabled={busy || !modified} onClick={revert}>
          Revert
        </button>
        <span className="dim" style={{ marginLeft: 'auto' }}>
          {policy.data.version} · loaded {fmtTime(policy.data.loaded_at)}
        </span>
      </div>
      {errors.length > 0 && (
        <Pane title={`Errors (${errors.length})`} style={{ flexShrink: 0, maxHeight: 160 }}>
          {errors.map((e, i) => (
            <div key={i} className="error-text">
              <span className="rev" style={{ padding: '0 4px', marginRight: 8 }}>
                E
              </span>
              {e.loc}: {e.msg}
            </div>
          ))}
        </Pane>
      )}
      {msg && errors.length === 0 && <div className={msg.kind === 'ok' ? 'bright' : 'error-text'}>{msg.kind === 'ok' ? '✓ ' : 'E '}{msg.text}</div>}
    </>
  )
}
