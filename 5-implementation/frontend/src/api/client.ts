// The only module that talks HTTP. Components call these functions, never fetch.
import type {
  AuditFilters,
  AuditPage,
  AuditRecord,
  ChatCompletion,
  ChatMessage,
  Health,
  Metrics,
  PolicyDocument,
  PolicyError,
  PolicySaved,
  PolicyValidation,
} from './types'

export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const PLAYGROUND_KEY: string = import.meta.env.VITE_PLAYGROUND_API_KEY ?? ''
export const PLAYGROUND_MODEL: string = import.meta.env.VITE_PLAYGROUND_MODEL ?? 'gemma4'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** A 422 from PUT /api/policy: the backend rejected the policy and kept the old one. */
export class PolicyRejected extends Error {
  readonly errors: PolicyError[]

  constructor(errors: PolicyError[]) {
    super(`policy rejected: ${errors.length} error(s)`)
    this.errors = errors
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    throw new ApiError(res.status, `${init?.method ?? 'GET'} ${path}: ${res.status} ${await res.text()}`)
  }
  return (await res.json()) as T
}

function query(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') q.set(k, String(v))
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}

export const getHealth = () => request<Health>('/api/health')

export const getMetrics = (since?: string) => request<Metrics>(`/api/metrics${query({ since })}`)

export const getAudit = (filters: AuditFilters, limit = 200, offset = 0) =>
  request<AuditPage>(`/api/audit${query({ ...filters, limit, offset })}`)

/** A link, not a fetch: the browser downloads the file the backend sends. */
export const auditExportUrl = (filters: AuditFilters, format: 'json' | 'csv') =>
  `${API_URL}/api/audit/export${query({ ...filters, format })}`

export const getPolicy = () => request<PolicyDocument>('/api/policy')

export const validatePolicy = (yaml: string) =>
  request<PolicyValidation>('/api/policy/validate', {
    method: 'POST',
    body: JSON.stringify({ yaml }),
  })

export async function savePolicy(yaml: string): Promise<PolicySaved> {
  const res = await fetch(`${API_URL}/api/policy`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ yaml }),
  })
  if (res.status === 422) {
    const body = (await res.json()) as { errors: PolicyError[] }
    throw new PolicyRejected(body.errors)
  }
  if (!res.ok) throw new ApiError(res.status, `PUT /api/policy: ${res.status} ${await res.text()}`)
  return (await res.json()) as PolicySaved
}

/** The playground goes through the same proxy as any agent, with the playground caller's key. */
export const chat = (messages: ChatMessage[]) =>
  request<ChatCompletion>('/v1/chat/completions', {
    method: 'POST',
    headers: { Authorization: `Bearer ${PLAYGROUND_KEY}` },
    body: JSON.stringify({ model: PLAYGROUND_MODEL, messages }),
  })

/** Audit rows as the backend writes them (server-sent events). Returns a function that closes
 * the stream. The browser reconnects by itself and resumes after the last row it got. */
export function subscribeEvents(
  onRecord: (record: AuditRecord) => void,
  onOpen: (open: boolean) => void,
): () => void {
  const source = new EventSource(`${API_URL}/api/events`)
  source.onopen = () => onOpen(true)
  source.onerror = () => onOpen(false)
  source.onmessage = (e: MessageEvent<string>) => onRecord(JSON.parse(e.data) as AuditRecord)
  return () => source.close()
}
