import { useEffect, useRef, useState } from 'react'

/** An element's content width, tracked with a `ResizeObserver`. */
export function useWidth<T extends HTMLElement>(initial = 900): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null)
  const [w, setW] = useState(initial)
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}
