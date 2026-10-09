export type DeploymentStatus =
  | 'submitted'
  | 'building'
  | 'running'
  | 'starting'
  | 'stopped'
  | 'failed'
  | 'denied'

export interface Deployment {
  id: string
  tenant_id: number
  repo_url: string
  port_override: number | null
  created_at: string
  status: DeploymentStatus | null
  reason: string | null
  phase: string | null
  mapped_port: number | null
  public_url: string | null
  image_ref: string | null
  idle_stop_at: string | null
  last_activity_at: string | null
  updated_at: string | null
  already_deployed?: boolean
  secrets?: Secret[]
}

export interface Secret {
  key: string
  masked?: boolean
}

export interface LogTuple {
  ts: string
  line: string
}

export interface LogsData {
  lines: LogTuple[]
  next_offset: number
  total: number
  status: DeploymentStatus | null
}

export interface AllowRule {
  kind: 'host' | 'prefix' | 'pattern' | string
  value: string
}

export interface QuotaPerUser {
  max_instances: number
  max_concurrent_builds: number
  cpu: number
  mem_mb: number
}

export interface PolicyConfig {
  allow_all: boolean
  allow_list: AllowRule[]
  quota: { per_user: QuotaPerUser }
  egress: unknown[]
  idle_stop_seconds: number
  halted: boolean
  halt_reason: string | null
}

export interface PolicyPatch {
  allow_all?: boolean
  allow_list?: AllowRule[]
  quota?: Partial<QuotaPerUser>
  egress?: unknown[]
  idle_stop_seconds?: number
}

export interface OperatorHealth {
  healthy: boolean
  components: { runner: 'ok' | 'degraded'; halted: boolean }
  runner_heartbeat: {
    ts: string
    executor: string | null
    builder: string | null
    pid: string | null
  } | null
  runtime: { executor: string | null; builder: string | null; real_images: boolean } | null
  active_users: number[]
  active_user_count: number
  resource_usage: {
    running_instances: number
    running_with_image: number
    running_without_image: number
    quota_hit_rate: number
    quota_hits: number
  }
  tenant_count: number
  per_tenant: { tenant_id: number; email: string; role: string; instances: number }[]
}

export interface AuditRow {
  id: number
  tenant_id: number
  actor: string
  deployment_id: string
  type: string
  created_at: string
  meta: Record<string, unknown> | null
}

export class PortalError extends Error {
  code: string
  status: number

  constructor(code: string, message: string, status: number) {
    super(message)
    this.name = 'PortalError'
    this.code = code
    this.status = status
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers:
      init?.body instanceof FormData ? init.headers : { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })

  let data: unknown = null
  try {
    data = await res.json()
  } catch {
    /* non-JSON body */
  }

  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } })?.error
    throw new PortalError(err?.code ?? 'http_error', err?.message ?? `Request failed (${res.status})`, res.status)
  }
  return data as T
}

export const api = {
  deployments: (offset = 0, limit = 25) =>
    request<{ deployments: Deployment[]; total: number; limit: number; offset: number }>(
      `/api/deployments?offset=${offset}&limit=${limit}`,
    ),
  deployment: (slug: string) => request<{ deployment: Deployment }>(`/api/deployments/${slug}`),
  deploy: (repoUrl: string, portOverride?: number | null) =>
    request<{ deployment: Deployment }>('/api/deployments', {
      method: 'POST',
      body: JSON.stringify({ repo_url: repoUrl, port_override: portOverride ?? null }),
    }),
  redeploy: (slug: string, repoUrl?: string, portOverride?: number | null) =>
    request<{ deployment: Deployment }>(`/api/deployments/${slug}/redeploy`, {
      method: 'POST',
      body: JSON.stringify({ repo_url: repoUrl, port_override: portOverride ?? null }),
    }),
  patchSettings: (slug: string, portOverride: number | null) =>
    request<{ deployment: Deployment }>(`/api/deployments/${slug}`, {
      method: 'PATCH',
      body: JSON.stringify({ port_override: portOverride }),
    }),
  start: (slug: string) => request<{ deployment: Deployment }>(`/api/deployments/${slug}/start`, { method: 'POST' }),
  stop: (slug: string) => request<{ deployment: Deployment }>(`/api/deployments/${slug}/stop`, { method: 'POST' }),
  deleteDeployment: (slug: string) =>
    request<{ deployment: Deployment; pending_delete: boolean }>(`/api/deployments/${slug}`, { method: 'DELETE' }),

  buildLogs: (slug: string, offset = 0, limit = 500) =>
    request<LogsData>(`/api/deployments/${slug}/build/logs?offset=${offset}&limit=${limit}`),
  runLogs: (slug: string, offset = 0, limit = 500) =>
    request<LogsData>(`/api/deployments/${slug}/run/logs?offset=${offset}&limit=${limit}`),

  secrets: (slug: string) => request<{ secrets: Secret[] }>(`/api/deployments/${slug}/secrets`),
  upsertSecret: (slug: string, key: string, value: string) =>
    request<{ secret: Secret }>(`/api/deployments/${slug}/secrets`, {
      method: 'PUT',
      body: JSON.stringify({ key, value }),
    }),
  deleteSecret: (slug: string, key: string) =>
    request<{ ok: boolean }>(`/api/deployments/${slug}/secrets/${encodeURIComponent(key)}`, { method: 'DELETE' }),

  operatorHealth: () => request<OperatorHealth>('/api/operator/health'),
  operatorConfig: () => request<{ config: PolicyConfig; revision: number }>('/api/operator/config'),
  putConfig: (patch: PolicyPatch) =>
    request<{ config: PolicyConfig; revision: number }>('/api/operator/config', {
      method: 'PUT',
      body: JSON.stringify(patch),
    }),
  rollbackConfig: (revision: number) =>
    request<{ config: PolicyConfig; revision: number }>('/api/operator/config/rollback', {
      method: 'POST',
      body: JSON.stringify({ revision }),
    }),
  forceStop: (slug: string) => request<{ ok: boolean }>(`/api/operator/instances/${slug}/force-stop`, { method: 'POST' }),
  halt: (reason: string) => request<{ ok: boolean; halted: boolean }>('/api/operator/halt', {
    method: 'POST',
    body: JSON.stringify({ confirm: true, reason }),
  }),
  haltClear: () => request<{ ok: boolean; halted: boolean }>('/api/operator/halt/clear', { method: 'POST' }),
  audit: (limit = 200) => request<{ audit: AuditRow[] }>(`/api/operator/audit?limit=${limit}`),
}