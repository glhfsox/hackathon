import { useEffect, useState } from 'react'
import { getHealth } from './api/client'
import { useClock, usePolled } from './hooks'
import AuditLog from './pages/AuditLog'
import Overview from './pages/Overview'
import PolicyEditor from './pages/PolicyEditor'
import Playground from './pages/Playground'
import { RANGES, type TimeRange } from './ranges'
import TimeFilter from './components/TimeFilter'
import SplitPane from './components/SplitPane'

const PAGES = [
  { id: 'overview', tab: '1:overview', file: 'overview' },
  { id: 'audit', tab: '2:audit', file: 'audit.log' },
  { id: 'policy', tab: '3:policy', file: 'policy.yaml' },
  { id: 'playground', tab: '4:playground', file: 'playground' },
] as const
type PageId = (typeof PAGES)[number]['id']

function pageFromHash(): PageId {
  const id = window.location.hash.slice(1)
  return PAGES.find((p) => p.id === id)?.id ?? 'overview'
}

export default function App() {
  const [page, setPage] = useState<PageId>(pageFromHash)
  const [range, setRange] = useState<TimeRange>({ minutes: RANGES['24h'], label: '24h' })
  const clock = useClock()
  const health = usePolled(getHealth, 5000)

  useEffect(() => {
    const onHash = () => setPage(pageFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const h = health.data

  return (
    <div className="app">
      <header className="tabbar">
        <nav>
          {PAGES.map((p) => (
            <a key={p.id} href={`#${p.id}`} className={`tab ${p.id === page ? 'active' : ''}`}>
              {p.id === page ? `[${p.tab}]` : p.tab}
            </a>
          ))}
        </nav>
        <div className="accent">
          aegis@localhost <span className="dim">{clock}</span>
        </div>
      </header>

      <SplitPane className="body" label="Resize navigation" initial={0.17} minFirst={140} minSecond={360} first={
        <aside className="tree" aria-label="Navigation">
          <div className="dim" style={{ padding: '0 8px 4px' }}>
            ▾ aegis/
          </div>
          {PAGES.map((p) => (
            <a key={p.id} href={`#${p.id}`} className={p.id === page ? 'active' : ''}>
              {'  '}
              {p.file}
            </a>
          ))}
          <div className="fill" />
          <div className="dim" style={{ padding: '0 8px' }}>
            ~<br />~<br />~
          </div>
        </aside>
      } second={
        <main className="main">
          {page === 'overview' && <Overview range={range} />}
          {page === 'audit' && <AuditLog range={range} />}
          {page === 'policy' && <PolicyEditor onSaved={health.reload} />}
          {page === 'playground' && <Playground />}
        </main>
      } />

      <footer className="statusline">
        <div>
          <span className="rev">NORMAL</span>
          <span>aegis://{PAGES.find((p) => p.id === page)?.file}</span>
          <span className="dim">{h?.policy_version ?? '-'}</span>
        </div>
        <div>
          {health.error ? (
            <span className="accent bold">backend: down▼</span>
          ) : (
            <>
              <span>
                jev:<span className={h?.jev === 'up' ? 'bright' : 'accent bold'}>{h ? `${h.jev}${h.jev === 'up' ? '▲' : '▼'}` : '-'}</span>
              </span>
              <span>
                fallback:
                <span className={h?.fallback === 'up' ? 'bright' : 'accent bold'}>
                  {h ? `${h.fallback}${h.fallback === 'up' ? '▲' : '▼'}` : '-'}
                </span>
              </span>
            </>
          )}
          <span className="dim">utf-8 | {clock}</span>
        </div>
      </footer>
      {(page === 'overview' || page === 'audit') && <TimeFilter range={range} onChange={setRange} />}
    </div>
  )
}
