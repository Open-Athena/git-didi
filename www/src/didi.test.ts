import { describe, expect, it } from 'vitest'
import { charDiff, parseDidi, type Row } from './didi'

// `tests/test_report.py`'s merge: both sides append `, staged = true` to a signature only the right side also extended
const SIG = [
  '@@ -3,6 +3,6 @@',
  ' --- a/sig.ts',
  ' +++ b/sig.ts',
  ' @@ -1,2 +1,2 @@',
  '--function f(a, b) {',
  '-+function f(a, b, staged = true) {',
  '+-function f(a, b, c) {',
  '++function f(a, b, c, staged = true) {',
  '  }',
]

// Runs of characters carrying a highlight (adjacent spans merged, whatever their other flag)
const marked = (r: Row, which: 'inner' | 'outer') => {
  if (r.kind !== 'line') return null
  const runs: string[] = []
  let prev = false
  for (const s of r.spans) {
    if (s[which]) {
      if (prev) runs[runs.length - 1] += s.text
      else runs.push(s.text)
    }
    prev = !!s[which]
  }
  return runs
}

describe('parseDidi', () => {
  const rows = parseDidi(SIG)

  it('classifies rows and gutters', () => {
    expect(rows.map(r => r.kind === 'line' ? `${r.kind} ${r.outer}${r.inner}` : `${r.kind}${'outer' in r ? ` ${r.outer}` : ''}`)).toEqual([
      'outer-hunk',
      'meta  ',
      'meta  ',
      'inner-hunk  ',
      'line --',
      'line -+',
      'line +-',
      'line ++',
      'line   ',
    ])
  })

  it('tracks inner line numbers per side from the inner hunk header', () => {
    expect(rows.map(r => r.kind === 'line' ? [r.left, r.right] : null).filter(Boolean)).toEqual([
      [{ old: 1 }, undefined],
      [{ new: 1 }, undefined],
      [undefined, { old: 1 }],
      [undefined, { new: 1 }],
      [{ old: 2, new: 2 }, { old: 2, new: 2 }],
    ])
  })

  it('highlights the edit within each side (inner) and the difference between sides (outer)', () => {
    expect(rows.map(r => marked(r, 'inner'))).toEqual([null, null, null, null, [], [', staged = true'], [], [', staged = true'], []])
    // only the right side has extra tokens (the parent's `, c`)
    expect(rows.map(r => marked(r, 'outer'))).toEqual([null, null, null, null, [], [], [', c'], ['c, '], []])
  })

  it('drops line numbers across outer hunks', () => {
    const rows = parseDidi(['@@ -1,2 +1,2 @@', ' @@ -10,2 +10,2 @@', '  x', '@@ -9,1 +9,1 @@', '  y'])
    expect(rows.flatMap(r => r.kind === 'line' ? [[r.text, r.left, r.right]] : [])).toEqual([
      ['x', { old: 10, new: 10 }, { old: 10, new: 10 }],
      ['y', undefined, undefined],
    ])
  })
})

describe('charDiff', () => {
  it('flags inserted tokens', () => {
    const [a, b] = charDiff('f(a)', 'f(a, b)')
    expect(a.every(c => !c)).toBe(true)
    expect([...'f(a, b)'].filter((_, i) => b[i]).join('')).toBe(', b')
  })
})
