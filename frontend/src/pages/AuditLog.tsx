import { useState } from 'react'
import SplitPane from '../components/SplitPane'
import { sinceIso, type TimeRange } from '../ranges'
import { getAudit } from '../api/client'
import { CHECKPOINTS, type AuditRecord } from '../api/types'
import { ActionTag, Pane, Status, TextBar } from '../components/ui'
import { fmtTime, shortId, usePolled } from '../hooks'

const PAGE_SIZE = 300
const POLL_MS = 5000

function Trace({ requestId, rows }: { requestId: string; rows: AuditRecord[] }) {
  const own = rows.filter((r) => r.request_id === requestId)
  const summary = own.filter((r) => r.check === 'turn_summary')
  const checks = own.filter((r) => r.check !== 'turn_summary')
  return (
    <div style={{ whiteSpace: 'pre', fontSize: 12 }}>
      <div className="dim">// decision trace for request_id: {requestId}</div>
      {CHECKPOINTS.map((cp) => {
        const results = checks.filter((r) => r.checkpoint === cp).reverse()
        const sum = summary.find((r) => r.checkpoint === cp)
        if (!results.length && !sum) {
          return (
            <div key={cp} className="dim" style={{ marginTop: 8 }}>
              ▸ {cp} — not reached
            </div>
          )
        }
        const action = sum?.action ?? 'allow'
        return (
          <div key={cp} style={{ marginTop: 8 }}>
            <div>
              ▾ <span className={action === 'allow' ? 'bright' : 'accent'}>{cp}</span>{' '}
              <span className={action === 'allow' ? 'dim' : 'accent'}>{action.toUpperCase()}</span>{' '}
              {sum && <span className="dim">({sum.latency_ms.toFixed(0)}ms)</span>}
            </div>
            {results.map((r, i) => (
              <div key={i}>
                {'  '}
                {i === results.length - 1 ? '└─ ' : '├─ '}
                <span className={r.action === 'allow' ? '' : 'accent bold'}>{r.check}</span>: <ActionTag action={r.action} />{' '}
                <span className="dim">
                  {r.score.toFixed(2)} · {r.latency_ms.toFixed(1)}ms · {r.decided_by}
                </span>
                {r.action !== 'allow' && (
                  <div style={{ whiteSpace: 'pre-wrap', paddingLeft: 40 }} className="dim">
                    └─ {r.reason}
                  </div>
                )}
              </div>
            ))}
            {sum && sum.tokens > 0 && (
              <div className="dim">
                {'  '}tokens {sum.tokens} · cost ${sum.cost.toFixed(4)}
                {sum.upstream_latency_ms !== null && ` · upstream ${sum.upstream_latency_ms.toFixed(0)}ms`}
              </div>
            )}
          </div>
        )
      })}
      {own.length === 0 && <div className="dim">rows of this request are outside the loaded page</div>}
    </div>
  )
}

export default function AuditLog({ range }: { range: TimeRange }) {
  const [selection, setSelected] = useState<{ id: string; key: string } | null>(null)
  const queryKey = String(range.minutes)
  const audit = usePolled(() => getAudit({ since: sinceIso(range) }, PAGE_SIZE), POLL_MS, queryKey)
  const selected = selection?.key === queryKey ? selection.id : null

  const items = audit.data?.items ?? []
  const visible = items.filter((r) => r.check !== 'turn_summary')
  const total = audit.data?.total ?? 0

  return (
    <>
      <SplitPane className="grow" label="Resize audit trace" initial={0.65} minFirst={260} minSecond={200} first={
        <Pane title={`Audit log (${total})`} className="grow" contentClassName="flush">
          <Status error={audit.error} loading={audit.loading && !audit.data} />
          <table className="log" style={{ fontSize: 12.5 }}>
            <thead>
              <tr>
                <th className="num">ln</th>
                <th>ts</th>
                <th>request_id</th>
                <th>caller</th>
                <th>checkpoint</th>
                <th>check</th>
                <th>action</th>
                <th>score</th>
                <th className="num">ms</th>
                <th>decided_by</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r, i) => {
                const system = r.checkpoint === null
                return (
                  <tr
                    key={`${r.ts}-${i}`}
                    className={r.request_id && r.request_id === selected ? 'sel' : ''}
                    onClick={() => r.request_id && setSelected({ id: r.request_id, key: queryKey })}
                    title={r.reason}
                  >
                    <td className={`num ${system ? 'accent' : 'dim'}`}>{system ? '!' : total - items.indexOf(r)}</td>
                    <td className={system ? 'accent' : ''}>{fmtTime(r.ts)}</td>
                    <td className="bright">{shortId(r.request_id)}</td>
                    <td className={system ? 'accent' : ''}>{r.caller_id ?? 'sys'}</td>
                    <td className="dim">{r.checkpoint ?? '-'}</td>
                    <td className={system ? 'accent' : ''}>{r.check}</td>
                    <td>{system ? <span className="accent">{r.action}</span> : <ActionTag action={r.action} />}</td>
                    <td>
                      {system ? (
                        '-'
                      ) : (
                        <>
                          {r.score.toFixed(2)} <TextBar value={r.score} width={5} className={r.score >= 0.7 ? 'accent' : 'bright'} />
                        </>
                      )}
                    </td>
                    <td className="num dim">{r.latency_ms.toFixed(1)}</td>
                    <td className={r.decided_by === 'rules' ? 'dim' : 'bright'}>{r.decided_by}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {audit.data && visible.length === 0 && (
            <div className="dim" style={{ padding: 16 }}>
              no rows match
            </div>
          )}
        </Pane>
      } second={
        <Pane title={selected ? `Decision trace ${shortId(selected)}` : 'Decision trace'} className="grow" style={{ background: '#000' }}>
          {selected ? <Trace requestId={selected} rows={items} /> : <div className="dim">// select a row to see its decision trace</div>}
        </Pane>
      } />
    </>
  )
}
