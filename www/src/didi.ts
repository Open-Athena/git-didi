// Parse a diff of diffs (`FileRec.didi`) into rendered rows: an outer gutter (which side has the line), an inner gutter
// (`+`/`-`/context within that side's patch), inner line numbers where known, and token-level highlights:
// - `inner`: the edit within one patch (a `-` line paired with the `+` line after it, on the same side);
// - `outer`: what differs between the sides (an outer `-` line paired with the outer `+` line of the same inner sign).

export type Outer = ' ' | '-' | '+'
export type Inner = ' ' | '-' | '+'
export type Span = { text: string, inner?: boolean, outer?: boolean }
export type LineNos = { old?: number, new?: number }
export type Row =
  | { kind: 'outer-hunk', text: string }
  | { kind: 'meta', outer: Outer, text: string }
  | { kind: 'inner-hunk', outer: Outer, text: string }
  | { kind: 'line', outer: Outer, inner: Inner, text: string, spans: Span[], left?: LineNos, right?: LineNos }

const META = /^(diff --git |index |--- (a\/|\/dev\/null)|\+\+\+ (b\/|\/dev\/null)|new file|deleted file|similarity |dissimilarity |rename |copy |old mode|new mode|Binary |\\ No newline)/
const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/
const TOKEN = /\w+|\s+|[^\w\s]/g

type Pos = { old: number, new: number } | null

export function parseDidi(lines: string[]): Row[] {
  const rows: Row[] = []
  let left: Pos = null, right: Pos = null
  for (const line of lines) {
    if (line.startsWith('@@')) {
      rows.push({ kind: 'outer-hunk', text: line })
      left = right = null
      continue
    }
    const outer = (line[0] ?? ' ') as Outer
    const rest = line.slice(1)
    const onLeft = outer !== '+', onRight = outer !== '-'
    const hunk = rest.match(HUNK)
    if (hunk) {
      const pos = { old: +hunk[1], new: +hunk[2] }
      if (onLeft) left = { ...pos }
      if (onRight) right = { ...pos }
      rows.push({ kind: 'inner-hunk', outer, text: rest })
      continue
    }
    if (META.test(rest)) {
      rows.push({ kind: 'meta', outer, text: rest })
      continue
    }
    const inner = (rest[0] ?? ' ') as Inner
    const text = rest.slice(1)
    const step = (p: Pos): LineNos | undefined => {
      if (!p) return undefined
      const nos: LineNos = inner === '-' ? { old: p.old } : inner === '+' ? { new: p.new } : { old: p.old, new: p.new }
      if (inner !== '+') p.old++
      if (inner !== '-') p.new++
      return nos
    }
    rows.push({ kind: 'line', outer, inner, text, spans: [{ text }], left: onLeft ? step(left) : undefined, right: onRight ? step(right) : undefined })
  }
  highlight(rows)
  return rows
}

type LineRow = Extract<Row, { kind: 'line' }>
type Marks = Map<LineRow, { inner: boolean[], outer: boolean[] }>

function highlight(rows: Row[]) {
  const marks: Marks = new Map()
  const mark = (r: LineRow, which: 'inner' | 'outer', changed: boolean[]) => {
    const m = marks.get(r) ?? { inner: [], outer: [] }
    changed.forEach((c, i) => { if (c) m[which][i] = true })
    marks.set(r, m)
  }
  const pairUp = (a: LineRow[], b: LineRow[], which: 'inner' | 'outer') => {
    for (let i = 0; i < Math.min(a.length, b.length); i++) {
      const [ca, cb] = charDiff(a[i].text, b[i].text)
      if (ca.some(Boolean) || cb.some(Boolean)) {
        mark(a[i], which, ca)
        mark(b[i], which, cb)
      }
    }
  }
  // Outer: a block of outer `-` rows followed by outer `+` rows; pair by inner sign, in order
  for (const block of outerBlocks(rows)) {
    for (const sign of ['-', '+', ' '] as Inner[]) {
      pairUp(block.minus.filter(r => r.inner === sign), block.plus.filter(r => r.inner === sign), 'outer')
    }
  }
  // Inner: on each side's sequence of rows, a run of inner `-` rows followed by inner `+` rows
  for (const side of ['left', 'right'] as const) {
    const seq = rows.filter((r): r is LineRow | Extract<Row, { kind: 'outer-hunk' }> =>
      r.kind === 'outer-hunk' || (r.kind === 'line' && (side === 'left' ? r.outer !== '+' : r.outer !== '-')))
    let minus: LineRow[] = [], plus: LineRow[] = []
    const flush = () => { pairUp(minus, plus, 'inner'); minus = []; plus = [] }
    for (const r of seq) {
      if (r.kind !== 'line' || r.inner === ' ') { flush(); continue }
      if (r.inner === '-') {
        if (plus.length) flush()
        minus.push(r)
      } else {
        plus.push(r)
      }
    }
    flush()
  }
  for (const [r, m] of marks) r.spans = spans(r.text, m.inner, m.outer)
}

function outerBlocks(rows: Row[]): { minus: LineRow[], plus: LineRow[] }[] {
  const blocks: { minus: LineRow[], plus: LineRow[] }[] = []
  let cur = { minus: [] as LineRow[], plus: [] as LineRow[] }
  const flush = () => { if (cur.minus.length && cur.plus.length) blocks.push(cur); cur = { minus: [], plus: [] } }
  for (const r of rows) {
    if (r.kind !== 'line' || r.outer === ' ') { flush(); continue }
    if (r.outer === '-') {
      if (cur.plus.length) flush()
      cur.minus.push(r)
    } else {
      cur.plus.push(r)
    }
  }
  flush()
  return blocks
}

/** Per-character "changed" flags for two strings, from an LCS over word/space/punctuation tokens. */
export function charDiff(a: string, b: string): [boolean[], boolean[]] {
  const ta = a.match(TOKEN) ?? [], tb = b.match(TOKEN) ?? []
  const n = ta.length, m = tb.length
  if (n * m > 160_000) return [Array(a.length).fill(false), Array(b.length).fill(false)]
  const lcs: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = ta[i] === tb[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1])
    }
  }
  const keepA = Array(n).fill(false), keepB = Array(m).fill(false)
  for (let i = 0, j = 0; i < n && j < m;) {
    if (ta[i] === tb[j]) { keepA[i] = keepB[j] = true; i++; j++ }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) i++
    else j++
  }
  const chars = (ts: string[], keep: boolean[]) => ts.flatMap((t, i) => Array(t.length).fill(!keep[i]))
  return [chars(ta, keepA), chars(tb, keepB)]
}

function spans(text: string, inner: boolean[], outer: boolean[]): Span[] {
  const out: Span[] = []
  for (let i = 0; i < text.length; i++) {
    const s: Span = { text: text[i] }
    if (inner[i]) s.inner = true
    if (outer[i]) s.outer = true
    const last = out.at(-1)
    if (last && !!last.inner === !!s.inner && !!last.outer === !!s.outer) last.text += s.text
    else out.push(s)
  }
  return out.length ? out : [{ text }]
}
