import { getAudit, getHealth, getMetrics } from '../api/client'
import type { Metrics, Mode, TimelineBucket } from '../api/types'
import { ActionTag, Pane, Status, TextBar } from '../components/ui'
import { fmtTime, usePolled } from '../hooks'
import { sinceIso, type TimeRange } from '../ranges'

const POLL_MS = 5000
const BINS = 12

// OWASP LLM Top 10 (2023) items and the checks that address them. Coverage shown is what the
// policy in force enables, nothing more.
const OWASP: { id: string; label: string; checks: string[] }[] = [
  { id: 'LLM01', label: 'Inj', checks: ['signatures', 'jev'] },
  { id: 'LLM02', label: 'Out', checks: ['pii_secrets', 'jev'] },
  { id: 'LLM03', label: 'Data', checks: [] },
  { id: 'LLM04', label: 'DoS', checks: ['budget', 'loop_detection'] },
  { id: 'LLM05', label: 'Sup', checks: [] },
  { id: 'LLM06', label: 'Sens', checks: ['pii_secrets'] },
  { id: 'LLM07', label: 'Plug', checks: ['tool_args'] },
  { id: 'LLM08', label: 'Agcy', checks: ['permissions', 'loop_detection'] },
  { id: 'LLM09', label: 'Rel', checks: [] },
  { id: 'LLM10', label: 'Mdl', checks: [] },
]

const enforces = (m: Mode) => m === 'block' || m === 'redact'

function checkModes(metrics: Metrics) {
  const ids = Object.keys(metrics.enabled_checks)
  const modes = (id: string) => Object.values(metrics.enabled_checks[id] ?? {})
  return {
    total: ids.length,
    on: ids.filter((id) => modes(id).some((m) => m !== 'off')).length,
    enforcing: ids.filter((id) => modes(id).some(enforces)).length,
    level(id: string): 'on' | 'monitor' | 'off' {
      const ms = modes(id)
      if (ms.some(enforces)) return 'on'
      return ms.some((m) => m === 'monitor') ? 'monitor' : 'off'
    },
  }
}

/** Spread the per-minute buckets over BINS equal slices of the selected window. */
function binTimeline(timeline: TimelineBucket[], range: TimeRange) {
  const span = range.minutes * 60_000
  const start = Date.now() - span
  const bins = Array.from({ length: BINS }, () => ({ blocked: 0, redacted: 0, flagged: 0 }))
  for (const b of timeline) {
    const i = Math.floor(((new Date(b.minute).getTime() - start) / span) * BINS)
    if (i < 0 || i >= BINS) continue
    bins[i].blocked += b.blocked
    bins[i].redacted += b.redacted
    bins[i].flagged += b.flagged
  }
  return bins
}

function binLabel(i: number, range: TimeRange): string {
  if (i === BINS - 1) return 'now'
  const minutes = Math.round((range.minutes * (BINS - 1 - i)) / BINS)
  return minutes >= 1440 ? `-${Math.round(minutes / 1440)}d` : minutes >= 60 ? `-${Math.round(minutes / 60)}h` : `-${minutes}m`
}

