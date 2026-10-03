import { useEffect, useState } from 'react'
import { getHealth } from './api/client'
import { usePolled } from './hooks'
import AuditLog from './pages/AuditLog'
import Overview from './pages/Overview'
import PolicyEditor from './pages/PolicyEditor'
import Playground from './pages/Playground'
import { RANGES, type TimeRange } from './ranges'
import TimeFilter from './components/TimeFilter'

const PAGES = [
  { id: 'overview', tab: '1:overview' },
  { id: 'audit', tab: '2:audit' },
  { id: 'policy', tab: '3:policy' },
  { id: 'playground', tab: '4:playground' },
] as const
type PageId = (typeof PAGES)[number]['id']

function pageFromHash(): PageId {
  const id = window.location.hash.slice(1)
  return PAGES.find((p) => p.id === id)?.id ?? 'overview'
}

export default function App() {
  const [page, setPage] = useState<PageId>(pageFromHash)
  const [range, setRange] = useState<TimeRange>({ minutes: RANGES['24h'], label: '24h' })
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
        <div className="brand"><span className="brand-name">Alpin</span><span className="brand-caption">AI control layer</span></div>
        <nav aria-label="Main navigation">
          {PAGES.map((p) => (
            <a key={p.id} href={`#${p.id}`} className={`tab ${p.id === page ? 'active' : ''}`} aria-current={p.id === page ? 'page' : undefined}>
              {p.id === page ? `[${p.tab}]` : p.tab}
            </a>
          ))}
        </nav>
      </header>

      <main className="main">
        {page === 'overview' && <Overview range={range} />}
        {page === 'audit' && <AuditLog range={range} />}
        {page === 'policy' && <PolicyEditor onSaved={health.reload} />}
        {page === 'playground' && <Playground />}
      </main>

      <footer className="statusline">
        <div>
          <span className="dim">policy</span>
          <span>{h?.policy_version ?? '—'}</span>
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
        </div>
      </footer>
      {(page === 'overview' || page === 'audit') && <TimeFilter range={range} onChange={setRange} />}
    </div>
  )
}
