import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Copy } from './bits'
import { Files } from './Detail'
import type { FileRec, Level } from './report'

// `git-didi serve` only: diff any two ranges (`git-didi patch LEFT RIGHT`), every non-equal file with its diff of diffs

export type Info = { serve: true, repo: string, repo_url: string | null, report: boolean }
type Side = { spec: string, base: string, tip: string }
type Comparison = { left: Side, right: Side, options: { normalize: Level }, counts: Record<string, number>, files: FileRec[] }

const LEVELS: Level[] = ['none', 'index', 'lines', 'context', 'ws', 'tokens']

/** `/api/info` answers only under `git-didi serve`; elsewhere (e.g. GH Pages) this resolves to null. */
export function useServeInfo() {
  return useQuery({
    queryKey: ['info'],
    queryFn: async (): Promise<Info | null> => {
      try {
        const res = await fetch('/api/info')
        if (!res.ok || !res.headers.get('content-type')?.includes('json')) return null
        return await res.json()
      } catch {
        return null
      }
    },
    retry: false,
  })
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  const data = await res.json()
  if (!res.ok) throw new Error(data.error ?? `${res.status} ${res.statusText}`)
  return data
}

export function Compare({ info, left, right, level, onChange }: {
  info: Info, left?: string, right?: string, level?: string,
  onChange: (left: string, right: string, level: string | undefined) => void,
}) {
  const [l, setL] = useState(left ?? '')
  const [r, setR] = useState(right ?? '')
  const [n, setN] = useState(level ?? 'index')
  useEffect(() => { setL(left ?? ''); setR(right ?? ''); setN(level ?? 'index') }, [left, right, level])
  const q = useQuery({
    queryKey: ['patch', left, right, level],
    queryFn: () => getJson<Comparison>(`/api/patch?${new URLSearchParams({ left: left!, right: right!, ...(level ? { n: level } : {}) })}`),
    enabled: !!left && !!right,
  })
  const cmd = left && right ? `git-didi patch ${level && level !== 'index' ? `-n ${level} ` : ''}${left} ${right}` : null
  const c = q.data
  const refs = c && {
    left: { repo: info.repo_url ?? undefined, pre: c.left.base, post: c.left.tip },
    right: { repo: info.repo_url ?? undefined, pre: c.right.base, post: c.right.tip },
  }
  return (
    <section className="compare">
      <form onSubmit={e => { e.preventDefault(); if (l && r) onChange(l, r, n === 'index' ? undefined : n) }}>
        <input value={l} onChange={e => setL(e.target.value)} placeholder="left: A..B (e.g. main@{1}..feature@{1})" />
        <input value={r} onChange={e => setR(e.target.value)} placeholder="right: C..D (e.g. main..feature)" />
        <select value={n} onChange={e => setN(e.target.value)} aria-label="normalization level">
          {LEVELS.map(x => <option key={x} value={x}>-n {x}</option>)}
        </select>
        <button type="submit">compare</button>
      </form>
      {cmd && <div className="cmd"><code>{cmd}</code><Copy text={cmd} /></div>}
      {q.isLoading && <p className="dim">Comparing…</p>}
      {q.error && <p className="error">{q.error.message}</p>}
      {c && refs && <Files files={c.files} counts={c.counts} refs={refs} />}
      {!left && <p className="dim">Diff the patches of any two ranges of <code>{info.repo}</code>, e.g. a branch before and after a rebase.</p>}
    </section>
  )
}
