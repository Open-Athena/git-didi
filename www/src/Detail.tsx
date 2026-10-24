import { useEffect, useMemo, useState } from 'react'
import { ClassChip, CLASS_TIPS, Copy, Sha, Subject } from './bits'
import { parseDidi, type Row } from './didi'
import { LaneChip, plural } from './GitDag'
import { itemCommand, type FileLabel, type FileRec, type Lane, type MergeRec, type PickRec, type Report } from './report'

// What a file's lines link to on GitHub: per side, the commits holding its pre- and post-image
type SideRefs = { repo?: string, pre?: string, post: string }
export type Refs = { left: SideRefs, right: SideRefs }

// Group order: real differences first (open), explained ones after (folded)
const GROUPS: FileLabel[] = ['differs', 'left-only', 'right-only', 'equal@tokens', 'equal@ws', 'equal@context', 'equal@lines', 'adopted', 'absorbed']
const OPEN = new Set<FileLabel>(['differs', 'equal@tokens', 'equal@ws'])

export function Detail({ report, sha, lanes, onClose, onCompare }: {
  report: Report, sha: string, lanes: Record<string, Lane>, onClose: () => void, onCompare?: (left: string, right: string) => void,
}) {
  const pick = report.picks[sha], merge = report.merges[sha]
  const cmd = itemCommand(report, sha)
  if (!pick && !merge) return <div className="detail"><p>No pick or merge {sha.slice(0, 10)} in this report. <button onClick={onClose}>close</button></p></div>
  return (
    <div className="detail">
      <div className="detail-close"><button onClick={onClose} aria-label="close">×</button></div>
      {pick ? <PickHead p={pick} lanes={lanes} /> : <MergeHead m={merge} lanes={lanes} />}
      {cmd && (
        <div className="cmd">
          <code>{cmd}</code><Copy text={cmd} />
          {onCompare && <button onClick={() => onCompare(...itemRanges(pick, merge))}>compare (all files, full diffs)</button>}
        </div>
      )}
      <Files files={(pick ?? merge).files} counts={merge?.counts} refs={pick ? pickRefs(pick, lanes) : mergeRefs(merge, lanes)} />
    </div>
  )
}

// The two ranges an item compares, as `git-didi patch` args
function itemRanges(pick?: PickRec, merge?: MergeRec): [string, string] {
  if (pick) return [`${pick.source}^..${pick.source}`, `${pick.commit}^..${pick.commit}`]
  return [`${merge!.before.base}..${merge!.before.tip}`, `${merge!.after.base}..${merge!.after.tip}`]
}

const laneOf = (lanes: Record<string, Lane>, id: string): Lane => lanes[id] ?? { id, ref: id, label: id, color: '#888888' }

function pickRefs(p: PickRec, lanes: Record<string, Lane>): Refs {
  const repo = lanes[p.lane]?.repo
  return { left: { repo: lanes[p.from_lane]?.repo ?? repo, post: p.source }, right: { repo, post: p.commit } }
}
function mergeRefs(m: MergeRec, lanes: Record<string, Lane>): Refs {
  const repo = lanes[m.lane]?.repo
  return { left: { repo, pre: m.before.base, post: m.before.tip }, right: { repo, pre: m.after.base, post: m.after.tip } }
}

function PickHead({ p, lanes }: { p: PickRec, lanes: Record<string, Lane> }) {
  const to = laneOf(lanes, p.lane), from = laneOf(lanes, p.from_lane)
  return (
    <div className="detail-head">
      <div className="detail-kind">Cherry-pick <LaneChip l={from} dark={false} /> → <LaneChip l={to} dark={false} /> <ClassChip c={p.class} /></div>
      <h2><Subject s={p.subject} /></h2>
      <div className="detail-sub">
        <Sha repo={to.repo} sha={p.commit} n={10} /> picked from <Sha repo={from.repo ?? to.repo} sha={p.source} n={10} /> · {p.date.slice(0, 16).replace('T', ' ')}
      </div>
      <p className="explain"><Subject s={CLASS_TIPS[p.class]} /></p>
    </div>
  )
}

function MergeHead({ m, lanes }: { m: MergeRec, lanes: Record<string, Lane> }) {
  const to = laneOf(lanes, m.lane), from = laneOf(lanes, m.from_lane)
  const size = (s: MergeRec['before']) => <>{plural(s.files, 'file')} <span className="plus">+{s.added}</span>/<span className="minus">−{s.removed}</span></>
  return (
    <div className="detail-head">
      <div className="detail-kind">Merge <LaneChip l={from} dark={false} /> → <LaneChip l={to} dark={false} /> <ClassChip c={m.class} /></div>
      <h2><Subject s={m.subject} /></h2>
      <div className="detail-sub"><Sha repo={to.repo} sha={m.sha} n={10} /> · {m.date.slice(0, 16).replace('T', ' ')}</div>
      <p className="explain">
        <code>{m.lane}</code>'s own delta, before vs. after merging <code>{m.from_lane}</code>: {size(m.before)} → {size(m.after)}
        {m.absorbed_commits.length > 0 && <> · {plural(m.absorbed_commits.length, 'absorbed pick')}: {m.absorbed_commits.map(c => <Sha key={c} repo={to.repo} sha={c} />)}</>}
      </p>
    </div>
  )
}

