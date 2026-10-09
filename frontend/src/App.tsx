import { Suspense, lazy } from 'react'
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/components/app-shell'

// Route-level code splitting: every page (and the UI components it pulls in)
// loads on demand instead of shipping one large entry chunk.
const DeployPage = lazy(() => import('@/pages/deploy').then((m) => ({ default: m.DeployPage })))
const DeploymentsPage = lazy(
  () => import('@/pages/deployments').then((m) => ({ default: m.DeploymentsPage })),
)
const DeploymentDetailPage = lazy(
  () => import('@/pages/deployment-detail').then((m) => ({ default: m.DeploymentDetailPage })),
)

function PageFallback() {
  return (
    <div className="flex justify-center py-16 text-sm text-muted-foreground">
      Loading…
    </div>
  )
}

export default function App() {
  return (
    <HashRouter>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<Navigate to="/deploy" replace />} />
            <Route path="/deploy" element={<DeployPage />} />
            <Route path="/deployments" element={<DeploymentsPage />} />
            <Route path="/deployments/:slug" element={<DeploymentDetailPage />} />
            <Route path="*" element={<Navigate to="/deploy" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </HashRouter>
  )
}