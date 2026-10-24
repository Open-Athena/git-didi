import { useMemo, useState, type ReactNode } from 'react'
import { ClassChip, Sha, Subject } from './bits'
import { DidiFile } from './Detail'
import { LaneChip, plural } from './GitDag'
import { tone, type Class, type Lane, type MergeRec, type PairRow, type Report } from './report'
import { useWidth } from './useWidth'

// A (child, parent) pair over time: the child's own delta size at each merge (the "intrinsic delta only goes down"
// trend), then the child's first-parent commits, each pick aligned with its parent-side commit. Runs of pairs with an
// empty residual collapse into a band; the rest stay open with their diff of diffs; unpaired commits (the intrinsic
// delta) are grouped by the surfaces they touch.

// Classes whose residual is empty at their own level (the label explains them)
const EMPTY = new Set<Class>(['equal', 'equal@lines', 'equal@context', 'equal@ws', 'absorbed', 'adopted'])

type Group =
  | { t: 'band', rows: PairRow[] }
  | { t: 'open', row: PairRow }
  | { t: 'own', rows: PairRow[] }

export function groupRows(rows: PairRow[]): Group[] {
  const groups: Group[] = []
  for (const row of rows) {
    const last = groups.at(-1)
    if (row.kind === 'own') {
      if (last?.t === 'own') last.rows.push(row)
      else groups.push({ t: 'own', rows: [row] })
    } else if (!row.class || EMPTY.has(row.class)) {
      if (last?.t === 'band') last.rows.push(row)
      else groups.push({ t: 'band', rows: [row] })
    } else {
      groups.push({ t: 'open', row })
    }
  }
  return groups
}

const count = <T,>(xs: T[]): [T, number][] => {
  const m = new Map<T, number>()
  for (const x of xs) m.set(x, (m.get(x) ?? 0) + 1)
  return [...m].sort((a, b) => b[1] - a[1])
}

export function PairTracks({ report, child, lanes, onItem, onPair }: {
  report: Report, child: string, lanes: Record<string, Lane>, onItem: (sha: string) => void, onPair: (child: string) => void,
}) {
  const pair = report.pairs.find(p => p.child === child)
  const groups = useMemo(() => pair ? groupRows(pair.rows) : [], [pair])
  const merges = useMemo(() => Object.values(report.merges).filter(m => m.lane === child).sort((a, b) => a.date.localeCompare(b.date)), [report, child])
  return (
    <section className="pairs">
      <div className="pair-tabs">
        <span className="dim">Pair tracks:</span>
        {report.pairs.map(p => (
          <button key={p.child} className={p.child === child ? 'on' : undefined} onClick={() => onPair(p.child)}>
            <LaneChip l={lanes[p.child]} dark={false} /> <span className="dim">↔ {p.parent}</span>
          </button>
        ))}
      </div>
      {!pair ? <p className="dim">No lane {child} with a parent in this report.</p> : (
        <>
          <PairSummary rows={pair.rows} child={lanes[pair.child]} parent={lanes[pair.parent]} />
          {merges.length > 1 && <Trend merges={merges} onItem={onItem} />}
          <div className="tracks">
            <div className="track-head"><LaneChip l={lanes[pair.parent]} dark={false} /><span /><LaneChip l={lanes[pair.child]} dark={false} /></div>
            {groups.map((g, i) => <GroupView key={i} g={g} report={report} child={lanes[pair.child]} parent={lanes[pair.parent]} onItem={onItem} />)}
          </div>
        </>
      )}
    </section>
  )
}

function PairSummary({ rows, child, parent }: { rows: PairRow[], child: Lane, parent: Lane }) {
  const down = rows.filter(r => r.kind === 'pick' && r.dir === 'down'), up = rows.filter(r => r.kind === 'pick' && r.dir === 'up')
  const own = rows.filter(r => r.kind === 'own'), merges = rows.filter(r => r.kind === 'merge')
  const parity = own.filter(r => r.surfaces?.includes('parity'))
  const viaPid = rows.filter(r => r.via === 'patch-id').length
  return (
    <p className="explain">
      <code>{child.id}</code>'s first-parent history: {plural(merges.length, 'merge')} from <code>{parent.id}</code>,{' '}
      {plural(down.length, 'pick')} from it, {plural(up.length, 'commit')} it picked{viaPid > 0 && <> ({viaPid} paired by patch-id: their trailer was lost)</>},
      and {plural(own.length, 'intrinsic commit')}{parity.length > 0 && <>, <span className="warn">{parity.length} touching parity surfaces</span></>}.
    </p>
  )
}

