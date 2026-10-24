import { describe, expect, it } from 'vitest'
import { badge, buildDag, tone, worst, type Report } from './report'

const sha = (c: string) => c.repeat(40)

// Two lanes: `main`, and `dep` forked from it on Oct 2, receiving a merge and two picks (one equal, one differing)
const REPORT: Report = {
  version: 1, generated: '2026-10-05T00:00:00+00:00', since: '2026-10-01',
  options: { normalize: 'index', unified: 3, ignore_whitespace: false, find_renames: null, find_copies: null },
  lanes: [
    { name: 'main', ref: 'main', head: sha('a'), head_date: '2026-10-03T10:00:00+00:00', commits: 12, color: '#5b5b5b' },
    { name: 'dep', ref: 'dep', parent: 'main', head: sha('b'), head_date: '2026-10-04T10:00:00+00:00', commits: 15, ahead: 3, behind: 0, fork_date: '2026-10-02T09:00:00+00:00' },
  ],
  segments: {
    main: [
      { kind: 'run', n: 2, first_date: '2026-10-01T10:00:00+00:00', last_date: '2026-10-03T10:00:00+00:00', commits: [
        { sha: sha('1'), date: '2026-10-01T10:00:00+00:00', subject: 'one' },
        { sha: sha('2'), date: '2026-10-03T10:00:00+00:00', subject: 'two' },
      ], feature_merges: {}, side_commits: 0 },
    ],
    dep: [
      { kind: 'run', n: 1, first_date: '2026-10-02T09:00:00+00:00', last_date: '2026-10-02T09:00:00+00:00', commits: [
        { sha: sha('3'), date: '2026-10-02T09:00:00+00:00', subject: "Merge branch 'feat'", merge: true },
      ], feature_merges: { feat: 1 }, side_commits: 2 },
      { kind: 'cp', date: '2026-10-03T11:00:00+00:00', last_date: '2026-10-03T12:00:00+00:00', from_lane: 'main', n: 2, picks: [
        { sha: sha('4'), date: '2026-10-03T11:00:00+00:00', source: sha('1'), subject: 'one', class: 'equal' },
        { sha: sha('5'), date: '2026-10-03T12:00:00+00:00', source: sha('2'), subject: 'two', class: 'differs' },
      ] },
      { kind: 'merge', sha: sha('6'), date: '2026-10-04T10:00:00+00:00', from_lane: 'main', from_kind: 'parent', subject: 'Merge main', n_in: 2, class: 'equal@lines' },
      { kind: 'cp', date: '2026-10-04T11:00:00+00:00', last_date: '2026-10-04T11:00:00+00:00', from_lane: 'dangling', n: 1, picks: [
        { sha: sha('7'), date: '2026-10-04T11:00:00+00:00', source: sha('9'), subject: 'gone', class: 'equal' },
      ] },
    ],
  },
  picks: {},
  merges: {},
}

describe('buildDag', () => {
  const dag = buildDag(REPORT)

  it('bins days, cells, and forks', () => {
    expect(dag.days).toEqual(['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04'])
    expect(Object.fromEntries(Object.entries(dag.cells).map(([l, c]) => [l, Object.fromEntries(Object.entries(c).map(([d, x]) => [d, [x.n, x.features]]))]))).toEqual({
      main: { '2026-10-01': [1, []], '2026-10-03': [1, []] },
      dep: { '2026-10-02': [1, ['feat']] },
    })
    expect(dag.forks).toEqual({ dep: '2026-10-02' })
  })

  it("links by (kind, from, to, day), classed by their worst item; sources outside the lanes aren't drawn", () => {
    expect(dag.links.map(k => [k.kind, k.from, k.to, k.day, k.n, k.cls])).toEqual([
      ['cp', 'main', 'dep', '2026-10-03', 2, 'differs'],
      ['merge', 'main', 'dep', '2026-10-04', 1, 'equal@lines'],
    ])
  })

  it('summarizes earlier root commits as a prelude', () => {
    expect(dag.prelude).toEqual({ lane: 'main', label: '10 ⋯', title: '10 earlier commits on main' })
  })
})

describe('classes', () => {
  it('orders, tones, and badges', () => {
    expect(worst(['equal', 'absorbed', 'equal@tokens', 'equal@lines'])).toBe('equal@tokens')
    expect(worst([])).toBe('equal')
    expect(['equal', 'adopted', 'equal@ws', 'missing', 'files-differ'].map(c => tone(c as never))).toEqual(['quiet', 'quiet', 'soft', 'soft', 'loud'])
    expect(['equal', 'equal@context', 'differs'].map(c => badge(c as never))).toEqual([null, 'ctx', '≠'])
  })
})
