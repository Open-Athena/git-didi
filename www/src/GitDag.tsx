import { FloatingPortal } from '@floating-ui/react'
import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ClassChip, Sha, Subject } from './bits'
import { badge, tone, type Dag, type Lane, type Link } from './report'

// A day-binned branch DAG (ported from the disky deck's `GitDag.tsx`): one lane per branch, one column per day. A dot
// is a lane's own first-parent commits that day; picks received that day are wedges in their source lane's colour.
// Arrows are merges (solid) and cherry-picks (dashed), drawn by their didi class: quiet when equal, badged with the
// level when equal only once normalized, outlined when they differ. Hover for a hint below the chart; click a dot or
// arrow for a drawer listing its commits, each opening its detail.

type Sel = { t: 'cell', lane: string, day: string } | { t: 'link', i: number } | { t: 'lane', lane: string }

const { PI, cos, log2, max, min, sin, sqrt } = Math
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
export const fmtDay = (d: string, dow = false) => {
  const t = new Date(`${d}T00:00:00Z`)
  return `${dow ? `${DOW[t.getUTCDay()]} ` : ''}${MON[t.getUTCMonth()]} ${t.getUTCDate()}`
}
export const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`
const luma = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16))
  return 0.299 * r + 0.587 * g + 0.114 * b
}
// a lane colour that reads as text: darkened on light backgrounds, dark greys lifted on dark ones
export const ink = (hex: string, dark: boolean) => {
  if (dark) return luma(hex) < 110 ? '#c8c8c8' : hex
  if (luma(hex) < 135) return hex
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16))
  return `rgb(${[r, g, b].map(c => Math.round(c * 0.62)).join(',')})`
}

export function LaneChip({ l, dark = true }: { l: Lane, dark?: boolean }) {
  return <span className="lane-chip" style={{ color: ink(l.color, dark) }}>{l.label}</span>
}
function ArrowIcon({ kind, color }: { kind: 'merge' | 'cp', color: string }) {
  return (
    <svg width={26} height={10} style={{ flex: 'none' }}>
      <path d="M0,5 H20" stroke={color} strokeWidth={1.8} strokeDasharray={kind === 'cp' ? '3 2.5' : undefined} />
      <path d="M20,1.5 L26,5 L20,8.5 z" fill={color} />
    </svg>
  )
}

export function GitDag({ dag, width, dark, laneH = 40, labelW = 128, onItem }: {
  dag: Dag, width: number, dark: boolean, laneH?: number, labelW?: number, onItem: (sha: string) => void,
}) {
  const { lanes, days, cells, links, forks, prelude } = dag
  const lane = Object.fromEntries(lanes.map(l => [l.id, l]))
  // Lineage tree, left of the labels: one indent step per generation
  const depth = (id: string): number => lane[id]?.parent ? 1 + depth(lane[id].parent!) : 0
  const maxDepth = max(...lanes.map(l => depth(l.id)))
  const treeStep = 9
  const treeW = 8 + treeStep * (maxDepth + 1)
  const treeX = (id: string) => 4 + treeStep * depth(id)
  const preW = prelude ? 64 : 0
  const lx = treeW
  const x0 = lx + labelW + preW
  const colW = max(4, (width - x0 - 10) / max(1, days.length))
  const top = 20
  const laneY = useMemo(() => Object.fromEntries(lanes.map((l, i) => [l.id, top + i * laneH + laneH / 2])), [lanes, laneH])
  const dayX = useMemo(() => Object.fromEntries(days.map((d, i) => [d, x0 + (i + 0.5) * colW])), [days, x0, colW])
  const height = top + lanes.length * laneH + 4

  const [hot, setHot] = useState<{ sel: Sel, hint: ReactNode } | null>(null)
  const [sel, setSel] = useState<Sel | null>(null)
  const hover = (s: Sel, hint: ReactNode) => ({
    onMouseEnter: () => setHot({ sel: s, hint }),
    onMouseLeave: () => setHot(null),
  })
  const on = (s: Sel, hint: ReactNode) => ({
    ...hover(s, hint),
    onClick: (e: React.MouseEvent) => { e.stopPropagation(); setSel(s.t === 'lane' && isSel(s) ? null : s) },
    style: { cursor: 'pointer' },
  })

  // focus = the selected (else hovered) item; everything not touching it dims
  const focus = sel ?? hot?.sel
  const focusKey = (() => {
    if (!focus) return null
    if (focus.t === 'lane') return { lanes: new Set([focus.lane]), day: null as string | null }
    if (focus.t === 'cell') return { lanes: new Set([focus.lane]), day: focus.day as string | null }
    const k = links[focus.i]
    return { lanes: new Set([k.from, k.to]), day: k.day as string | null }
  })()
  const onDay = (d: string) => !focusKey?.day || focusKey.day === d
  const dimCell = (l: string, d: string) => !!focusKey && !(onDay(d) && focusKey.lanes.has(l))
  const dimLink = (i: number) => !!focus && !(focus.t === 'link' ? focus.i === i : focusKey && onDay(links[i].day) && (focusKey.lanes.has(links[i].from) || focusKey.lanes.has(links[i].to)))
  const laneRow = focus?.t === 'lane' ? focus.lane : null
  const litLanes = laneRow ? new Set([laneRow, ...links.filter(k => k.from === laneRow || k.to === laneRow).flatMap(k => [k.from, k.to])]) : null
  const dimLane = (id: string) => !!litLanes && !litLanes.has(id)

  const r = (n: number) => min(colW * 0.46, 6 + 1.9 * sqrt(n))
  // cherry-picks received per (lane, day): link indices, and counts by source lane (largest first)
  const cpIn = useMemo(() => {
    const g: Record<string, { ix: number[], n: number, byFrom: { f: string, n: number }[] }> = {}
    links.forEach((k, i) => {
      if (k.kind !== 'cp' || dayX[k.day] === undefined) return
      const e = g[`${k.to}|${k.day}`] ??= { ix: [], n: 0, byFrom: [] }
      e.ix.push(i)
      e.n += k.n
      const b = e.byFrom.find(x => x.f === k.from)
      if (b) b.n += k.n
      else e.byFrom.push({ f: k.from, n: k.n })
    })
    for (const e of Object.values(g)) e.byFrom.sort((a, b) => b.n - a.n)
    return g
  }, [links, dayX])
  const dotN = (l: string, d: string) => (cells[l]?.[d]?.n ?? 0) + (cpIn[`${l}|${d}`]?.n ?? 0)
  const wedge = (cx: number, cy: number, rr: number, f0: number, f1: number) => {
    const a0 = f0 * 2 * PI - PI / 2, a1 = f1 * 2 * PI - PI / 2
    return `M${cx},${cy} L${cx + rr * cos(a0)},${cy + rr * sin(a0)} A${rr},${rr} 0 ${a1 - a0 > PI ? 1 : 0} 1 ${cx + rr * cos(a1)},${cy + rr * sin(a1)} Z`
  }
  const isSel = (s: Sel) => JSON.stringify(s) === JSON.stringify(sel)

  const laneTip = (l: Lane) => {
    const n = Object.values(cells[l.id] ?? {}).reduce((a, c) => a + c.n, 0)
    const ins = links.filter(k => k.to === l.id), outs = links.filter(k => k.from === l.id)
    const sum = (ks: Link[], kind: 'merge' | 'cp') => ks.filter(k => k.kind === kind).reduce((a, k) => a + k.n, 0)
    const off = ins.filter(k => tone(k.cls) !== 'quiet').length
    return (
      <>
        <div className="tip-row"><LaneChip l={l} />{l.parent && <span className="dim">← <LaneChip l={lane[l.parent]} /></span>}</div>
        {l.note && <div className="tip-sub">{l.note}</div>}
        <div className="tip-sub">
          {plural(n, 'commit')} in view · in: {plural(sum(ins, 'merge'), 'merge')}, {plural(sum(ins, 'cp'), 'pick')}
          {off > 0 && <> ({off} not equal)</>} · out: {plural(sum(outs, 'merge'), 'merge')}, {plural(sum(outs, 'cp'), 'pick')}
        </div>
      </>
    )
  }

  return (
    <div className="dag" onMouseLeave={() => setHot(null)} onClick={() => sel?.t === 'lane' && setSel(null)}>
      <svg width={width} height={height} style={{ overflow: 'visible' }}>
        <defs>
          {lanes.map(l => (
            <marker key={l.id} id={`arr-${l.id}`} viewBox="0 0 6 6" refX={5} refY={3} markerWidth={6} markerHeight={6} markerUnits="userSpaceOnUse" orient="auto">
              <path d="M0,0 L6,3 L0,6 z" fill={l.color} />
            </marker>
          ))}
        </defs>
        {days.map((d, i) => (i % max(1, Math.round(28 / colW)) === 0 || i === days.length - 1) && (
          <text key={d} x={dayX[d]} y={10} fontSize={9} textAnchor="middle" className="axis">{fmtDay(d)}</text>
        ))}
        {lanes.map(l => {
          const y = laneY[l.id]
          const tx = treeX(l.id)
          if (!l.parent) return <circle key={`t${l.id}`} cx={tx} cy={y} r={3} fill={l.color} opacity={dimLane(l.id) ? 0.25 : 0.8} />
          const px = treeX(l.parent), py = laneY[l.parent]
          const d = `M${px},${py} L${px},${y - 7} Q${px},${y} ${px + 7},${y} L${lx - 3},${y}`
          return <path key={`t${l.id}`} d={d} fill="none" stroke={l.color} strokeWidth={2} strokeLinecap="round" opacity={dimLane(l.id) ? 0.2 : 0.7} />
        })}
        {lanes.map(l => {
          const y = laneY[l.id]
          const fork = forks[l.id]
          const start = fork && dayX[fork] !== undefined ? dayX[fork] : x0
          const gh = l.repo && `${l.repo}/tree/${l.ref}`
          const s: Sel = { t: 'lane', lane: l.id }
          return (
            <g key={l.id} opacity={dimLane(l.id) ? 0.3 : 1}>
              <rect x={lx} y={y - laneH / 2} width={width - lx} height={laneH} fill="transparent" {...on(s, laneTip(l))} />
              <a href={gh || undefined} target="_blank" rel="noreferrer" {...hover(s, laneTip(l))}>
                <text x={lx + labelW - 8} y={y + 4} fontSize={12} fontWeight={700} className="mono" textAnchor="end" fill={ink(l.color, dark)}>{l.label}</text>
              </a>
              <line x1={start} x2={width - 10} y1={y} y2={y} stroke={l.color} strokeWidth={laneRow === l.id ? 4 : 2.5} opacity={0.45} pointerEvents="none" />
              {fork && l.parent && dayX[fork] !== undefined && (
                <path d={`M${start - colW},${laneY[l.parent]} C${start - colW * 0.3},${laneY[l.parent]} ${start - colW * 0.7},${y} ${start - r(1)},${y}`} fill="none" stroke={l.color} strokeWidth={2.5} opacity={0.45} pointerEvents="none" />
              )}
            </g>
          )
        })}
        {prelude && (
          <g {...hover({ t: 'lane', lane: prelude.lane }, <div className="tip-row"><LaneChip l={lane[prelude.lane]} /> {prelude.title}</div>)}>
            <rect x={lx + labelW} y={laneY[prelude.lane] - 10} width={preW - 8} height={20} rx={10} fill={lane[prelude.lane].color} />
            <text x={lx + labelW + (preW - 8) / 2} y={laneY[prelude.lane] + 4} fontSize={10} textAnchor="middle" fill="white" fontWeight={600}>{prelude.label}</text>
          </g>
        )}
        {/* merges + cherry-picks: just left of the day's dots, arrowhead on the receiving lane; badge = didi class */}
        {links.map((k, i) => {
          const x = dayX[k.day]
          if (x === undefined) return null
          const y1 = laneY[k.from], y2 = laneY[k.to]
          const sx = x - colW * 0.8, ex = x - r(dotN(k.to, k.day)) - 2
          const mx = (sx + ex) / 2, my = (y1 + y2) / 2
          const d = `M${sx},${y1} C${mx},${y1} ${mx},${y2} ${ex},${y2}`
          const t = tone(k.cls), b = badge(k.cls)
          const off = k.items.filter(x => x.class && tone(x.class) !== 'quiet').length
          const tip = (
            <>
              <div className="tip-row"><LaneChip l={lane[k.from]} /><ArrowIcon kind={k.kind} color={ink(lane[k.from].color, true)} /><LaneChip l={lane[k.to]} /><ClassChip c={k.cls} /></div>
              <div className="tip-sub">
                {fmtDay(k.day, true)} · {k.kind === 'cp' ? plural(k.n, 'cherry-picked commit') : <>{plural(k.n, 'merge')}, bringing in {plural(k.items.reduce((a, x) => a + (x.n_in ?? 0), 0), 'commit')}</>}
                {off > 0 && <> · {off} not equal</>}
              </div>
              {k.kind === 'cp' && k.items.slice(0, 2).map((x, j) => <div key={j} className="tip-sub ellipsis">· <Subject s={x.subject} /></div>)}
            </>
          )
          const w = (isSel({ t: 'link', i }) ? 1.6 : 0) + 1.3 + 0.7 * log2(k.n)
          return (
            <g key={i} opacity={dimLink(i) ? (sel ? 0.12 : 0.3) : 1} {...on({ t: 'link', i }, tip)}>
              <path d={d} fill="none" stroke="transparent" strokeWidth={8} />
              {t !== 'quiet' && <path d={d} fill="none" className={`${t}-halo`} strokeWidth={w + (t === 'loud' ? 4 : 2.5)} />}
              <path
                d={d}
                fill="none"
                stroke={lane[k.from].color}
                strokeWidth={t === 'quiet' ? w : w + 0.6}
                strokeDasharray={k.kind === 'cp' ? '3 2.5' : undefined}
                opacity={t === 'quiet' ? (k.kind === 'cp' ? 0.75 : 0.6) : 1}
                markerEnd={`url(#arr-${k.from})`}
              />
              {b && t === 'loud' && (
                <g className={`link-badge ${t}`} pointerEvents="none">
                  <rect x={mx - 3.2 * b.length - 5} y={my - 6.5} width={6.4 * b.length + 10} height={13} rx={6.5} />
                  <text x={mx} y={my + 3.2} fontSize={9} textAnchor="middle" fontWeight={700}>{b}</text>
                </g>
              )}
            </g>
          )
        })}
        {/* dots: a lane's own commits that day, plus picks received as wedges in their source's colour */}
        {lanes.map(l => [...new Set([...Object.keys(cells[l.id] ?? {}), ...Object.keys(cpIn).filter(k => k.startsWith(`${l.id}|`)).map(k => k.split('|')[1])])].map(d => {
          if (dayX[d] === undefined) return null
          const c = cells[l.id]?.[d]
          const cp = cpIn[`${l.id}|${d}`]
          const own = c?.n ?? 0
          const n = own + (cp?.n ?? 0)
          const rr = r(n)
          const cx = dayX[d], cy = laneY[l.id]
          const s: Sel = c ? { t: 'cell', lane: l.id, day: d } : { t: 'link', i: cp!.ix[0] }
          const tip = (
            <>
              <div className="tip-row"><LaneChip l={l} /><span>{fmtDay(d, true)}</span></div>
              {c && <div className="tip-sub">{plural(c.n, 'commit')}{c.features.length ? `, incl. ${plural(c.features.length, 'feature merge')}` : ''}</div>}
              {cp && <div className="tip-sub">+ {plural(cp.n, 'cherry-pick')} from {cp.byFrom.map(({ f, n: k }, j) => <Fragment key={f}>{j ? ', ' : ''}<LaneChip l={lane[f]} /> {k}</Fragment>)}</div>}
              {c?.commits.filter(x => !x.merge).slice(0, cp ? 0 : 2).map((x, j) => <div key={j} className="tip-sub ellipsis">· <Subject s={x.subject} /></div>)}
            </>
          )
          const ring = { className: isSel(s) ? 'ring sel' : 'ring' }
          let f = own / n
          return (
            <g key={`${l.id}-${d}`} opacity={(c ? dimCell(l.id, d) : cp!.ix.every(dimLink)) ? (sel ? 0.25 : 0.35) : 1} {...on(s, tip)}>
              {cp ? (
                <>
                  {own > 0 && <path d={wedge(cx, cy, rr, 0, f)} fill={l.color} />}
                  {cp.byFrom.map(({ f: from, n: k }) => {
                    const f0 = f
                    f += k / n
                    return f - f0 >= 0.999 ? <circle key={from} cx={cx} cy={cy} r={rr} fill={lane[from].color} /> : <path key={from} d={wedge(cx, cy, rr, f0, f)} fill={lane[from].color} />
                  })}
                  <circle cx={cx} cy={cy} r={rr} fill="none" {...ring} />
                </>
              ) : <circle cx={cx} cy={cy} r={rr} fill={l.color} {...ring} />}
              {rr >= 6 && (
                <text x={cx} y={cy + 3.5} fontSize={min(11, rr * (n >= 100 ? 0.72 : 0.95))} textAnchor="middle" fill="white" fontWeight={700} pointerEvents="none"
                  stroke="rgba(0,0,0,.45)" strokeWidth={cp ? 2.2 : 0} paintOrder="stroke" strokeLinejoin="round">{n}</text>
              )}
            </g>
          )
        }))}
      </svg>
      <Legend links={links} />
      <div className="strip">
        {hot ? (
          <>{hot.hint}{hot.sel.t !== 'lane' && <div className="strip-hint">click for details</div>}</>
        ) : sel?.t === 'lane' ? (
          <>{laneTip(lane[sel.lane])}<div className="strip-hint">pinned · click the row again to unpin</div></>
        ) : (
          <div className="dim">hover a branch row, dot or arrow for details here · click a dot or arrow to list its commits</div>
        )}
      </div>
      {sel && sel.t !== 'lane' && <Drawer dag={dag} sel={sel} onClose={() => setSel(null)} onItem={sha => { setSel(null); onItem(sha) }} />}
    </div>
  )
}

