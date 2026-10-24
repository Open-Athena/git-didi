// Types for `git-didi report` output (schema: ../schema.json), and the day-binned lanes model drawn by `GitDag`.

export type Level = 'none' | 'index' | 'lines' | 'context' | 'ws' | 'tokens'
export type PickClass = 'equal' | 'equal@lines' | 'equal@context' | 'equal@ws' | 'equal@tokens' | 'differs' | 'files-differ' | 'missing'
export type MergeClass = 'equal' | 'absorbed' | 'adopted' | 'equal@lines' | 'equal@context' | 'equal@ws' | 'equal@tokens' | 'differs'
export type FileLabel = 'equal' | 'equal@lines' | 'equal@context' | 'equal@ws' | 'equal@tokens' | 'differs' | 'left-only' | 'right-only' | 'absorbed' | 'adopted'
export type Class = PickClass | MergeClass

export type ReportLane = {
  name: string, ref: string, parent?: string, label?: string, color?: string, repo_url?: string, aliases?: string[],
  surfaces?: Record<string, string[]>, head: string, head_date: string, commits: number, ahead?: number, behind?: number,
  fork_date?: string | null,
}
export type Commit = { sha: string, date: string, subject: string, merge?: true }
export type RunSeg = { kind: 'run', n: number, first_date: string, last_date: string, commits: Commit[], feature_merges: Record<string, number>, side_commits: number }
export type MergeSeg = { kind: 'merge', sha: string, date: string, from_lane: string, from_kind: 'parent' | 'peer', subject: string, n_in: number, class?: MergeClass }
export type PickItem = { sha: string, date: string, source: string, subject: string, class: PickClass }
export type CpSeg = { kind: 'cp', date: string, last_date: string, from_lane: string, n: number, picks: PickItem[] }
export type Segment = RunSeg | MergeSeg | CpSeg

export type FileRec = {
  path: string, left_path?: string, status: 'differs' | 'left-only' | 'right-only' | 'absorbed' | 'adopted', level: Level | null,
  label: FileLabel, left_lines: number, right_lines: number, residual_lines?: number, absorbed_lines?: number,
  didi?: string[], didi_lines?: number,
}
export type PickRec = {
  commit: string, subject: string, source: string, class: PickClass, level: Level | null, ok: boolean, files: FileRec[],
  lane: string, from_lane: string, date: string,
}
export type Side = { base: string, tip: string, files: number, added: number, removed: number }
export type MergeRec = {
  sha: string, lane: string, from_lane: string, date: string, subject: string, class: MergeClass, ok: boolean,
  before: Side, after: Side, absorbed_commits: string[], counts: Record<string, number>, files: FileRec[],
}
export type Report = {
  version: 1, generated: string, since: string | null,
  options: { normalize: Level, unified: number, ignore_whitespace: boolean, find_renames: string | null, find_copies: string | null },
  lanes: ReportLane[], segments: Record<string, Segment[]>, picks: Record<string, PickRec>, merges: Record<string, MergeRec>,
}

// How loudly to draw an item: `quiet` = faithful, `soft` = equal once normalized, `loud` = differs
export type Tone = 'quiet' | 'soft' | 'loud'
const SEVERITY: Class[] = ['equal', 'absorbed', 'adopted', 'equal@lines', 'equal@context', 'equal@ws', 'equal@tokens', 'missing', 'differs', 'files-differ']
export const worst = (cs: Class[]): Class => cs.reduce((a, c) => SEVERITY.indexOf(c) > SEVERITY.indexOf(a) ? c : a, 'equal' as Class)
export const tone = (c: Class): Tone => ['equal', 'absorbed', 'adopted'].includes(c) ? 'quiet' : ['differs', 'files-differ'].includes(c) ? 'loud' : 'soft'
// A link badge: the level, or what's wrong
export const badge = (c: Class): string | null => ({
  'equal@lines': 'lines', 'equal@context': 'ctx', 'equal@ws': 'ws', 'equal@tokens': 'tok', 'missing': '?', 'differs': '≠', 'files-differ': 'files',
} as Record<string, string>)[c] ?? null

