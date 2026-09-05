import { asNumber, asRecord, asString, type ReasoningTraceResponse, type RunExceptionsResponse, type RunResultsResponse } from '@/lib/api-types'

export type EvidenceRow = { label: string; value: string }
export type Outcome = { key: string; label: string; count: number }

export function readableKey(key: string) { return key.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase()) }
export function evidenceRows(value: unknown): EvidenceRow[] {
  const record = asRecord(value)
  if (!record) return value == null ? [] : [{ label: 'Value', value: String(value) }]
  return Object.entries(record).flatMap(([key, item]) => {
    if (item === null || item === undefined || typeof item === 'object') return []
    return [{ label: readableKey(key), value: typeof item === 'number' ? item.toLocaleString() : String(item) }]
  })
}
export function outcomes(value: unknown): Outcome[] {
  const record = asRecord(value)
  return record ? Object.entries(record).map(([key, item]) => ({ key, label: readableKey(key), count: asNumber(item) ?? 0 })) : []
}
export function scoreRows(value: unknown): EvidenceRow[] {
  const record = asRecord(value)
  if (!record) return []
  return ['accuracy', 'precision', 'recall', 'overall'].flatMap((key) => record[key] === undefined ? [] : [{ label: readableKey(key), value: formatMetric(record[key]) }])
}
export function formatMetric(value: unknown) { const n = asNumber(value); return n === null ? String(value ?? 'Not returned') : `${n <= 1 ? (n * 100).toFixed(2) : n.toFixed(2)}%` }
export function performanceRows(value: unknown) { return evidenceRows(value) }
export function traceRows(trace: ReasoningTraceResponse) { return [...evidenceRows(trace.history), ...evidenceRows(trace.final_decision)] }
export function exceptionRows(value: unknown) {
  if (!Array.isArray(value)) return []
  return value.map((item) => { const record = asRecord(item) ?? {}; return { id: asString(record.record_id ?? record.id) ?? 'Unknown record', stage: asString(record.stage) ?? 'Investigation', reason: asString(record.reason ?? record.detail) ?? 'No reason returned by backend.', rows: evidenceRows(record) } })
}
export function resultSummary(result: RunResultsResponse | undefined) {
  return { overall: result?.overall_match_rate, total: result?.total_records, outcomes: outcomes(result?.breakdown), full: scoreRows(result?.full_pipeline_scores), baseline: scoreRows(result?.baseline_scores), performance: performanceRows(result?.performance) }
}
export function exceptionSummary(exceptions: RunExceptionsResponse | undefined) { return exceptionRows(exceptions?.exceptions) }

export function delta(full: unknown, baseline: unknown) { const a = asNumber(full); const b = asNumber(baseline); return a === null || b === null ? null : a - b }
export function formatDelta(value: number | null) { return value === null ? 'Not returned' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}` }

export function traceSections(trace: ReasoningTraceResponse) {
  return { proposer: evidenceRows(trace.history), verifier: evidenceRows(asRecord(trace.final_decision)?.verifier), decision: evidenceRows(trace.final_decision), timing: evidenceRows({ wall_clock_time_sec: trace.wall_clock_time_sec, active_processing_time_sec: trace.active_processing_time_sec, reactive_throttle_wait_sec: trace.reactive_throttle_wait_sec, self_paced_wait_sec: trace.self_paced_wait_sec, other_pacing_wait_sec: trace.other_pacing_wait_sec }) }
}

export function labelStatus(value: unknown) { return asString(value)?.replaceAll('_', ' ') ?? 'Not returned' }