export function Files({ files, counts, refs }: { files: FileRec[], counts?: Record<string, number>, refs: Refs }) {
  const by = new Map<FileLabel, FileRec[]>()
  for (const f of files) by.set(f.label, [...by.get(f.label) ?? [], f])
  const equal = counts?.equal ?? 0
  return (
    <div className="files">
      {GROUPS.filter(g => by.has(g)).map(g => (
        <details key={g} open={OPEN.has(g)} className="group">
          <summary><ClassChip c={g} /> {plural(by.get(g)!.length, 'file')}</summary>
          {by.get(g)!.map(f => <DidiFile key={f.path} f={f} refs={refs} />)}
        </details>
      ))}
      {equal > 0 && <div className="group equal-count"><ClassChip c="equal" /> {plural(equal, 'file')} (identical patches, not listed)</div>}
      {files.length === 0 && !equal && <p className="dim">Every file's patch is identical.</p>}
    </div>
  )
}

export function DidiFile({ f, refs }: { f: FileRec, refs: Refs }) {
  const rows = useMemo(() => f.didi ? parseDidi(f.didi) : null, [f.didi])
  const blob = refs.right.repo && f.status !== 'left-only' ? `${refs.right.repo}/blob/${refs.right.post}/${f.path}` : undefined
  return (
    <div className="file">
      <div className="file-head">
        {blob ? <a href={blob} target="_blank" rel="noreferrer" className="mono">{f.left_path ? `${f.left_path} → ` : ''}{f.path}</a> : <span className="mono">{f.path}</span>}
        <span className="dim"> · {f.left_lines} → {f.right_lines} changed lines</span>
        {f.label === 'differs' && f.absorbed_lines! > 0 && (
          <span className="dim"> · {f.absorbed_lines}/{f.residual_lines} residual lines explained</span>
        )}
      </div>
      {rows && <DidiView rows={rows} path={f.path} leftPath={f.left_path ?? f.path} refs={refs} />}
      {f.didi_lines !== undefined && <div className="truncated dim">{f.didi?.length ?? 0} of {f.didi_lines} lines shown; run the command above for the rest</div>}
    </div>
  )
}

function lineHref(r: Extract<Row, { kind: 'line' }>, refs: Refs, path: string, leftPath: string): { n: number, href?: string } | null {
  const side = r.outer === '-' ? 'left' : 'right'
  const nos = side === 'left' ? r.left : r.right
  if (!nos) return null
  const s = refs[side]
  const [n, sha] = r.inner === '-' ? [nos.old, s.pre] : [nos.new, s.post]
  if (n === undefined) return null
  return { n, href: s.repo && sha ? `${s.repo}/blob/${sha}/${side === 'left' ? leftPath : path}#L${n}` : undefined }
}

const SIGN: Record<string, string> = { '-': 'minus', '+': 'plus', ' ': 'ctx' }

export function DidiView({ rows, path, leftPath, refs }: { rows: Row[], path: string, leftPath: string, refs: Refs }) {
  const [wrap, setWrap] = useWrap()
  return (
    <div className="didi-wrap">
      <label className="wrap-toggle"><input type="checkbox" checked={wrap} onChange={e => setWrap(e.target.checked)} /> wrap</label>
      <div className={`didi${wrap ? ' wrap' : ''}`} role="table">
      <div className="didi-rows">
      {rows.map((r, i) => {
        if (r.kind === 'outer-hunk') return <div key={i} className="didi-row outer-hunk"><span className="g" /><span className="g" /><span className="ln" /><span className="t">{r.text}</span></div>
        const outer = SIGN[r.outer]
        if (r.kind !== 'line') {
          return (
            <div key={i} className={`didi-row outer-${outer} ${r.kind}`}>
              <span className="g o">{r.outer}</span><span className="g i" /><span className="ln" /><span className="t">{r.text}</span>
            </div>
          )
        }
        const ln = lineHref(r, refs, path, leftPath)
        return (
          <div key={i} className={`didi-row outer-${outer} inner-${SIGN[r.inner]}`}>
            <span className="g o">{r.outer}</span>
            <span className="g i">{r.inner}</span>
            <span className="ln">{ln && (ln.href ? <a href={ln.href} target="_blank" rel="noreferrer">{ln.n}</a> : ln.n)}</span>
            <span className="t">{r.spans.map((s, j) => <span key={j} className={[s.inner && 'tok-inner', s.outer && 'tok-outer'].filter(Boolean).join(' ') || undefined}>{s.text}</span>)}</span>
          </div>
        )
      })}
      </div>
      </div>
    </div>
  )
}

// Line wrapping in diffs of diffs: on by default, remembered per browser
const WRAP_KEY = 'git-didi:wrap'
const wrapListeners = new Set<(w: boolean) => void>()
function useWrap(): [boolean, (w: boolean) => void] {
  const [wrap, setWrapState] = useState(() => { try { return localStorage.getItem(WRAP_KEY) !== '0' } catch { return true } })
  useEffect(() => {
    wrapListeners.add(setWrapState)
    return () => { wrapListeners.delete(setWrapState) }
  }, [])
  const setWrap = (w: boolean) => {
    try { localStorage.setItem(WRAP_KEY, w ? '1' : '0') } catch { /* private mode */ }
    wrapListeners.forEach(f => f(w))
  }
  return [wrap, setWrap]
}
