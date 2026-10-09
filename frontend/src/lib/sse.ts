import { useEffect, useRef, useState } from 'react'
import type { LogTuple } from './api'

export interface SseLogState {
  lines: LogTuple[]
  offset: number
  ended: boolean
  stale: boolean
}

function streamUrl(slug: string, kind: 'build' | 'run', offset: number) {
  return `/api/deployments/${slug}/${kind}/logs/stream?offset=${offset}`
}

/**
 * Stream a deployment log over SSE (FR-3): opens at `offset`, reopens on drop
 * from the last seen offset, dedupes replayed lines. `stale` means the stream
 * gave up after repeated failures — the caller shows a Retry.
 */
export function useSseLog(slug: string, kind: 'build' | 'run', enabled: boolean): SseLogState {
  const [lines, setLines] = useState<LogTuple[]>([])
  const [offset, setOffset] = useState(0)
  const [ended, setEnded] = useState(false)
  const [stale, setStale] = useState(false)

  const seenRef = useRef<Set<string>>(new Set())
  const nextOffsetRef = useRef(0)
  const genRef = useRef(0)

  useEffect(() => {
    if (!enabled || !slug) return
    const gen = genRef.current
    const seen = seenRef.current
    let es: EventSource | null = null
    let retries = 0
    let timer: ReturnType<typeof setTimeout> | null = null

    setLines([])
    setEnded(false)
    setStale(false)
    seen.clear()

    const open = () => {
      const source = new EventSource(streamUrl(slug, kind, nextOffsetRef.current))
      es = source
      source.addEventListener('line', (event) => {
        try {
          const d = JSON.parse((event as MessageEvent).data) as { ts: string; line: string; offset: number }
          const key = `${d.ts}\u0000${d.line}`
          if (!seen.has(key)) {
            seen.add(key)
            setLines((prev) => [...prev, { ts: d.ts, line: d.line }])
          }
          if (typeof d.offset === 'number') nextOffsetRef.current = d.offset
          setOffset(nextOffsetRef.current)
          retries = 0
        } catch {
          /* skip malformed frame */
        }
      })
      source.addEventListener('end', () => {
        setEnded(true)
        source.close()
      })

      source.onerror = () => {
        if (gen !== genRef.current) return
        if (source.readyState === EventSource.CLOSED) {
          setEnded(true)
          return
        }
        source.close()
        retries += 1
        if (retries >= 4) {
          setStale(true)
          return
        }
        timer = setTimeout(open, Math.min(1000 * retries, 4000))
      }
    }

    open()
    return () => {
      genRef.current += 1
      if (timer) clearTimeout(timer)
      es?.close()
    }
  }, [enabled, slug, kind])

  return { lines, offset, ended, stale }
}