export type Lane = { id: string, ref: string, label: string, color: string, parent?: string, repo?: string, note?: string }
export type Cell = { n: number, commits: Commit[], features: string[] }
export type LinkItem = { sha: string, subject: string, source?: string, n_in?: number, class?: Class }
export type Link = { from: string, to: string, day: string, kind: 'merge' | 'cp', n: number, items: LinkItem[], cls: Class }
export type Dag = {
  lanes: Lane[], days: string[], cells: Record<string, Record<string, Cell>>, links: Link[], forks: Record<string, string>,
  prelude?: { lane: string, label: string, title: string },
}

const PALETTE = ['#5b5b5b', '#4285f4', '#6c4bd8', '#e8a400', '#f38020', '#2f8f5b', '#3aa36b', '#7cc29a', '#d6336c', '#0b7285']
const day = (iso: string) => iso.slice(0, 10)

export function featureName(subject: string): string {
  const m = subject.match(/^Merge (?:remote-tracking )?branch '(?:refs\/heads\/)?([^']+)'/)
  return m ? m[1] : subject
}

/** Bin a report's segments by day: a dot per lane-day of own commits, a link per (kind, from, to, day). */
export function buildDag(report: Report): Dag {
  const lanes: Lane[] = report.lanes.map((l, i) => ({
    id: l.name, ref: l.ref, label: l.name, color: l.color ?? PALETTE[i % PALETTE.length], parent: l.parent, repo: l.repo_url, note: l.label,
  }))
  const ids = new Set(lanes.map(l => l.id))
  const cells: Dag['cells'] = {}
  const linkMap = new Map<string, Link>()
  const forks: Record<string, string> = {}
  const daySet = new Set<string>()
  for (const l of report.lanes) {
    if (l.parent && l.fork_date && !(report.since && day(l.fork_date) < report.since)) forks[l.name] = day(l.fork_date)
    const lane = cells[l.name] = {} as Record<string, Cell>
    for (const s of report.segments[l.name] ?? []) {
      if (s.kind === 'run') {
        for (const c of s.commits) {
          const d = day(c.date)
          daySet.add(d)
          const cell = lane[d] ??= { n: 0, commits: [], features: [] }
          cell.n++
          cell.commits.push(c)
          if (c.merge) cell.features.push(featureName(c.subject))
        }
        continue
      }
      const d = day(s.kind === 'cp' ? s.last_date : s.date)
      daySet.add(d)
      if (!ids.has(s.from_lane)) continue
      const key = `${s.kind}|${s.from_lane}|${l.name}|${d}`
      const k = linkMap.get(key) ?? { from: s.from_lane, to: l.name, day: d, kind: s.kind, n: 0, items: [], cls: 'equal' }
      if (s.kind === 'cp') {
        for (const p of s.picks) k.items.push({ sha: p.sha, source: p.source, subject: p.subject, class: p.class })
        k.n += s.picks.length
      } else {
        k.items.push({ sha: s.sha, subject: s.subject, n_in: s.n_in, class: s.class })
        k.n++
      }
      k.cls = worst(k.items.flatMap(x => x.class ? [x.class] : []))
      linkMap.set(key, k)
    }
  }
  const all = [...daySet].sort()
  const days: string[] = []
  if (all.length) {
    for (let t = new Date(`${all[0]}T00:00:00Z`); t <= new Date(`${all.at(-1)}T00:00:00Z`); t.setUTCDate(t.getUTCDate() + 1)) {
      days.push(t.toISOString().slice(0, 10))
    }
  }
  const root = report.lanes.find(l => !l.parent)
  let prelude: Dag['prelude']
  if (root && report.since) {
    const inView = Object.values(cells[root.name]).reduce((a, c) => a + c.n, 0)
    const earlier = root.commits - inView
    if (earlier > 0) prelude = { lane: root.name, label: `${earlier} ⋯`, title: `${earlier} earlier commits on ${root.name}` }
  }
  return { lanes, days, cells, links: [...linkMap.values()], forks, prelude }
}

export function itemCommand(report: Report, sha: string): string | null {
  const p = report.picks[sha]
  if (p) return `git-didi patch ${p.source.slice(0, 10)}^..${p.source.slice(0, 10)} ${sha.slice(0, 10)}^..${sha.slice(0, 10)}`
  const m = report.merges[sha]
  if (m) return `git-didi merge ${sha.slice(0, 10)}`
  return null
}
