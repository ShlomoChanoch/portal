import { cn } from 'cn'
import { Badge } from '@/components/ui/badge'
import type { DeploymentStatus } from '@/lib/api'

const LABELS: Record<DeploymentStatus, string> = {
  submitted: 'Submitted',
  building: 'Building',
  running: 'Running',
  starting: 'Starting',
  stopped: 'Stopped',
  failed: 'Failed',
  denied: 'Denied',
}

function LiveBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex h-5 w-fit items-center gap-1.5 rounded-full bg-accent px-2 py-0.5 text-xs font-medium whitespace-nowrap text-accent-foreground',
        className,
      )}
    >
      <span className="relative flex size-1.5">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-75 motion-reduce:animate-none" />
        <span className="relative inline-flex size-1.5 rounded-full bg-current" />
      </span>
      Running
    </span>
  )
}

export function StatusBadge({
  status,
  className,
}: {
  status: DeploymentStatus | null | undefined
  className?: string
}) {
  if (status === 'running') return <LiveBadge className={className} />
  if (!status) return <Badge variant="outline" className={className}>Unknown</Badge>

  const variant: 'secondary' | 'outline' | 'destructive' =
    status === 'submitted' || status === 'building' || status === 'starting'
      ? 'secondary'
      : status === 'failed'
        ? 'destructive'
        : 'outline'

  return (
    <Badge variant={variant} className={className}>
      {LABELS[status]}
    </Badge>
  )
}