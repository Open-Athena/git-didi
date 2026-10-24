import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { boolParam, stringParam, useUrlState } from 'use-prms'
import { ClassChip, Sha, Subject } from './bits'
import { Compare, useServeInfo } from './Compare'
import { Detail } from './Detail'
import { PairTracks } from './PairTracks'
import { useWidth } from './useWidth'
import { GitDag, LaneChip, plural } from './GitDag'
import { buildDag, tone, type Class, type Lane, type Report } from './report'

type Theme = 'auto' | 'light' | 'dark'

function useTheme(): [Theme, () => void, boolean] {
  const [theme, setTheme] = useState<Theme>(() => { try { return (localStorage.getItem('git-didi:theme') as Theme) || 'auto' } catch { return 'auto' } })
  const media = useMemo(() => window.matchMedia('(prefers-color-scheme: dark)'), [])
  const [sysDark, setSysDark] = useState(media.matches)
  useEffect(() => {
    const f = (e: MediaQueryListEvent) => setSysDark(e.matches)
    media.addEventListener('change', f)
    return () => media.removeEventListener('change', f)
  }, [media])
  useEffect(() => {
    if (theme === 'auto') delete document.documentElement.dataset.theme
    else document.documentElement.dataset.theme = theme
    try { localStorage.setItem('git-didi:theme', theme) } catch { /* private mode */ }
  }, [theme])
  const next = () => setTheme(t => t === 'auto' ? 'light' : t === 'light' ? 'dark' : 'auto')
  return [theme, next, theme === 'dark' || (theme === 'auto' && sysDark)]
}

