import type { components } from '@/lib/generated-api-types'

export interface RunStartResponse {
  run_id: string
  status: string
  provider_mode: string
}

export interface SessionRunsResponse {
  run_ids: string[]
  runs: RunResultsSummary[]
}

export interface RunStatusResponse {
  status: string
  records_processed?: number | null
  total_records?: number | null
  fast_path_resolved_so_far?: number | null
  agent_resolved_so_far?: number | null
  error?: string | null
}

export interface RunResultsResponse {
  run_id?: string | null
  run_started_at?: string | null
  timestamp?: string | null
  provider_mode?: string | null
  total_records?: number | null
  overall_match_rate?: number | null
  breakdown?: {
    fast_path_confirmed?: number | null
    fast_path_flagged?: number | null
    agent_confirmed?: number | null
    exception?: number | null
    [key: string]: number | null | undefined
  } | null
  financial_impact?: Record<string, unknown> | null
  reproducibility?: Record<string, unknown> | null
  full_pipeline_scores?: unknown
  baseline_scores?: unknown
  performance?: unknown
  ground_truth_orphan_rows_excluded?: unknown
  dead_letter_queue?: unknown
  caveats?: unknown
  dataset_manifest?: Record<string, Record<string, unknown>> | null
  status?: string | null
}

export type RunResultsSummary = Pick<RunResultsResponse, 'run_id' | 'run_started_at' | 'timestamp' | 'provider_mode' | 'total_records' | 'overall_match_rate' | 'breakdown' | 'status'>

export interface ReasoningTraceResponse {
  record_id: string
  handled_by_key?: string | null
  provider?: string | null
  history?: unknown
  final_status?: string | null
  final_decision?: unknown
  wall_clock_time_sec?: number | null
  active_processing_time_sec?: number | null
  reactive_throttle_wait_sec?: number | null
  self_paced_wait_sec?: number | null
  other_pacing_wait_sec?: number | null
}

export interface RunExceptionsResponse {
  run_id: string
  exceptions?: ExceptionRecord[] | null
  dead_letter_queue?: ExceptionRecord[] | null
}

export interface ExceptionRecord {
  record_id?: string | null
  id?: string | null
  stage?: string | null
  reason?: string | null
  provider?: string | null
  detail?: string | null
}

export type ApiError = { detail?: string; message?: string; error?: string }

export function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

export function normalizeStatus(status: unknown): string {
  return typeof status === 'string' ? status.toLowerCase() : 'unknown'
}

export function isTerminalStatus(status: unknown): boolean {
  return ['completed', 'failed', 'cancelled'].includes(normalizeStatus(status))
}

export function isKnownStatus(status: unknown): boolean {
  return ['starting', 'queued', 'running', 'completed', 'failed'].includes(normalizeStatus(status))
}

export function extractRecordIds(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    const record = asRecord(item)
    const id = record?.record_id ?? record?.id
    return typeof id === 'string' ? [id] : []
  })
}

/** The generated FastAPI OpenAPI schema, refreshed by `pnpm generate:api`. */
export type GeneratedApiTypes = components
