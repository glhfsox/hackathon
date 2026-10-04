import { getAudit, getHealth, getMetrics } from '../api/client'
import type { Metrics, Mode, TimelineBucket } from '../api/types'
import { ActionTag, Pane, Status, TextBar } from '../components/ui'
import { fmtTime, usePolled } from '../hooks'
import { sinceIso, type TimeRange } from '../ranges'

const POLL_MS = 5000
const BINS = 12

const enforces = (m: Mode) => m === 'block' || m === 'redact'

function checkModes(metrics: Metrics) {
  const ids = Object.keys(metrics.enabled_checks)
  const modes = (id: string) => Object.values(metrics.enabled_checks[id] ?? {})
  return {
    total: ids.length,
    on: ids.filter((id) => modes(id).some((m) => m !== 'off')).length,
    enforcing: ids.filter((id) => modes(id).some(enforces)).length,

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
    <div className="overview-workspace">
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
              <div>jev threshold {m.jev_threshold}</div>
              {health.data && (
                <div className={health.data.jev === 'up' ? '' : 'accent'}>
                  jev remote {health.data.jev}
                  {health.data.jev === 'down' && ` · fallback ${health.data.fallback}`}
                </div>
              )}
              <div className="dim">
                Security overhead: median {m.overhead_ms.p50.toFixed(1)} ms · 95th percentile {m.overhead_ms.p95.toFixed(1)} ms
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
        <div className="col" style={{ width: '40%' }}>
          <Pane title="Response time" className="grow">
            <p className="metric-help">Elapsed time in milliseconds for the selected window. Median (p50): half finish within this time. 95th percentile (p95): 95% finish within this time.</p>
            <table className="latency-table">
              <thead><tr><th>Stage</th><th className="num">Median</th><th className="num">95th %</th></tr></thead>
              <tbody>
                {([
                  ['Security before model', m.request_latency_ms.pre_checks],
                  ['Model response', m.request_latency_ms.upstream],
                  ['Security after model', m.request_latency_ms.post_checks],
                  ['Total response', m.request_latency_ms.total],
                ] as const).map(([label, p]) => (
                  <tr key={label}><td className={label === 'Total response' ? 'bright bold' : ''}>{label}</td><td className="num">{p.p50.toFixed(1)} ms</td><td className="num">{p.p95.toFixed(1)} ms</td></tr>
                ))}
              </tbody>
            </table>
            <div className="dim" style={{ marginTop: 8 }}>Individual security checks</div>
            <table className="latency-table">
              <thead><tr><th>Check</th><th className="num">Median</th><th className="num">95th %</th></tr></thead>
              <tbody>{latency.map(([check, p]) => (
                <tr key={check}><td>{check}</td><td className="num">{p.p50.toFixed(1)} ms</td><td className="num">{p.p95.toFixed(1)} ms</td></tr>
              ))}</tbody>
            </table>
          </Pane>
          <Pane title="Usage today" className="grow">
            <p className="metric-help">Today's tokens and cost per user. Budget limits are unavailable in this report.</p>
            {Object.keys(m.budget_by_caller).length === 0 && <div className="dim">No usage recorded today.</div>}
            <table>
              <thead><tr><th>User</th><th className="num">Tokens</th><th className="num">Cost</th></tr></thead>
              <tbody>{Object.entries(m.budget_by_caller).map(([caller, b]) => (
                <tr key={caller}><td>{caller}</td><td className="num">{b.tokens_today.toLocaleString()}</td><td className="num">${b.cost_today.toFixed(4)}</td></tr>
              ))}</tbody>
            </table>
          </Pane>
        </div>
      </div>
    </div>
  )
}