function Legend({ links }: { links: Link[] }) {
  const count = (kind: 'merge' | 'cp', t?: string) => links.filter(k => k.kind === kind).reduce((a, k) => a + k.items.filter(x => !t || (x.class && tone(x.class) === t)).length, 0)
  const item = (icon: ReactNode, text: ReactNode) => <span className="legend-item">{icon}{text}</span>
  const arrow = (dash?: string) => <svg width={30} height={10}><path d="M0,5 H24" stroke="currentColor" strokeWidth={1.6} strokeDasharray={dash} /><path d="M24,2 L30,5 L24,8 z" fill="currentColor" /></svg>
  return (
    <div className="legend">
      {item(arrow(), `merge (${count('merge')}; ${count('merge', 'loud')} differ)`)}
      {item(arrow('3 2.5'), `cherry-pick (${count('cp')}; ${count('cp', 'loud')} differ)`)}
      {item(<svg width={30} height={10}><path d="M0,5 H24" className="soft-halo" strokeWidth={4.5} /><path d="M0,5 H24" stroke="currentColor" strokeWidth={1.6} /></svg>, 'equal once normalized (lines, context, ws, tokens)')}
      {item(<svg width={18} height={13}><g className="link-badge loud"><rect x={1} y={0} width={16} height={13} rx={6.5} /><text x={9} y={9.7} fontSize={9} textAnchor="middle" fontWeight={700}>≠</text></g></svg>, 'differs')}
    </div>
  )
}