export default function Overview({ range }: { range: TimeRange }) {
  const metrics = usePolled(() => getMetrics(sinceIso(range)), POLL_MS, String(range.minutes))
  const health = usePolled(getHealth, POLL_MS)
  const feed = usePolled(() => getAudit({ since: sinceIso(range) }, 200), POLL_MS, String(range.minutes))

  const m = metrics.data
  if (!m) return <Status error={metrics.error} loading={metrics.loading} />

  const modes = checkModes(m)
  const t = m.totals
  const bins = binTimeline(m.timeline, range)
  const binMax = Math.max(1, ...bins.map((b) => b.blocked + b.redacted + b.flagged))
  const blocks = Object.entries(m.blocks_by_check).sort((a, b) => b[1] - a[1])
  const blockMax = Math.max(1, ...blocks.map(([, n]) => n))
  const latency = Object.entries(m.latency_ms_by_check).filter(([c]) => c !== 'turn_summary')
  const latencyMax = Math.max(1, ...latency.map(([, p]) => p.p95))
  const threats = (feed.data?.items ?? [])
    .filter((r) => r.action !== 'allow' && r.checkpoint !== null && r.check !== 'turn_summary')
    .slice(0, 30)
  const counters: [string, number, string][] = [
    ['requests', t.requests, ''],
    ['allowed', t.allowed, 'dim'],
    ['redacted', t.redacted, 'accent'],
    ['blocked', t.blocked, 'accent bold'],
    ['flagged', t.flagged, ''],
  ]
  const share = (n: number) => (t.requests ? n / t.requests : 0)

  return (
    <>
      <div className="row" style={{ flex: 1 }}>
        <Pane title="Security posture" className="grow" contentClassName="posture">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: '100%' }}>
            <div className="accent bold" style={{ fontSize: 64, lineHeight: '64px' }}>
              {modes.enforcing}/{modes.total}
            </div>
            <div style={{ textAlign: 'right' }}>
              <div className="accent bold" style={{ fontSize: 20 }}>
                checks enforcing
              </div>
              <div>{modes.on}/{modes.total} checks on</div>
              <div>profile {m.active_profile}</div>
              {health.data && (
                <div className={health.data.jev === 'up' ? '' : 'accent'}>
                  jev remote {health.data.jev}
                  {health.data.jev === 'down' && ` · fallback ${health.data.fallback}`}
                </div>
              )}
              <div className="dim">
                overhead p50/p95 {m.overhead_ms.p50.toFixed(0)}/{m.overhead_ms.p95.toFixed(0)}ms
              </div>
            </div>
          </div>
        </Pane>
        <Pane title="Request counts" style={{ width: '34%' }}>
          <table>
            <thead>
              <tr>
                <th>metric</th>
                <th className="num">count</th>
                <th className="num">share</th>
                <th>of requests</th>
              </tr>
            </thead>
            <tbody>
              {counters.map(([label, n, cls]) => (
                <tr key={label}>
                  <td className={cls}>{label}</td>
                  <td className="num bold">{n}</td>
                  <td className={`num ${cls}`}>{(share(n) * 100).toFixed(0)}%</td>
                  <td className={cls}>
                    <TextBar value={share(n)} width={8} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="dim" style={{ marginTop: 8 }}>
            tokens {m.tokens_total} · cost ${m.cost_total.toFixed(4)}
          </div>
        </Pane>
      </div>

      <div className="row" style={{ flex: 1 }}>
        <Pane title={`Threats · last ${range.label}`} className="grow">
          <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', height: '100%', minHeight: 90 }}>
            {bins.map((b, i) => (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-end', height: '100%' }}>
                <div style={{ width: 14, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', flex: 1 }}>
                  <div title={`flagged ${b.flagged}`} style={{ background: 'var(--fg-dim)', height: `${(b.flagged / binMax) * 100}%` }} />
                  <div title={`redacted ${b.redacted}`} style={{ background: 'var(--accent-dim)', height: `${(b.redacted / binMax) * 100}%` }} />
                  <div title={`blocked ${b.blocked}`} style={{ background: 'var(--accent)', height: `${(b.blocked / binMax) * 100}%` }} />
                </div>
                <div className="dim" style={{ fontSize: 11 }}>
                  {i % 2 === 1 || i === BINS - 1 ? binLabel(i, range) : ' '}
                </div>
              </div>
            ))}
          </div>
        </Pane>
        <Pane title="Blocks by check" className="grow">
          {blocks.length === 0 && <div className="dim">no blocks in window</div>}
          <table>
            <tbody>
              {blocks.map(([check, n]) => (
                <tr key={check}>
                  <td style={{ paddingLeft: 0 }}>{check}</td>
                  <td className="accent">
                    <TextBar value={n / blockMax} width={12} /> <span style={{ color: 'var(--fg)' }}>{n}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Pane>
        <Pane title="OWASP LLM Top 10" className="grow">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2px 16px' }}>
            {OWASP.map((o) => {
              const levels = o.checks.map(modes.level)
              const mark = levels.includes('on') ? '✓' : levels.includes('monitor') ? '~' : ' '
              return (
                <div key={o.id} title={o.checks.join(', ') || 'not covered by any check'}>
                  <span className={mark === '✓' ? 'bright' : mark === '~' ? 'accent' : 'dim'}>{mark === ' ' ? '–' : mark}</span>{' '}
                  <span className={mark === ' ' ? 'dim' : ''}>
                    {o.id}:{o.label}
                  </span>
                </div>
              )
            })}
          </div>
        </Pane>
      </div>

      <div className="row" style={{ flex: 1 }}>
        <Pane title="Recent threats" className="grow">
          <Status error={feed.error} loading={feed.loading && !feed.data} />
          {threats.length === 0 && feed.data && <div className="dim">no threats in window</div>}
          {threats.map((r, i) => (
            <div key={`${r.ts}-${i}`} style={{ display: 'flex', gap: 8, whiteSpace: 'nowrap', overflow: 'hidden' }}>
              <span className="dim">{fmtTime(r.ts)}</span>
              <span style={{ width: 64, textAlign: 'center' }}>
                <ActionTag action={r.action} />
              </span>
              <span className="accent-dim">{r.check}</span>
              <span className="dim">{r.caller_id ?? 'sys'}</span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.reason}</span>
              {r.score > 0 && <span className="accent">{r.score.toFixed(2)}</span>}
            </div>
          ))}
        </Pane>
        <div className="col" style={{ width: 380 }}>
          <Pane title="Latency · p50 / p95 (ms)" className="grow">
            {latency.map(([check, p]) => (
              <div key={check} style={{ display: 'flex', gap: 8 }}>
                <span className="dim" style={{ width: 120 }}>
                  {check}
                </span>
                <TextBar value={p.p95 / latencyMax} width={10} className={check === 'jev' ? 'accent' : 'dim'} />
                <span>
                  {p.p50.toFixed(0)}/{p.p95.toFixed(0)}
                </span>
              </div>
            ))}
          </Pane>
          <Pane title="Budget today" className="grow">
            {Object.entries(m.budget_by_caller).map(([caller, b]) => {
              const used = b.tokens_limit ? b.tokens_today / b.tokens_limit : 0
              return (
                <div key={caller} style={{ display: 'flex', gap: 8 }}>
                  <span className="dim" style={{ width: 120 }}>
                    {caller}
                  </span>
                  <TextBar value={used} width={10} className={used > 0.8 ? 'accent' : ''} />
                  <span>
                    {b.tokens_today}/{b.tokens_limit || '∞'}
                  </span>
                </div>
              )
            })}
          </Pane>
        </div>
      </div>
    </>
  )
}