// The own delta's size (changed lines) before and after each merge: drops are what merges absorb, rises are new own work
function Trend({ merges, onItem }: { merges: MergeRec[], onItem: (sha: string) => void }) {
  const [metric, setMetric] = useState<'lines' | 'files'>('lines')
  const val = (s: MergeRec['before']) => metric === 'lines' ? s.added + s.removed : s.files
  const [ref, w] = useWidth<HTMLDivElement>()
  const h = 120, pad = { l: 56, r: 10, t: 10, b: 18 }
  const t = (d: string) => new Date(d).getTime()
  const t0 = t(merges[0].date), t1 = t(merges.at(-1)!.date)
  const vs = merges.flatMap(m => [val(m.before), val(m.after)])
  const lo = Math.min(...vs), hi = Math.max(...vs)
  const x = (d: string) => pad.l + (t1 === t0 ? 0 : (t(d) - t0) / (t1 - t0)) * (w - pad.l - pad.r)
  const y = (v: number) => pad.t + (hi === lo ? 0.5 : 1 - (v - lo) / (hi - lo)) * (h - pad.t - pad.b)
  const path = merges.map((m, i) => `${i ? 'L' : 'M'}${x(m.date)},${y(val(m.before))} L${x(m.date)},${y(val(m.after))}`).join(' ')
  return (
    <div className="trend" ref={ref}>
      <div className="trend-head">
        <span>Own delta at each merge ({metric === 'lines' ? 'changed lines' : 'files'}): a drop is what the merge absorbed</span>
        <span className="seg">{(['lines', 'files'] as const).map(m => <button key={m} className={m === metric ? 'on' : undefined} onClick={() => setMetric(m)}>{m}</button>)}</span>
      </div>
      <svg width={w} height={h}>
        <text x={pad.l - 6} y={y(hi) + 4} textAnchor="end" fontSize={10} className="axis">{hi.toLocaleString()}</text>
        <text x={pad.l - 6} y={y(lo) + 4} textAnchor="end" fontSize={10} className="axis">{lo.toLocaleString()}</text>
        <text x={pad.l} y={h - 4} fontSize={10} className="axis">{merges[0].date.slice(0, 10)}</text>
        <text x={w - pad.r} y={h - 4} fontSize={10} textAnchor="end" className="axis">{merges.at(-1)!.date.slice(0, 10)}</text>
        <path d={path} fill="none" className="trend-line" strokeWidth={1.5} />
        {merges.map(m => (
          <circle key={m.sha} cx={x(m.date)} cy={y(val(m.after))} r={3.5} className={`trend-dot ${tone(m.class)}`} onClick={() => onItem(m.sha)}>
            <title>{`${m.sha.slice(0, 10)} ${m.class}: ${val(m.before)} → ${val(m.after)}`}</title>
          </circle>
        ))}
      </svg>
    </div>
  )
}

function Row({ row, child, parent, onItem, body }: { row: PairRow, child: Lane, parent: Lane, onItem: (sha: string) => void, body?: ReactNode }) {
  const left = row.kind === 'pick' ? <Sha repo={parent.repo} sha={row.parent_sha!} />
    : row.kind === 'merge' ? <span className="dim">merge ⤵</span>
    : row.from_lane ? <span className="dim">pick from {row.from_lane}</span> : null
  const arrow = row.kind === 'pick' ? (row.dir === 'down' ? '→' : '←') : ''
  const rec = recordSha(row)
  return (
    <div className={`track-row ${row.kind}`}>
      <span className="track-l">{left}</span>
      <span className="track-c">
        {arrow && <span className="dim">{arrow}</span>}
        {row.class && <ClassChip c={row.class} />}
        {row.via === 'patch-id' && <span className="via dim">patch-id</span>}
      </span>
      <span className="track-r">
        <Sha repo={child.repo} sha={row.sha} />
        <span className="time dim">{row.date.slice(0, 10)}</span>
        {row.surfaces?.includes('parity') && <span className="warn" title="touches a parity surface">⚠</span>}
        {row.surfaces && <span className="surfaces dim">{row.surfaces.join(' · ')}</span>}
        <span className={rec ? 'clickable-subject' : undefined} onClick={rec ? () => onItem(rec) : undefined}><Subject s={row.subject} /></span>
        {body}
      </span>
    </div>
  )
}

