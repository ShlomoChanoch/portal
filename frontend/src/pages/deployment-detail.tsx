import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { KeyRound, Play, RefreshCw, Square, Trash2, Settings2 } from 'lucide-react'
import { api, PortalError, type Deployment, type LogTuple } from '@/lib/api'
import { useSseLog } from '@/lib/sse'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { StatusBadge } from '@/components/status-badge'
import { UrlCopyPill } from '@/components/url-copy-pill'
import { LogView } from '@/components/log-view'
import { formatAgo, formatClock } from '@/lib/format'

const POLL_MS = 3000

const LIVE: Record<'build' | 'run', (s: DeploymentStatusLike) => boolean> = {
  build: (s) => s === 'building' || s === 'submitted',
  run: (s) => s === 'starting' || s === 'running',
}

type DeploymentStatusLike = Deployment['status']

function LogPanel({ slug, kind }: { slug: string; kind: 'build' | 'run' }) {
  const [deployment, setDeployment] = useState<Deployment | null>(null)
  const live = !!deployment?.status && LIVE[kind](deployment.status)
  const sse = useSseLog(slug, kind, live)
  const [json, setJson] = useState<LogTuple[]>([])
  const status = deployment?.status ?? null

  useEffect(() => {
    void api.deployment(slug).then(({ deployment }) => setDeployment(deployment)).catch(() => undefined)
  }, [slug])

  useEffect(() => {
    if (live) return
    let cancelled = false
    const fn = kind === 'build' ? api.buildLogs : api.runLogs
    fn(slug, 0, 2000)
      .then((data) => {
        if (!cancelled) setJson(data.lines)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [slug, kind, live, status])

  const showStreaming = live && !sse.stale
  const lines = showStreaming ? sse.lines : json.length > 0 ? json : sse.lines

  return (
    <LogView
      label={kind}
      lines={lines}
      streaming={showStreaming}
      status={status}
      className="h-80 sm:h-96"
    />
  )
}

function SecretsTab({ slug }: { slug: string }) {
  const [deployment, setDeployment] = useState<Deployment | null>(null)
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)

  const reload = useCallback(() => {
    void api.deployment(slug).then(({ deployment }) => setDeployment(deployment)).catch(() => undefined)
  }, [slug])

  useEffect(() => {
    reload()
  }, [reload])

  const add = async () => {
    const k = key.trim()
    if (!k || !value) return
    setBusy(true)
    try {
      await api.upsertSecret(slug, k, value)
      setKey('')
      setValue('')
      toast.success(`Saved ${k} — applied on next start.`)
      reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save the secret.')
    } finally {
      setBusy(false)
    }
  }

  const remove = async (k: string) => {
    try {
      await api.deleteSecret(slug, k)
      toast.success(`Removed ${k}.`)
      reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not delete the secret.')
    }
  }

  const secrets = deployment?.secrets ?? []

  return (
    <div className="space-y-4">
      <Card size="sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound className="size-4" />
            Secrets
          </CardTitle>
          <CardDescription>
            Injected into the container as environment variables on the next start. Values are
            masked and never returned.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {secrets.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">No secrets yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {secrets.map((s) => (
                <li key={s.key} className="flex items-center justify-between gap-3 py-2">
                  <span className="font-mono text-sm">{s.key}</span>
                  <div className="flex items-center gap-2">
                    {s.masked && <span className="text-xs text-muted-foreground">••••••</span>}
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Delete ${s.key}`}
                      onClick={() => void remove(s.key)}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <Separator />
          <div className="grid grid-cols-[1fr_auto_auto] items-end gap-2">
            <div className="space-y-1">
              <Label htmlFor="secret-key">Key</Label>
              <Input id="secret-key" value={key} onChange={(e) => setKey(e.target.value)} placeholder="DATABASE_URL" />
            </div>
            <div className="space-y-1">
              <Label htmlFor="secret-value">Value</Label>
              <Input
                id="secret-value"
                type="password"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="postgres://…"
              />
            </div>
            <Button onClick={() => void add()} disabled={busy || !key.trim() || !value}>
              Add
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

export function DeploymentDetailPage() {
  const { slug = '' } = useParams()
  const navigate = useNavigate()
  const [deployment, setDeployment] = useState<Deployment | null>(null)
  const [redeployOpen, setRedeployOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [repoUrl, setRepoUrl] = useState('')
  const [portOverride, setPortOverride] = useState('')
  const [busy, setBusy] = useState(false)
  const prevStatus = useRef<DeploymentStatusLike>(null)
  const initialised = useRef(false)

  const reload = useCallback(() => {
    void api.deployment(slug).then(({ deployment }) => {
      setDeployment(deployment)
      if (!initialised.current) {
        initialised.current = true
        setRepoUrl(deployment.repo_url)
        setPortOverride(deployment.port_override ? String(deployment.port_override) : '')
      }
      const prev = prevStatus.current
      prevStatus.current = deployment.status
      if (deployment.status === 'running' && prev && prev !== 'running' && deployment.public_url) {
        toast.success('Running — your app is live.', { description: deployment.public_url })
      }
    }).catch(() => undefined)
  }, [slug])

  useEffect(() => {
    void reload()
    const timer = setInterval(() => void reload(), POLL_MS)
    return () => clearInterval(timer)
  }, [reload])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault()
        setRedeployOpen(true)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  if (!deployment) {
    return (
      <section className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-40 w-full" />
      </section>
    )
  }

  const status = deployment.status
  const isRunning = status === 'running'
  const canStop = !!status && ['running', 'starting', 'building', 'submitted'].includes(status)

  const doStop = async () => {
    try {
      await api.stop(slug)
      toast.info('Stopping…')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not stop.')
    }
  }

  const doStart = async () => {
    try {
      await api.start(slug)
      toast.info('Starting…')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not start.')
    }
  }

  const doDelete = async () => {
    setBusy(true)
    try {
      await api.deleteDeployment(slug)
      setDeleteOpen(false)
      toast.success('Deployment deleted — quota slot freed.')
      navigate('/deployments')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not delete.')
    } finally {
      setBusy(false)
    }
  }

  const doRedeploy = async () => {
    setBusy(true)
    try {
      const port = portOverride === '' ? null : Number(portOverride)
      await api.redeploy(slug, repoUrl, port)
      setRedeployOpen(false)
      toast.success('Redeploying — build restarted.')
      void reload()
    } catch (e) {
      toast.error(e instanceof PortalError ? e.message : 'Could not redeploy.')
    } finally {
      setBusy(false)
    }
  }

  const doSettings = async () => {
    setBusy(true)
    try {
      const port = portOverride === '' ? null : Number(portOverride)
      await api.patchSettings(slug, port)
      setSettingsOpen(false)
      toast.success('Settings saved — applied on next start.')
      void reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save settings.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-6">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-mono text-2xl font-semibold">{slug}</h2>
          <StatusBadge status={status} />
          {deployment.reason && status === 'failed' && (
            <span className="font-mono text-xs text-destructive">{deployment.reason}</span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground">
          <span className="font-mono text-xs">{deployment.repo_url}</span>
          <span>created {formatAgo(deployment.created_at)}</span>
          {deployment.updated_at && <span>updated {formatAgo(deployment.updated_at)}</span>}
          {deployment.last_activity_at && <span>active {formatAgo(deployment.last_activity_at)}</span>}
          {deployment.mapped_port && (
            <span className="font-mono text-xs">port {deployment.mapped_port}</span>
          )}
          {deployment.idle_stop_at && status !== 'stopped' && (
            <span>auto-stops {formatClock(deployment.idle_stop_at)}</span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {isRunning && deployment.public_url && <UrlCopyPill url={deployment.public_url} />}
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setRedeployOpen(true)}
              aria-label={`Redeploy ${slug}`}
              title="Redeploy (Cmd/Ctrl+Enter)"
            >
              <RefreshCw />
              Redeploy
            </Button>
            <Button variant="outline" size="sm" onClick={() => setSettingsOpen(true)}>
              <Settings2 />
              Settings
            </Button>
            {canStop ? (
              <Button variant="destructive" size="sm" onClick={() => void doStop()}>
                <Square />
                Stop
              </Button>
            ) : (
              (status === 'stopped' || status === 'failed' || status === 'denied') && (
                <Button size="sm" onClick={() => void doStart()}>
                  <Play />
                  Start
                </Button>
              )
            )}
            {(status === 'stopped' || status === 'failed' || status === 'denied') && (
              <Button
                variant="outline"
                size="sm"
                className="text-destructive"
                onClick={() => setDeleteOpen(true)}
                aria-label={`Delete ${slug}`}
                title="Delete permanently and free its quota slot"
              >
                <Trash2 />
                Delete
              </Button>
            )}
          </div>
        </div>
      </header>

      <Tabs defaultValue="build">
        <TabsList>
          <TabsTrigger value="build">Build</TabsTrigger>
          <TabsTrigger value="run">Run</TabsTrigger>
          <TabsTrigger value="secrets">Secrets</TabsTrigger>
        </TabsList>
        <TabsContent value="build">
          <LogPanel slug={slug} kind="build" />
        </TabsContent>
        <TabsContent value="run">
          <LogPanel slug={slug} kind="run" />
        </TabsContent>
        <TabsContent value="secrets">
          <SecretsTab slug={slug} />
        </TabsContent>
      </Tabs>

      <Dialog open={redeployOpen} onOpenChange={setRedeployOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Redeploy {slug}</DialogTitle>
            <DialogDescription>
              Rebuilds from the repo URL and restarts the container. A new build replays here.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="redeploy-repo">Repo URL</Label>
              <Input
                id="redeploy-repo"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                spellCheck={false}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="redeploy-port">Port override (optional)</Label>
              <Input
                id="redeploy-port"
                type="number"
                value={portOverride}
                onChange={(e) => setPortOverride(e.target.value)}
                placeholder="e.g. 8080"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRedeployOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => void doRedeploy()} disabled={busy || !repoUrl.trim()}>
              {busy ? 'Redeploying…' : 'Redeploy'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Settings</DialogTitle>
            <DialogDescription>
              Per-Deployment settings that apply at the next (re)start.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1">
            <Label htmlFor="settings-port">Port override</Label>
            <Input
              id="settings-port"
              type="number"
              value={portOverride}
              onChange={(e) => setPortOverride(e.target.value)}
              placeholder="Leave empty for automatic"
            />
            <p className="text-xs text-muted-foreground">Leave empty to auto-assign a port.</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSettingsOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => void doSettings()} disabled={busy}>
              {busy ? 'Saving…' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {slug}?</DialogTitle>
            <DialogDescription>
              Permanently removes this Deployment and its logs, and frees its quota slot. The git
              history of the underlying repo is untouched. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => void doDelete()} disabled={busy}>
              {busy ? 'Deleting…' : 'Delete permanently'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}