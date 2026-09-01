'use client'

import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { FileSearch, LoaderCircle, RefreshCw, X } from 'lucide-react'
import { usePrefetchTrace, useReasoningTrace } from '@/lib/queries'
import { asRecord, asString } from '@/lib/api-types'

type Props = { recordId?: string; open?: boolean; onOpenChange?: (open: boolean) => void }

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return 'Not returned by backend.'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString() : 'Unavailable'
  return String(value)
}

function humanize(key: string) { return key.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase()) }

/** Render unknown backend evidence without flattening it into a JSON wall. */
function EvidenceValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || typeof value !== 'object') return <span className="evidence-primitive">{displayValue(value)}</span>
  if (Array.isArray(value)) return value.length ? <ol className="evidence-array">{value.map((item, index) => <li key={index}><EvidenceValue value={item} /></li>)}</ol> : <span className="muted">None returned.</span>
  const record = asRecord(value)
  if (!record) return <span className="muted">Unavailable</span>
  return <dl className="evidence-object">{Object.entries(record).map(([key, item]) => <div key={key}><dt>{humanize(key)}</dt><dd><EvidenceValue value={item} /></dd></div>)}</dl>
}

function EvidenceBlock({ title, value }: { title: string; value: unknown }) { return <section className="trace-block"><h3>{title}</h3><EvidenceValue value={value} /></section> }

function HistoryTimeline({ history }: { history: unknown }) {
  if (!Array.isArray(history) || history.length === 0) return <p className="muted">No proposer or verifier history returned.</p>
  return <div className="trace-timeline">{history.map((entry, index) => {
    const item = asRecord(entry) ?? {}
    const role = asString(item.agent ?? item.role) ?? 'agent'
    const attempt = asString(item.attempt) ?? String(index + 1)
    const result = asRecord(item.result)
    const summary = asString(result?.decision ?? result?.status ?? result?.confidence) ?? 'Evidence returned'
    const reasoning = asString(result?.reasoning ?? item.reasoning)
    const candidateIds = result?.matched_ledger_ids ?? result?.candidate_ids
    return <article className="trace-attempt" key={`${role}-${attempt}-${index}`}><div className="trace-attempt-head"><span>{role === 'verifier' ? 'VERIFIER' : 'PROPOSER'} · ATTEMPT {attempt}</span><b>{summary}</b></div>{reasoning && <p>{reasoning}</p>} {Array.isArray(candidateIds) && candidateIds.length > 0 && <div className="trace-chip-row">{candidateIds.slice(0, 8).map((id) => <span key={String(id)}>{String(id)}</span>)}</div>}<details><summary>Show returned evidence</summary><EvidenceValue value={entry} /></details></article>
  })}</div>
}

export function TraceDrawer({ recordId = '', open: controlledOpen, onOpenChange }: Props) {
  const [localOpen, setLocalOpen] = useState(false)
  const [localRecordId, setLocalRecordId] = useState(recordId)
  const open = controlledOpen ?? localOpen
  const id = recordId || localRecordId
  const trace = useReasoningTrace(id, open && Boolean(id))
  const prefetch = usePrefetchTrace()
  const closeRef = useRef<HTMLButtonElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const reduced = useReducedMotion()
  const setOpen = (next: boolean) => { setLocalOpen(next); onOpenChange?.(next) }

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
      if (event.key !== 'Tab') return
      const nodes = Array.from(document.querySelectorAll<HTMLElement>('.trace-drawer button, .trace-drawer input')).filter((node) => !node.hasAttribute('disabled'))
      const boundary = event.shiftKey ? nodes[0] : nodes.at(-1)
      if (nodes.length && document.activeElement === boundary) { event.preventDefault(); (event.shiftKey ? nodes.at(-1) : nodes[0])?.focus() }
    }
    requestAnimationFrame(() => closeRef.current?.focus())
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); requestAnimationFrame(() => triggerRef.current?.focus() || previous?.focus()) }
  }, [open])

  const data = trace.data
  const decision = asRecord(data?.final_decision)
  const decisionReason = asString(decision?.reasoning)
  const decisionStatus = asString(data?.final_status ?? decision?.status) ?? 'Not returned'
  const trigger = <button ref={triggerRef} className="trace-open" onMouseEnter={() => id && prefetch(id)} onFocus={() => id && prefetch(id)} onClick={() => setOpen(true)} aria-haspopup="dialog"><FileSearch data-icon="inline-start" /> {id ? 'Inspect trace' : 'Open backend trace'}</button>
  return <><span className="trace-trigger">{trigger}</span><AnimatePresence>{open && <motion.div className="trace-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false) }} initial={reduced ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={reduced ? undefined : { opacity: 0 }}><motion.aside className="trace-drawer liquid-focal" role="dialog" aria-modal="true" aria-labelledby="trace-title" initial={reduced ? false : { x: '100%' }} animate={{ x: 0 }} exit={reduced ? undefined : { x: '100%' }}><button ref={closeRef} className="trace-close" onClick={() => setOpen(false)} aria-label="Close reasoning trace"><X /></button><span className="eyebrow">CASE REVIEW / EVIDENCE</span><h2 id="trace-title">{id || 'Reasoning trace'}</h2><label htmlFor="record-id">Backend record ID</label><input id="record-id" value={localRecordId} onChange={(event) => setLocalRecordId(event.target.value)} placeholder="Paste a record id" autoComplete="off"/><p className="trace-hint">A guided explanation of how this record was evaluated.</p>{trace.isFetching && <p aria-live="polite"><LoaderCircle className="spin" /> Loading evidence…</p>}{trace.error && <div className="run-message" role="alert">{trace.error.message}<button className="trace-open" onClick={() => trace.refetch()}><RefreshCw /> Retry</button></div>}{data && <div className="trace-evidence"><section className="trace-decision-summary"><span className="micro-label">DECISION</span><strong>{decisionStatus === 'exception' ? 'Needs attention' : decisionStatus}</strong>{decisionReason && <p>{decisionReason}</p>}</section><EvidenceBlock title="RECORD IDENTITY" value={{ record_id: data.record_id, provider: data.provider, handled_by_key: data.handled_by_key, final_status: data.final_status }} /><section className="trace-block"><h3>INVESTIGATION TIMELINE</h3><HistoryTimeline history={data.history} /></section><EvidenceBlock title="FINAL DECISION DETAILS" value={data.final_decision} /><EvidenceBlock title="TIMING" value={{ wall_clock_time_sec: data.wall_clock_time_sec, active_processing_time_sec: data.active_processing_time_sec, reactive_throttle_wait_sec: data.reactive_throttle_wait_sec, self_paced_wait_sec: data.self_paced_wait_sec, other_pacing_wait_sec: data.other_pacing_wait_sec }} /></div>}</motion.aside></motion.div>}</AnimatePresence></>
}