async function fetchReport(url: string): Promise<Report> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} fetching ${url}`)
  return res.json()
}

export function App() {
  const [url, setUrl] = useUrlState('r', stringParam())
  const [item, setItem] = useUrlState('item', stringParam())
  const [pair, setPair] = useUrlState('pair', stringParam())
  const [left, setLeft] = useUrlState('left', stringParam())
  const [right, setRight] = useUrlState('right', stringParam())
  const [level, setLevel] = useUrlState('n', stringParam())
  const [cmp, setCmp] = useUrlState('cmp', boolParam)
  const info = useServeInfo().data
  // Under `git-didi serve` with a lanes config, the repo's own report is the default
  const src = url ?? (info?.report ? '/api/report' : undefined)
  const onCompare = (l: string, r: string, n?: string) => {
    setLeft(l || undefined); setRight(r || undefined); setLevel(n); setCmp(!l || !r); setItem(undefined)
  }
  const [dropped, setDropped] = useState<{ name: string, report: Report } | null>(null)
  const [dragging, setDragging] = useState(false)
  const [theme, nextTheme, dark] = useTheme()
  const q = useQuery({ queryKey: ['report', src], queryFn: () => fetchReport(src!), enabled: !!src && !dropped })
  const client = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const refresh = async () => {
    setRefreshing(true)
    try { client.setQueryData(['report', src], await fetchReport('/api/report?refresh')) } finally { setRefreshing(false) }
  }
  const report = dropped?.report ?? q.data

  const load = (file: File) => file.text().then(t => setDropped({ name: file.name, report: JSON.parse(t) }))
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) load(f)
  }

  return (
    <div className={`app${dragging ? ' dragging' : ''}`} onDragOver={e => { e.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
      <header>
        <h1><a href="https://github.com/runsascoded/git-didi" target="_blank" rel="noreferrer">git-didi</a></h1>
        {info && <span className="serve-chip">serve · {info.repo}</span>}
        {info?.report && !url && !dropped && <button onClick={refresh} disabled={refreshing}>{refreshing ? 'rebuilding…' : 'refresh report'}</button>}
        {report && <span className="dim">{dropped ? dropped.name : src} · generated {report.generated.slice(0, 16).replace('T', ' ')}{report.since && <> · since {report.since}</>}</span>}
        <button className="theme" onClick={nextTheme} aria-label="cycle theme">{theme === 'auto' ? '◐ auto' : theme === 'light' ? '☀ light' : '☾ dark'}</button>
      </header>
      {cmp || left || right || (info && !report && !q.isLoading) ? (
        <>
          {report && <nav className="views"><button onClick={() => { setLeft(undefined); setRight(undefined); setLevel(undefined); setCmp(false) }}>← report</button></nav>}
          <Compare info={info!} left={left} right={right} level={level} onChange={onCompare} />
        </>
      ) : !report && info?.report && !url && q.isLoading ? (
        <p className="landing">Building <code>{info.repo}</code>'s report (the first build checks every pick and merge; later ones are incremental)…</p>
      ) : !report ? (
        <Landing loading={q.isLoading} error={q.error?.message} onUrl={u => { setDropped(null); setUrl(u) }} onFile={load} />
      ) : (
        <Loaded report={report} dark={dark} item={item} setItem={setItem} pair={pair} setPair={setPair} onCompare={info ? onCompare : undefined} />
      )}
    </div>
  )
}

function Landing({ loading, error, onUrl, onFile }: { loading: boolean, error?: string, onUrl: (u: string) => void, onFile: (f: File) => void }) {
  const [u, setU] = useState('')
  return (
    <div className="landing">
      {loading && <p>Loading report…</p>}
      {error && <p className="error">{error}</p>}
      <p>View a <code>git-didi report</code>: branch lanes with every cherry-pick and merge audited, and diffs of diffs.</p>
      <form onSubmit={e => { e.preventDefault(); if (u) onUrl(u) }}>
        <input value={u} onChange={e => setU(e.target.value)} placeholder="report.json URL (CORS-readable)" size={60} />
        <button type="submit">load</button>
      </form>
      <p>…or drop a report file here, or <label className="file-pick">pick one<input type="file" accept=".json,application/json" onChange={e => e.target.files?.[0] && onFile(e.target.files[0])} /></label>.</p>
      <pre>git-didi report -c didi.yml -s 2026-09-28 -o report.json</pre>
    </div>
  )
}

function Loaded({ report, dark, item, setItem, pair, setPair, onCompare }: {
  report: Report, dark: boolean, item: string | undefined, setItem: (s: string | undefined) => void, pair: string | undefined, setPair: (s: string | undefined) => void,
  onCompare?: (left: string, right: string) => void,
}) {
  const dag = useMemo(() => buildDag(report), [report])
  const lanes = useMemo(() => Object.fromEntries(dag.lanes.map(l => [l.id, l])), [dag])
  const [ref, width] = useWidth<HTMLDivElement>()
  useEffect(() => {
    if (item) document.querySelector('.detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [item])
  return (
    <>
      <section ref={ref} className="lanes">
        <GitDag dag={dag} width={width} dark={dark} onItem={setItem} />
      </section>
      {item && <Detail report={report} sha={item} lanes={lanes} onClose={() => setItem(undefined)} onCompare={onCompare} />}
      <nav className="views">
        <button className={!pair ? 'on' : undefined} onClick={() => setPair(undefined)}>Audit</button>
        <button className={pair ? 'on' : undefined} onClick={() => setPair(pair ?? report.pairs[0]?.child)} disabled={!report.pairs?.length}>Pair tracks</button>
        {onCompare && <button onClick={() => onCompare('', '')}>Compare ranges</button>}
      </nav>
      {pair
        ? <PairTracks report={report} child={pair.split(',')[0]} lanes={lanes} onItem={setItem} onPair={setPair} />
        : <Audit report={report} lanes={lanes} onItem={setItem} onPair={setPair} />}
    </>
  )
}

function counts(cs: Class[]): ReactNode {
  const by = new Map<Class, number>()
  for (const c of cs) by.set(c, (by.get(c) ?? 0) + 1)
  return [...by].sort((a, b) => b[1] - a[1]).map(([c, n]) => <span key={c} className="count"><ClassChip c={c} /> {n}</span>)
}

// Per lane: picks received and parent merges, by class; the non-equal ones listed, each opening its detail
function Audit({ report, lanes, onItem, onPair }: { report: Report, lanes: Record<string, Lane>, onItem: (sha: string) => void, onPair: (child: string) => void }) {
  const picks = Object.values(report.picks), merges = Object.values(report.merges)
  return (
    <section className="audit">
      <h3>Audit</h3>
      <table>
        <thead><tr><th>lane</th><th>picks received</th><th>parent merges</th><th>differ</th></tr></thead>
        <tbody>
          {report.lanes.map(l => {
            const ps = picks.filter(p => p.lane === l.name), ms = merges.filter(m => m.lane === l.name)
            const off = [...ps.map(p => ({ sha: p.commit, cls: p.class as Class, subject: p.subject, date: p.date, kind: 'pick' })),
              ...ms.map(m => ({ sha: m.sha, cls: m.class as Class, subject: m.subject, date: m.date, kind: 'merge' }))]
              .filter(x => tone(x.cls) === 'loud').sort((a, b) => b.date.localeCompare(a.date))
            return (
              <tr key={l.name}>
                <td>
                  <LaneChip l={lanes[l.name]} dark={false} />
                  {l.parent && <div><button className="link" onClick={() => onPair(l.name)}>pair tracks ↔ {l.parent}</button></div>}
                </td>
                <td>{ps.length ? counts(ps.map(p => p.class)) : <span className="dim">none</span>}</td>
                <td>{ms.length ? counts(ms.map(m => m.class)) : <span className="dim">none</span>}</td>
                <td>
                  {off.length > 0 && (
                    <details className="off">
                      <summary>{plural(off.length, 'item')}</summary>
                      <ul>
                        {off.map(x => (
                          <li key={x.sha} className="clickable" onClick={() => onItem(x.sha)}>
                            <Sha repo={lanes[l.name].repo} sha={x.sha} /> <ClassChip c={x.cls} /> <span className="dim">{x.kind} · {x.date.slice(0, 10)}</span> <Subject s={x.subject} />
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}
