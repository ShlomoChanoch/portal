import { useEffect, useRef } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { cn } from 'cn'
import { Rocket } from 'lucide-react'

const NAV: { to: string; label: string }[] = [
  { to: '/deploy', label: 'Deploy' },
  { to: '/deployments', label: 'Your Deployments' },
]

function navClass({ isActive }: { isActive: boolean }) {
  return cn(
    'rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none',
    isActive ? 'text-primary' : 'text-muted-foreground hover:text-foreground',
  )
}

/**
 * Global top nav over a single content column (DESIGN.md layout). There is no
 * session: the app is always signed in. Keyboard primitives: `/` joins the
 * Deploy screen and focuses the repo box; `d` and the `g d` chord open Your
 * Deployments (the chord is a no-op prefix followed by the same plain-`d`
 * behaviour, consumed so it never leaves stale state behind).
 */
export function AppShell() {
  const navigate = useNavigate()
  const gAt = useRef(0)
  const gTimer = useRef<number | undefined>(undefined)

  useEffect(() => {
    const burnG = () => {
      gAt.current = 0
      if (gTimer.current !== undefined) {
        window.clearTimeout(gTimer.current)
        gTimer.current = undefined
      }
    }
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const typing =
        !!target &&
        (target.isContentEditable ||
          ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
      const nav = (path: string) => {
        event.preventDefault()
        navigate(path)
      }
      const modifier = event.metaKey || event.ctrlKey || event.altKey

      if (event.key === '/') {
        nav('/deploy')
        requestAnimationFrame(() => {
          window.dispatchEvent(new CustomEvent('portal:focus-repo'))
        })
        return
      }
      if (typing || modifier) return

      // `g` arms a short-lived prefix; any following `d` is the chord.
      if (event.key === 'g') {
        gAt.current = Date.now()
        if (gTimer.current !== undefined) {
          window.clearTimeout(gTimer.current)
        }
        gTimer.current = window.setTimeout(() => {
          gAt.current = 0
          gTimer.current = undefined
        }, 800)
        return
      }

      if (event.key === 'd') {
        const chord = gAt.current !== 0 && Date.now() - gAt.current < 800
        burnG()
        nav('/deployments')
        if (chord) {
          return
        }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (gTimer.current !== undefined) {
        window.clearTimeout(gTimer.current)
      }
    }
  }, [navigate])

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex h-12 w-full max-w-5xl items-center gap-4 px-4">
          <NavLink to="/deploy" className="flex items-center gap-2 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none rounded-lg">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <Rocket className="size-4" />
            </span>
            <span className="text-[15px] font-semibold tracking-tight">Portal</span>
          </NavLink>

          <nav className="flex items-center gap-1" aria-label="Primary">
            {NAV.map((item) => (
              <NavLink key={item.to} to={item.to} className={navClass}>
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">
        <Outlet />
      </main>
    </div>
  )
}