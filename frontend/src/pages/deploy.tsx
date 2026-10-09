import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Loader2 } from 'lucide-react'
import { api, PortalError } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

export function DeployPage() {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const [repo, setRepo] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const onFocus = () => inputRef.current?.focus()
    window.addEventListener('portal:focus-repo', onFocus)
    return () => window.removeEventListener('portal:focus-repo', onFocus)
  }, [])

  const deploy = async () => {
    const url = repo.trim()
    if (!url) {
      toast.info('Paste a repo URL first.')
      return
    }
    setBusy(true)
    try {
      const { deployment } = await api.deploy(url)
      navigate(`/deployments/${deployment.id}`)
    } catch (e) {
      if (e instanceof PortalError) {
        toast.error(e.message, { description: e.code })
      } else {
        toast.error('Could not start the build.')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="mx-auto w-full max-w-xl space-y-8">
      <header className="text-center">
        <h1 className="font-heading text-display font-semibold tracking-tight">
          Paste. Build. Run.
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Give Portal a repo URL and get a running container and a shareable address.
        </p>
      </header>

      <form
        className="flex flex-col gap-3 sm:flex-row"
        onSubmit={(event) => {
          event.preventDefault()
          void deploy()
        }}
      >
        <Input
          ref={inputRef}
          value={repo}
          onChange={(event) => setRepo(event.target.value)}
          placeholder="https://github.com/you/your-app"
          className="h-10 flex-1 border-console/20 bg-input/30 px-3 font-mono text-[15px]"
          aria-label="Repo URL"
          spellCheck={false}
          autoComplete="off"
        />
        <Button type="submit" size="lg" className="h-10 shrink-0" disabled={busy || !repo.trim()}>
          {busy ? <Loader2 className="animate-spin" /> : null}
          {busy ? 'Deploying…' : 'Deploy'}
        </Button>
      </form>
    </section>
  )
}