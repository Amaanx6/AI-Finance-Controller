import type { ApiError, ReasoningTraceResponse, RunExceptionsResponse, RunResultsResponse, RunResultsSummary, RunStartResponse, RunStatusResponse, SessionRunsResponse } from '@/lib/api-types'

export class ApiRequestError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiRequestError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/${path}`, {
    ...init,
    cache: 'no-store',
    headers: { ...(init?.body ? { 'content-type': 'application/json' } : {}), ...init?.headers },
  })
  const body = await response.text()
  let parsed: unknown = null
  try { parsed = body ? JSON.parse(body) : null } catch { parsed = body }
  if (!response.ok) {
    const detail = parsed && typeof parsed === 'object' ? (parsed as ApiError).detail || (parsed as ApiError).message || (parsed as ApiError).error : null
    throw new ApiRequestError(response.status, detail || `Request failed (${response.status})`)
  }
  return parsed as T
}

export const api = {
  startRun: (idempotencyKey: string) => request<RunStartResponse>('run', { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey } }),
  status: (runId: string) => request<RunStatusResponse>(`status/${encodeURIComponent(runId)}`),
  latestResults: () => request<RunResultsResponse>('results/latest'),
  allResults: () => request<RunResultsSummary[]>('results'),
  sessionRuns: () => request<SessionRunsResponse>('session/runs'),
  results: (runId: string) => runId.startsWith('legacy:')
    ? request<RunResultsResponse>(`results/by-timestamp/${encodeURIComponent(runId.slice(7))}`)
    : request<RunResultsResponse>(`results/${encodeURIComponent(runId)}`),
  exceptions: (runId: string) => request<RunExceptionsResponse>(`exceptions/${encodeURIComponent(runId)}`),
  trace: (recordId: string, runId?: string) => request<ReasoningTraceResponse>(runId
    ? `reasoning-trace/${encodeURIComponent(runId)}/${encodeURIComponent(recordId)}`
    : `reasoning-trace/${encodeURIComponent(recordId)}`),
}
