import { useEffect, useRef, useState } from 'react'
import { cn } from 'cn'
import { ArrowDown, Copy } from 'lucide-react'
import { formatLogTs } from '@/lib/format'
import type { DeploymentStatus } from '@/lib/api'

interface LogViewProps {
  label: string
  lines: { ts: string; line: string }[]
  streaming?: boolean
  status?: DeploymentStatus | null
  className?: string
}

/**
 * The console pane (DESIGN.md log-view): fixed near-black navy in both modes,
 * monospace lines, muted timestamps, live tail in console-active, autoscroll
 * that pins to the tail and resumes with Esc or the Resume button. Liveness is
 * conveyed by the text "live" suffix, not color alone.
 */
export function LogView({ label, lines, streaming = false, status, className }: LogViewProps) {
  const viewportRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)
  const [pinned, setPinned] = useState(true)
  const [hasNew, setHasNew] = useState(false)

  useEffect(() => {
    if (pinnedRef.current && viewportRef.current) {
      viewportRef.current.scrollTop = viewportRef.current.scrollHeight
      setHasNew(false)
    } else if (lines.length > 0) {
      setHasNew((prev) => prev || true)
    }
  }, [lines])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && viewportRef.current) {
        const el = viewportRef.current
        el.scrollTop = el.scrollHeight
        pinnedRef.current = true
        setPinned(true)
        setHasNew(false)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const onScroll = () => {
    const el = viewportRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    pinnedRef.current = atBottom
    setPinned(atBottom)
    if (atBottom) setHasNew(false)
  }

  const resume = () => {
    const el = viewportRef.current
    if (el) {
      el.scrollTo({ top: el.scrollHeight })
      pinnedRef.current = true
      setPinned(true)
      setHasNew(false)
    }
  }

  const copyLog = async () => {
    try {
      await navigator.clipboard.writeText(lines.map((l) => l.line).join('\n'))
    } catch {
      /* clipboard unavailable */
    }
  }

  const lastLine = lines.length > 0 ? lines[lines.length - 1].line : ''

  return (
    <div
      className={cn(
        'flex flex-col overflow-hidden rounded-lg bg-console text-console-fg ring-1 ring-white/5',
        className,
      )}
    >
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-1.5">
        <div className="flex items-center gap-2 font-mono text-xs text-console-muted">
          <span className="uppercase tracking-wider">{label}</span>
          <span aria-hidden="true">·</span>
          <span>{lines.length} lines</span>
          <span
            className={cn('text-console-active', !streaming && 'invisible')}
            role="status"
            aria-live="polite"
          >
            live
          </span>
        </div>
        <button
          type="button"
          onClick={() => void copyLog()}
          aria-label="Copy log"
          title="Copy log"
          className="inline-flex min-h-6 min-w-6 items-center justify-center rounded text-console-muted transition-colors hover:bg-white/10 hover:text-console-fg focus-visible:ring-3 focus-visible:ring-console-active/60 focus-visible:outline-none"
        >
          <Copy className="size-3.5" />
        </button>
      </div>

      <div
        ref={viewportRef}
        onScroll={onScroll}
        className="min-h-32 flex-1 overflow-y-auto overscroll-contain p-3"
        role="log"
        aria-label={`${label} output`}
      >
        {lines.length === 0 ? (
          <p className="py-8 text-center font-mono text-xs text-console-muted">
            {streaming ? 'Waiting for output…' : 'No output yet.'}
          </p>
        ) : (
          <div className="relative">
            {lines.map((l, i) => (
              <div
                key={i}
                className={cn(
                  'grid grid-cols-[5.5rem_1fr] gap-3 font-mono text-[13px] leading-relaxed',
                  i === lines.length - 1 && hasNew && 'text-console-active',
                )}
              >
                <span className="select-none truncate text-console-muted">{formatLogTs(l.ts)}</span>
                <span className="min-w-0 whitespace-pre-wrap break-words">{l.line}</span>
              </div>
            ))}
            <span className="sr-only" aria-live="polite">
              {lastLine}
            </span>
          </div>
        )}
      </div>

      {hasNew && !pinned && (
        <div className="flex items-center justify-center p-2">
          <button
            type="button"
            onClick={resume}
            className="inline-flex h-7 items-center gap-1.5 rounded-full bg-console-active px-3 text-xs font-medium text-white shadow-sm transition-colors hover:brightness-110 focus-visible:ring-3 focus-visible:ring-console-active/60 focus-visible:outline-none"
          >
            <ArrowDown className="size-3.5" />
            Resume live
            {status ? ` · ${status}` : ''}
          </button>
        </div>
      )}
    </div>
  )
}