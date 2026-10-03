import { useEffect, useState } from 'react'
import { getHealth } from './api/client'
import { useClock, usePolled } from './hooks'
import AuditLog from './pages/AuditLog'
import Overview from './pages/Overview'
import PolicyEditor from './pages/PolicyEditor'
import Playground from './pages/Playground'
import { RANGES, type Range } from './ranges'

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
  const [range, setRange] = useState<Range>('24h')
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

      <div className="body">
        <aside className="tree">
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

        <main className="main">
          {page === 'overview' && <Overview range={range} />}
          {page === 'audit' && <AuditLog />}
          {page === 'policy' && <PolicyEditor onSaved={health.reload} />}
          {page === 'playground' && <Playground />}
        </main>
      </div>

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
      <div className="cmdline">
        <div>
          :filter since=
          {(Object.keys(RANGES) as Range[]).map((r) => (
            <button
              key={r}
              className={`btn ${r === range ? 'accent bold' : ''}`}
              style={{ marginRight: 8 }}
              onClick={() => setRange(r)}
            >
              {r}
            </button>
          ))}
          <span className="cursor blink" />
        </div>
      </div>
    </div>
  )
}
