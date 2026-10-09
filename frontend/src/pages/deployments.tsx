import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus } from 'lucide-react'
import { api, type Deployment } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { StatusBadge } from '@/components/status-badge'
import { formatAgo } from '@/lib/format'

const LIMIT = 25
/* The list API caps `limit` at 50, so a window may need more than one request. */
const MAX_PAGE = 50

export function DeploymentsPage() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<Deployment[]>([])
  const [total, setTotal] = useState(0)
  const [size, setSize] = useState(LIMIT)
  const [loading, setLoading] = useState(true)

  /* Fetch the window [0, size) and replace it in one go. Appending would let the
   * 4s poll duplicate rows or reset a grown list back to the first page. */
  const load = useCallback(async (windowSize: number) => {
    try {
      const page: Deployment[] = []
      for (let offset = 0; offset < windowSize; offset += MAX_PAGE) {
        const data = await api.deployments(offset, Math.min(MAX_PAGE, windowSize - offset))
        page.push(...data.deployments)
        setTotal(data.total)
        if (data.deployments.length === 0 || page.length >= data.total) break
      }
      setRows(page)
    } catch {
      /* ignore transient errors; poll retries */
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(size)
    const timer = setInterval(() => void load(size), 4000)
    return () => clearInterval(timer)
  }, [load, size])

  if (loading) {
    return (
      <section className="space-y-4">
        <div className="h-8 w-56 rounded bg-muted/60" />
        <div className="h-24 rounded-lg bg-muted/50" />
      </section>
    )
  }

  if (rows.length === 0) {
    return (
      <section className="flex min-h-[50vh] flex-col items-center justify-center gap-4 text-center">
        <h2 className="font-heading text-display-sm font-semibold">Nothing deployed yet</h2>
        <p className="max-w-sm text-sm text-muted-foreground">
          Paste a repo URL on the Deploy screen and watch it go from submitted to serving traffic.
        </p>
        <Button onClick={() => navigate('/deploy')}>
          <Plus />
          Deploy your first app
        </Button>
      </section>
    )
  }

  const loadMore = () => {
    if (rows.length < total) setSize((n) => n + LIMIT)
  }

  return (
    <section className="space-y-4">
      <header>
        <h2 className="font-heading text-display font-semibold">Your Deployments</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {total} {total === 1 ? 'app' : 'apps'} · newest first
        </p>
      </header>

      <Card size="sm">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Deployment</TableHead>
              <TableHead>Repo</TableHead>
              <TableHead className="hidden sm:table-cell">Status</TableHead>
              <TableHead className="hidden md:table-cell">Port</TableHead>
              <TableHead className="text-right">Updated</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((d) => (
              <TableRow key={d.id} className="cursor-pointer" onClick={() => navigate(`/deployments/${d.id}`)}>
                <TableCell>
                  <Link
                    to={`/deployments/${d.id}`}
                    onClick={(event) => event.stopPropagation()}
                    className="font-mono text-sm font-medium text-primary underline-offset-4 hover:underline"
                  >
                    {d.id}
                  </Link>
                </TableCell>
                <TableCell className="max-w-48 truncate font-mono">{d.repo_url}</TableCell>
                <TableCell className="hidden sm:table-cell">
                  <StatusBadge status={d.status} />
                </TableCell>
                <TableCell className="hidden font-mono text-muted-foreground md:table-cell">
                  {d.mapped_port ?? (d.port_override ? `→ ${d.port_override}` : '—')}
                </TableCell>
                <TableCell className="text-right text-muted-foreground">
                  {formatAgo(d.updated_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      {rows.length > 0 && (
        <ul className="grid gap-3 sm:hidden">
          {rows.map((d) => (
            <li key={d.id}>
              <Card
                size="sm"
                onClick={() => navigate(`/deployments/${d.id}`)}
                className="cursor-pointer"
              >
                <CardHeader>
                  <CardTitle className="font-mono text-sm">{d.id}</CardTitle>
                  <CardDescription className="truncate font-mono text-xs">{d.repo_url}</CardDescription>
                </CardHeader>
                <CardContent className="flex items-center justify-between">
                  <StatusBadge status={d.status} />
                  <span className="text-xs text-muted-foreground">{formatAgo(d.updated_at)}</span>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}

      {rows.length < total && (
        <div className="flex justify-center">
          <Button variant="outline" onClick={loadMore}>
            Load more
          </Button>
        </div>
      )}
    </section>
  )
}