function Drawer({ dag, sel, onClose, onItem }: { dag: Dag, sel: Sel, onClose: () => void, onItem: (sha: string) => void }) {
  const [el, setEl] = useState<HTMLDivElement | null>(null)
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    const away = (e: PointerEvent) => { if (el && !el.contains(e.target as Node)) onClose() }
    window.addEventListener('keydown', k, true)
    window.addEventListener('pointerdown', away, true)
    return () => { window.removeEventListener('keydown', k, true); window.removeEventListener('pointerdown', away, true) }
  }, [onClose, el])
  const lane = Object.fromEntries(dag.lanes.map(l => [l.id, l]))
  const row = (key: string | number, left: ReactNode, body: ReactNode, sha?: string) => (
    <li key={key} className={sha ? 'clickable' : undefined} onClick={sha ? () => onItem(sha) : undefined}>{left}<span>{body}</span></li>
  )
  let head: ReactNode, sub: ReactNode = null, list: ReactNode
  if (sel.t === 'cell') {
    const c = dag.cells[sel.lane][sel.day]
    const l = lane[sel.lane]
    head = <><LaneChip l={l} /> · {fmtDay(sel.day, true)}</>
    sub = <>{plural(c.n, 'commit')}{c.features.length > 0 && <>, incl. {plural(c.features.length, 'feature merge')}</>}</>
    list = c.commits.map((x, i) => row(i, <><Sha repo={l.repo} sha={x.sha} /><span className="time">{x.date.slice(11, 16)}</span></>,
      x.merge ? <span className="dim">⤵ merged feature <code>{x.subject.match(/'(?:refs\/heads\/)?([^']+)'/)?.[1] ?? x.subject}</code></span> : <Subject s={x.subject} />))
  } else if (sel.t === 'link') {
    const k = dag.links[sel.i]
    head = <>{k.kind === 'cp' ? 'Cherry-picks' : 'Merges'}: <LaneChip l={lane[k.from]} /> → <LaneChip l={lane[k.to]} /></>
    sub = <>{fmtDay(k.day, true)} · {k.kind === 'cp' ? plural(k.n, 'commit') : <>{plural(k.n, 'merge')}, bringing in {plural(k.items.reduce((a, x) => a + (x.n_in ?? 0), 0), 'commit')}</>} · click a row for its diff of diffs</>
    list = k.items.map((x, i) => row(i, <><Sha repo={lane[k.to].repo} sha={x.sha} />{x.class && <ClassChip c={x.class} />}</>,
      k.kind === 'merge' ? <span className="dim">{plural(x.n_in ?? 0, 'commit')} from <code>{k.from}</code></span> : <Subject s={x.subject} />, x.class ? x.sha : undefined))
  }
  return (
    <FloatingPortal>
      <div ref={setEl} onClick={e => e.stopPropagation()} className="drawer">
        <div className="drawer-head">
          <div className="drawer-title"><span>{head}</span><button onClick={onClose} aria-label="close">×</button></div>
          {sub && <div className="drawer-sub">{sub}</div>}
        </div>
        <ul>{list}</ul>
      </div>
    </FloatingPortal>
  )
}