function GroupView({ g, report, child, parent, onItem }: { g: Group, report: Report, child: Lane, parent: Lane, onItem: (sha: string) => void }) {
  const [open, setOpen] = useState(false)
  if (g.t === 'open') {
    const r = g.row
    const rec = recordSha(r)
    const files = r.kind === 'merge' ? undefined : rec ? report.picks[rec]?.files : r.files
    const shown = (files ?? []).filter(f => f.didi?.length)
    return (
      <div className="group-open">
        <Row row={r} child={child} parent={parent} onItem={onItem} body={r.kind === 'merge' && <MergeNote m={report.merges[r.sha]} onItem={onItem} />} />
        {shown.map(f => <div key={f.path} className="track-files"><DidiFile f={f} refs={pairRefs(r, child, parent)} /></div>)}
      </div>
    )
  }
  if (g.t === 'band') {
    const picks = g.rows.filter(r => r.kind === 'pick'), merges = g.rows.filter(r => r.kind === 'merge')
    const label = [picks.length && plural(picks.length, 'pick'), merges.length && plural(merges.length, 'merge')].filter(Boolean).join(', ')
    return (
      <div className="band">
        <button className="band-head" onClick={() => setOpen(!open)}>
          <span className="caret">{open ? '▾' : '▸'}</span> {label}, empty residual{' '}
          <span className="dim">({count(g.rows.map(r => r.class ?? 'equal')).map(([c, n]) => `${n} ${c}`).join(', ')}) · {g.rows[0].date.slice(0, 10)} – {g.rows.at(-1)!.date.slice(0, 10)}</span>
        </button>
        {open && g.rows.map(r => <Row key={r.sha} row={r} child={child} parent={parent} onItem={onItem} />)}
      </div>
    )
  }
  const surfaces = count(g.rows.flatMap(r => r.surfaces ?? []))
  const parity = g.rows.filter(r => r.surfaces?.includes('parity'))
  return (
    <div className="band own">
      <button className="band-head" onClick={() => setOpen(!open)}>
        <span className="caret">{open ? '▾' : '▸'}</span> {plural(g.rows.length, 'intrinsic commit')}
        {surfaces.length > 0 && <span className="dim"> · {surfaces.map(([s, n]) => `${s} ${n}`).join(', ')}</span>}
        {parity.length > 0 && <span className="warn"> · ⚠ {parity.length} touch parity</span>}
      </button>
      {(open ? g.rows : parity).map(r => <Row key={r.sha} row={r} child={child} parent={parent} onItem={onItem} />)}
    </div>
  )
}

function MergeNote({ m, onItem }: { m?: MergeRec, onItem: (sha: string) => void }) {
  if (!m) return null
  const off = m.files.filter(f => !EMPTY.has(f.label as Class))
  return (
    <span className="merge-note dim">
      {' · '}{m.before.added + m.before.removed} → {m.after.added + m.after.removed} lines; {plural(off.length, 'file')} with a residual{' '}
      <button onClick={() => onItem(m.sha)}>open</button>
    </span>
  )
}

// The report record behind a row: trailer picks are keyed by the pick commit (the parent's, for `up`); merges by SHA;
// patch-id pairs have none (their files ride on the row)
export function recordSha(r: PairRow): string | null {
  if (r.kind === 'merge') return r.sha
  if (r.kind === 'pick' && r.via === 'trailer') return r.dir === 'up' ? r.parent_sha! : r.sha
  return null
}

function pairRefs(r: PairRow, child: Lane, parent: Lane) {
  const src = r.dir === 'up' ? { repo: child.repo, post: r.sha } : { repo: parent.repo ?? child.repo, post: r.parent_sha! }
  const dst = r.dir === 'up' ? { repo: parent.repo ?? child.repo, post: r.parent_sha! } : { repo: child.repo, post: r.sha }
  // trailer picks compare source (left) vs pick (right); patch-id pairs compare parent (left) vs child (right)
  if (r.via === 'patch-id') return { left: { repo: parent.repo ?? child.repo, post: r.parent_sha! }, right: { repo: child.repo, post: r.sha } }
  return { left: src, right: dst }
}
