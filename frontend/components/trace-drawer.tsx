'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Clipboard, FileSearch, LoaderCircle, RefreshCw, X } from 'lucide-react'
import { usePrefetchTrace, useReasoningTrace } from '@/lib/queries'
import { asRecord, asString } from '@/lib/api-types'

type Props = { recordId?: string; runId?: string; open?: boolean; showTrigger?: boolean; onOpenChange?: (open: boolean) => void }

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return 'Not returned by backend.'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString() : 'Unavailable'
  return String(value)
}

function humanize(key: string) { return key.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase()) }

function EvidenceValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || typeof value !== 'object') return <span className="evidence-primitive">{displayValue(value)}</span>
  if (Array.isArray(value)) return value.length ? <ol className="evidence-array">{value.map((item, index) => <li key={index}><EvidenceValue value={item} /></li>)}</ol> : <span className="muted">None returned.</span>
  const record = asRecord(value)
  if (!record) return <span className="muted">Unavailable</span>
  return <dl className="evidence-object">{Object.entries(record).map(([key, item]) => <div key={key}><dt>{humanize(key)}</dt><dd><EvidenceValue value={item} /></dd></div>)}</dl>
}

function EvidenceBlock({ title, value }: { title: string; value: unknown }) { return <section className="trace-block"><h3>{title}</h3><EvidenceValue value={value} /></section> }

function historyEntry(entry: unknown, index: number) {
  const item = asRecord(entry) ?? {}
  const role = (asString(item.agent ?? item.role) ?? 'agent').toLowerCase()
  const result = asRecord(item.result) ?? {}
  return { item, role, attempt: asString(item.attempt) ?? String(index + 1), reasoning: asString(result.reasoning ?? item.reasoning), summary: asString(result.decision ?? result.status ?? result.confidence) ?? 'Evidence returned', candidates: result.matched_ledger_ids ?? result.candidate_ids }
}

function HistoryTimeline({ history }: { history: unknown }) {
  if (!Array.isArray(history) || history.length === 0) return <p className="muted">No proposer or verifier history returned.</p>
  return <div className="trace-timeline">{history.map((entry, index) => { const record = historyEntry(entry, index); const candidateIds = Array.isArray(record.candidates) ? record.candidates : []; return <article className={`trace-attempt trace-${record.role}`} key={`${record.role}-${record.attempt}-${index}`}><div className="trace-attempt-head"><span>{record.role === 'verifier' ? 'VERIFIER' : 'PROPOSER'} · ATTEMPT {record.attempt}</span><b>{record.summary}</b></div>{record.reasoning && <p>{record.reasoning}</p>}{candidateIds.length > 0 && <div className="trace-chip-row">{candidateIds.slice(0, 8).map((id) => <span key={String(id)}>{String(id)}</span>)}</div>}<details><summary>View returned evidence</summary><EvidenceValue value={record.item} /></details></article> })}</div>
}

function plainSummary(data: Record<string, unknown>, history: unknown) {
  const decision = asRecord(data.final_decision) ?? {}
  const status = String(data.final_status ?? decision.status ?? '').toLowerCase()
  const entries = Array.isArray(history) ? history.map((entry, index) => historyEntry(entry, index)) : []
  const verifierChallenge = entries.some((entry) => entry.role === 'verifier' && ['disagree', 'rejected', 'exception'].includes(entry.summary.toLowerCase()))
  const candidateCount = entries.reduce((count, entry) => count + (Array.isArray(entry.candidates) ? entry.candidates.length : 0), 0)
  if (status === 'exception' || status === 'dlq') return verifierChallenge ? 'This record was not auto-confirmed because the verifier challenged the proposed match and the available evidence did not resolve the conflict.' : 'This record needs review because the available evidence was not strong enough to support an automatic match.'
  if (verifierChallenge) return `The proposer found a possible match, but the verifier challenged it. Additional evidence was reviewed before reaching a ${status || 'final'} decision.`
  if (candidateCount > 1) return 'Several candidate records were compared before the system selected the strongest evidence-backed match.'
  return 'The record was reviewed against the available evidence and reached a final decision.'
}

function statusLabel(status: string): string {
  return status
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase()) || 'Not returned'
}

function statusTone(status: string): string {
  if (status === 'exception' || status === 'dlq') return 'attention'
  if (status === 'agent_confirmed' || status === 'fast_path_confirmed') return 'confirmed'
  return 'review'
}

