import { autoUpdate, flip, FloatingPortal, offset, shift, useFloating, useFocus, useHover, useInteractions } from '@floating-ui/react'
import { Fragment, useState, type ReactNode } from 'react'
import { tone, type Class, type FileLabel } from './report'

export function Tooltip({ tip, children }: { tip: ReactNode, children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const { refs, floatingStyles, context } = useFloating({
    open, onOpenChange: setOpen, placement: 'top', whileElementsMounted: autoUpdate, middleware: [offset(6), flip(), shift({ padding: 8 })],
  })
  const { getReferenceProps, getFloatingProps } = useInteractions([useHover(context, { delay: { open: 80 } }), useFocus(context)])
  return (
    <>
      <span ref={refs.setReference} {...getReferenceProps()} className="tt-ref">{children}</span>
      {open && (
        <FloatingPortal>
          <div ref={refs.setFloating} style={floatingStyles} {...getFloatingProps()} className="tooltip">{tip}</div>
        </FloatingPortal>
      )}
    </>
  )
}

export const CLASS_TIPS: Record<string, string> = {
  'equal': 'Same patch (ignoring `index` lines)',
  'equal@lines': 'Same patch once hunk-header line numbers are ignored',
  'equal@context': 'Same `+`/`-` lines; only context lines differ',
  'equal@ws': 'Same `+`/`-` lines once whitespace is ignored',
  'equal@tokens': 'Same token-level edits, on lines that otherwise differ (e.g. the same parameter added to a signature both sides changed)',
  'differs': 'The patches differ',
  'files-differ': 'The pick touches a different set of files than its source (an adapted pick)',
  'missing': "The pick's source commit isn't in the repo (rewritten history)",
  'absorbed': 'The residual is explained by picks of the child\'s own commits that the merge brought back',
  'adopted': 'Lines the delta lost are now in the base, without a matching pick',
  'left-only': 'Only in the left (before) side',
  'right-only': 'Only in the right (after) side',
}

export function ClassChip({ c }: { c: Class | FileLabel }) {
  const t = c === 'left-only' || c === 'right-only' ? 'loud' : tone(c as Class)
  return <Tooltip tip={<Subject s={CLASS_TIPS[c] ?? c} />}><span className={`class-chip ${t}`}>{c}</span></Tooltip>
}

// `code` spans and a leading [tag] chip, from a commit subject
export function Subject({ s }: { s: string }) {
  const m = s.match(/^\[([a-z0-9-]+)\]\s*/)
  const rest = m ? s.slice(m[0].length) : s
  return (
    <>
      {m && <span className="subject-tag">{m[1]}</span>}
      {rest.split('`').map((p, i) => i % 2 ? <code key={i}>{p}</code> : <Fragment key={i}>{p}</Fragment>)}
    </>
  )
}

export function Sha({ repo, sha, n = 7 }: { repo?: string, sha: string, n?: number }) {
  return repo
    ? <a className="sha" href={`${repo}/commit/${sha}`} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}>{sha.slice(0, n)}</a>
    : <span className="sha">{sha.slice(0, n)}</span>
}

export function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false)
  return (
    <button className="copy" onClick={() => navigator.clipboard.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1200) })}>
      {done ? 'copied' : 'copy'}
    </button>
  )
}
