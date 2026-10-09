import { useState } from 'react'
import { cn } from 'cn'
import { Check, Copy, ExternalLink } from 'lucide-react'

interface UrlCopyPillProps {
  url: string
  className?: string
}

/**
 * The payoff moment: an emerald pill carrying the Public URL in monospace,
 * with Copy and Open actions on the console surface (DESIGN.md url-copy). Only
 * rendered once an Instance is Running.
 */
export function UrlCopyPill({ url, className }: UrlCopyPillProps) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <div
      className={cn(
        'inline-flex max-w-full items-center gap-1 rounded-lg bg-accent py-1 pr-1 pl-2 text-accent-foreground',
        className,
      )}
    >
      <span className="min-w-0 truncate font-mono text-[15px] leading-tight font-medium">{url}</span>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label="Copy URL"
        title="Copy URL"
        className="inline-flex min-h-6 min-w-6 shrink-0 items-center justify-center rounded-md bg-console text-console-fg transition-colors hover:bg-console/90 focus-visible:ring-3 focus-visible:ring-console-active/60 focus-visible:outline-none"
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </button>
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        aria-label="Open URL in a new tab"
        title="Open in a new tab"
        className="inline-flex min-h-6 min-w-6 shrink-0 items-center justify-center rounded-md bg-console text-console-fg transition-colors hover:bg-console/90 focus-visible:ring-3 focus-visible:ring-console-active/60 focus-visible:outline-none"
      >
        <ExternalLink className="size-3.5" />
      </a>
    </div>
  )
}