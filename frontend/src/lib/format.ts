const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
const ABSOLUTE = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

export function formatAgo(iso: string | null | undefined): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const diffSeconds = Math.round((then - Date.now()) / 1000)
  const abs = Math.abs(diffSeconds)
  if (abs < 60) return RELATIVE.format(diffSeconds, 'second')
  if (abs < 3600) return RELATIVE.format(Math.round(diffSeconds / 60), 'minute')
  if (abs < 86400) return RELATIVE.format(Math.round(diffSeconds / 3600), 'hour')
  return ABSOLUTE.format(then)
}

export function formatLogTs(ts: string): string {
  const t = new Date(ts)
  if (Number.isNaN(t.getTime())) return ts
  return t.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    fractionalSecondDigits: 3,
  })
}

export function formatClock(ts: string): string {
  const t = new Date(ts)
  if (Number.isNaN(t.getTime())) return ''
  return t.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}