export function TraceDrawer({ recordId = '', runId = '', open: controlledOpen, showTrigger = true, onOpenChange }: Props) {
  const [localOpen, setLocalOpen] = useState(false)
  const [localRecordId, setLocalRecordId] = useState(recordId)
  const open = controlledOpen ?? localOpen
  const id = recordId || localRecordId
  const trace = useReasoningTrace(id, runId, open && Boolean(id))
  const prefetch = usePrefetchTrace()
  const closeRef = useRef<HTMLButtonElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const reduced = useReducedMotion()
  const setOpen = useCallback((next: boolean) => { setLocalOpen(next); onOpenChange?.(next) }, [onOpenChange])
  useEffect(() => { if (!open) return; const previous = document.activeElement as HTMLElement | null; const trigger = triggerRef.current; const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); if (event.key !== 'Tab') return; const nodes = Array.from(document.querySelectorAll<HTMLElement>('.trace-drawer button, .trace-drawer input, .trace-drawer summary')).filter((node) => !node.hasAttribute('disabled')); const boundary = event.shiftKey ? nodes[0] : nodes.at(-1); if (nodes.length && document.activeElement === boundary) { event.preventDefault(); (event.shiftKey ? nodes.at(-1) : nodes[0])?.focus() } }; requestAnimationFrame(() => closeRef.current?.focus()); document.addEventListener('keydown', onKey); return () => { document.removeEventListener('keydown', onKey); requestAnimationFrame(() => trigger?.focus() || previous?.focus()) } }, [open, setOpen])
  const data = asRecord(trace.data)
  const decision = asRecord(data?.final_decision) ?? {}
  const decisionStatus = asString(data?.final_status ?? decision.status) ?? 'Not returned'
  const normalizedStatus = decisionStatus.toLowerCase()
  const decisionReason = asString(decision.reasoning)
  const trigger = id ? <button ref={triggerRef} className="trace-open" onMouseEnter={() => prefetch(id, runId)} onFocus={() => prefetch(id, runId)} onClick={() => setOpen(true)} aria-haspopup="dialog"><FileSearch data-icon="inline-start" /> Open case trace</button> : null
  return <>{showTrigger && <span className="trace-trigger">{trigger}</span>}<AnimatePresence>{open && <motion.div className="trace-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false) }} initial={reduced ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={reduced ? undefined : { opacity: 0 }}><motion.aside className={`trace-drawer liquid-focal trace-${statusTone(normalizedStatus)}`} role="dialog" aria-modal="true" aria-labelledby="trace-title" initial={reduced ? false : { x: '100%' }} animate={{ x: 0 }} exit={reduced ? undefined : { x: '100%' }}><button ref={closeRef} className="trace-close" onClick={() => setOpen(false)} aria-label="Close reasoning trace"><X /></button><div className="trace-drawer-heading"><span className="eyebrow">CASE REVIEW / EVIDENCE</span><span className="trace-heading-mark"><FileSearch size={15} /> Guided review</span></div><div className="trace-title-row"><div><p className="trace-kicker">Backend reasoning trace</p><h2 id="trace-title">{id || 'Reasoning trace'}</h2></div>{id && <button className="icon-btn" type="button" aria-label="Copy record ID" onClick={() => void navigator.clipboard?.writeText(id)}><Clipboard size={15} /></button>}</div>{recordId ? <div className="trace-record-context"><span className="micro-label">SELECTED CASE</span><strong>{id}</strong><p>This trace is linked to the record you opened from this run.</p></div> : <><label htmlFor="record-id">Look up a record</label><div className="trace-id-input"><input id="record-id" value={localRecordId} onChange={(event) => setLocalRecordId(event.target.value)} placeholder="Enter a record ID" autoComplete="off"/><span>ID</span></div><p className="trace-hint">Follow the evidence from candidate search to the final decision.</p></>}{trace.isFetching && <p className="trace-loading" aria-live="polite"><LoaderCircle className="spin" /> Loading evidence</p>}{trace.error && <div className="run-message" role="alert">{trace.error.message}<button className="trace-open" onClick={() => trace.refetch()}><RefreshCw /> Retry</button></div>}{data && <div className="trace-evidence"><section className="trace-decision-summary"><div className="trace-summary-top"><span className="micro-label">FINAL OUTCOME</span><span className={`trace-status ${statusTone(normalizedStatus)}`}>{statusLabel(normalizedStatus)}</span></div><strong>{normalizedStatus === 'exception' || normalizedStatus === 'dlq' ? 'Needs attention' : 'Decision reached'}</strong><p>{plainSummary(data, data.history)}</p>{decisionReason && <details><summary>Read the final rationale</summary><p>{decisionReason}</p></details>}</section><section className="trace-block"><div className="trace-block-heading"><div><span className="micro-label">HOW WE GOT HERE</span><h3>Investigation timeline</h3></div><span className="trace-count">{Array.isArray(data.history) ? `${data.history.length} ${data.history.length === 1 ? 'attempt' : 'attempts'}` : 'No attempts'}</span></div><div className="trace-steps"><span className="done">1 <b>Candidate search</b></span><span className={Array.isArray(data.history) && data.history.length ? 'done' : ''}>2 <b>Proposer recommendation</b></span><span className={Array.isArray(data.history) && data.history.some((item) => String(asRecord(item)?.agent ?? asRecord(item)?.role).toLowerCase() === 'verifier') ? 'done' : ''}>3 <b>Verifier challenge</b></span><span className={decisionReason ? 'done' : ''}>4 <b>Evidence review</b></span><span className="done">5 <b>Final decision</b></span></div><HistoryTimeline history={data.history} /></section><EvidenceBlock title="RECORD IDENTITY" value={{ record_id: data.record_id, provider: data.provider, handled_by_key: data.handled_by_key, final_status: data.final_status }} /><EvidenceBlock title="FINAL DECISION DETAILS" value={data.final_decision} /><EvidenceBlock title="TIMING" value={{ wall_clock_time_sec: data.wall_clock_time_sec, active_processing_time_sec: data.active_processing_time_sec, reactive_throttle_wait_sec: data.reactive_throttle_wait_sec, self_paced_wait_sec: data.self_paced_wait_sec, other_pacing_wait_sec: data.other_pacing_wait_sec }} /><details className="raw-evidence"><summary>View raw backend response</summary><pre>{JSON.stringify(data, null, 2)}</pre></details></div>}</motion.aside></motion.div>}</AnimatePresence></>
}
