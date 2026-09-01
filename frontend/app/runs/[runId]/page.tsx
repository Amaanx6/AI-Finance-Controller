'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, ArrowUpRight, CircleAlert, FileSearch, RefreshCw, ShieldCheck } from 'lucide-react'
import { useParams, useSearchParams } from 'next/navigation'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useRunExceptions, useRunResults, useRunStatus, normalizeStatus } from '@/lib/queries'
import { asNumber, asRecord, extractRecordIds } from '@/lib/api-types'
import { evidenceRows, exceptionSummary, formatDelta, formatMetric, labelStatus, outcomes, resultSummary } from '@/lib/view-models'
import { TraceDrawer } from '@/components/trace-drawer'

export default function RunPage() {
  const { runId = '' } = useParams<{ runId: string }>()
  const statusQuery = useRunStatus(runId)
  const status = normalizeStatus(statusQuery.data?.status)
  const complete = status === 'completed'
  const failed = status === 'failed'
  return <main className="run-detail">
    <header className="runs-nav"><Link href="/runs" className="back"><ArrowLeft size={15}/> all runs</Link><span className="brand"><span className="brand-mark">A</span>arbiter</span><span className="run-id">RUN {runId || '…'}</span></header>
    <section className="run-content"><div className="eyebrow">{complete ? 'RESULTS / EVIDENCE' : 'LIVE RUN / INVESTIGATION'}</div><h1>{complete ? 'The evidence is in.' : failed ? 'The run needs attention.' : 'Watching certainty take shape.'}</h1><p className="runs-lede">{complete ? 'A readable account of every decision, from fast path to exception.' : 'Live state from the reconciliation engine. No simulated activity.'}</p>{statusQuery.error && <p className="run-message" role="alert">{statusQuery.error.message}</p>}{complete ? <Results runId={runId} /> : <Live query={statusQuery} status={status} failed={failed} runId={runId} />}</section>
  </main>
}

function Live({ query, status, failed, runId }: { query: ReturnType<typeof useRunStatus>; status: string; failed: boolean; runId: string }) {
  const reduced = useReducedMotion()
  const processed = asNumber(query.data?.records_processed) ?? 0
  const total = asNumber(query.data?.total_records) ?? 0
  const fast = asNumber(query.data?.fast_path_resolved_so_far) ?? 0
  const agent = asNumber(query.data?.agent_resolved_so_far) ?? 0
  const progress = total > 0 ? Math.min(100, processed / total * 100) : 0
  const startedAtRef = useRef(Date.now())
  const [elapsed, setElapsed] = useState(0)
  const [activity, setActivity] = useState<string[]>([])
  const previous = useRef({ processed, fast, agent, status })

  useEffect(() => {
    if (completeStatus(status) || failed) return
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [status, failed])

  useEffect(() => {
    const next: string[] = []
    const prev = previous.current
    if (processed > prev.processed) next.push(`${processed - prev.processed} additional record${processed - prev.processed === 1 ? '' : 's'} processed.`)
    if (fast > prev.fast) next.push(`Fast path resolved ${fast - prev.fast} additional record${fast - prev.fast === 1 ? '' : 's'}.`)
    if (agent > prev.agent) next.push(`Agent-resolved count increased to ${agent}.`)
    if (status !== prev.status) next.push(`Run entered ${labelStatus(status)} state.`)
    if (next.length) setActivity(current => [...next, ...current].slice(0, 4))
    previous.current = { processed, fast, agent, status }
  }, [processed, fast, agent, status])

  const derived = failed ? (query.data?.error || 'The backend reported a failed run.') : status === 'pending' ? 'Waiting for processing to begin.' : `${processed} of ${total || '—'} records processed.`
  return <motion.div className="live-panel glass liquid-focal" aria-live="polite" initial={reduced ? false : {opacity:0,y:18}} animate={{opacity:1,y:0}} transition={{duration:.55}}>
    <div className="live-heading"><span className="status-dot">● {failed ? 'attention' : 'live'}</span><span>{labelStatus(status)}</span></div>
    <div className="live-hero"><div><span className="micro-label">RECONCILIATION PROGRESS</span><strong>{Math.round(progress)}%</strong><small>{processed} / {total || '—'} records</small></div><div className="elapsed"><span className="micro-label">OBSERVED TIME</span><b>{formatElapsed(elapsed)}</b></div></div>
    <div className="progress" aria-label={`Run progress ${Math.round(progress)} percent`}><motion.i animate={{width:`${progress}%`}} transition={{duration:reduced?0:.55,ease:'easeOut'}}/></div>
    <div className="live-stats"><Stat label="processed" value={total ? `${processed} / ${total}` : processed}/><Stat label="fast path" value={fast}/><Stat label="agent path" value={agent}/></div>
    <div className="live-investigation glass"><div className="live-investigation-head"><span className="micro-label">CURRENT STATE</span><span className="status-dot">{failed ? 'attention' : 'active'}</span></div><h3>{failed ? 'Backend reported a failure.' : status === 'pending' ? 'Preparing the investigation.' : agent > fast ? 'Agent resolution in progress.' : 'Separating deterministic matches.'}</h3><p>{derived}</p></div>
    <div className="activity-feed"><div className="micro-label">RECENT ACTIVITY</div>{activity.length ? activity.map((item,index) => <motion.div key={`${item}-${index}`} initial={reduced?false:{opacity:0,x:-8}} animate={{opacity:1,x:0}}><span>{index === 0 ? '→' : '✓'}</span>{item}</motion.div>) : <div><span>→</span>{derived}</div>}</div>
    {failed && <p className="run-message" role="alert">{query.data?.error || 'No failure detail returned.'}</p>}
  </motion.div>
}

function completeStatus(value: string) { return value === 'completed' || value === 'failed' }
function formatElapsed(seconds: number) { const m=Math.floor(seconds/60); const s=seconds%60; return `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` }
function Stat({label,value}:{label:string;value:unknown}) { return <div><span>{label}</span><b>{String(value)}</b></div> }

function Results({ runId }: { runId: string }) {
  const results = useRunResults(runId)
  const exceptions = useRunExceptions(runId)
  const search = useSearchParams()
  const [traceOpen, setTraceOpen] = useState(Boolean(search.get('trace')))
  const [selectedTrace, setSelectedTrace] = useState(search.get('trace') || '')
  const result = results.data
  const summary = resultSummary(result)
  const ids = extractRecordIds(exceptions.data?.exceptions)
  const traceRows = ids.slice(0, 8)

  useEffect(() => {
    const requested = search.get('trace') || ''
    if (requested) { setSelectedTrace(requested); setTraceOpen(true) }
  }, [search])

  function openTrace(id: string) {
    setSelectedTrace(id)
    setTraceOpen(true)
    const params = new URLSearchParams(search.toString())
    params.set('trace', id)
    window.history.replaceState(null, '', `?${params.toString()}`)
  }

  function closeTrace(open: boolean) {
    setTraceOpen(open)
    if (!open) {
      const params = new URLSearchParams(search.toString())
      params.delete('trace')
      const query = params.toString()
      window.history.replaceState(null, '', query ? `?${query}` : window.location.pathname)
    }
  }

  return <div className="results">
    <div className="result-banner glass"><div><span className="eyebrow">COMPLETED RUN / BACKEND EVIDENCE</span><h2>Results are ready to inspect.</h2><p>{runId} · {result?.timestamp || result?.run_started_at || 'Timestamp not returned'}</p></div><ShieldCheck color="var(--mint)" size={28}/></div>

    {results.isPending ? <p aria-live="polite">Loading persisted result…</p> : results.error ? <p className="run-message" role="alert">{results.error.message}</p> : <>
      {ids.length > 0 && <section className="result-section highlighted"><span className="eyebrow">HIGHLIGHTED CASES</span><h2>The evidence worth opening.</h2><p>{ids.length} backend-provided exception{ids.length === 1 ? '' : 's'} require inspection.</p><div className="trace-list">{traceRows.map(id => <div className="trace-row" key={id}><CircleAlert className="trace-icon" size={16}/><div><b>{id}</b><small>Open the backend reasoning trace and inspect its evidence.</small></div><button className="trace-open" onClick={() => openTrace(id)}>Inspect trace <ArrowUpRight size={15}/></button></div>)}</div></section>}

      <section className="result-section"><span className="eyebrow">PROOF / KPI</span><div className="kpi-grid"><Kpi label="overall match rate" value={formatMetric(summary.overall)}/><Kpi label="records" value={summary.total ?? 'Not returned'}/><Kpi label="exceptions" value={summary.outcomes.find(x=>x.key.includes('exception'))?.count ?? 'Not returned'}/><Kpi label="agent resolved" value={summary.outcomes.find(x=>x.key.includes('agent'))?.count ?? 'Not returned'}/></div></section>

      <section className="result-grid"><OutcomeSection items={summary.outcomes}/><CompareSection full={result?.full_pipeline_scores} baseline={result?.baseline_scores}/></section>
      <section className="result-grid"><ReadableSection title="PATTERN ANALYSIS" value={result?.breakdown}/><ReadableSection title="PERFORMANCE" value={result?.performance}/></section>

      {exceptions.isPending ? <p aria-live="polite">Loading exceptions…</p> : exceptions.error ? <p className="run-message" role="alert">{exceptions.error.message}</p> : <section className="result-section"><span className="eyebrow">EXCEPTIONS / DEAD LETTER QUEUE</span><div className="exception-list">{exceptionSummary(exceptions.data).slice(0,8).map(item => <details className="evidence-details" key={item.id}><summary><CircleAlert size={15}/>{item.id}</summary><Evidence rows={item.rows}/><button className="trace-open" onClick={()=>openTrace(item.id)}>Inspect trace <FileSearch size={14}/></button></details>)}</div></section>}
      <section className="result-section traces-section"><span className="eyebrow">REASONING TRACES</span><p className="muted">Select an exception to inspect the backend evidence trail.</p>{traceRows.length ? traceRows.map(id=><button key={id} className="trace-index" onClick={()=>openTrace(id)}><span>{id}</span><ArrowUpRight size={14}/></button>) : <p className="muted">No traceable exception records returned.</p>}</section>
    </>}
    <TraceDrawer open={traceOpen} onOpenChange={closeTrace} recordId={selectedTrace || traceRows[0] || ''}/>
  </div>
}

function Kpi({label,value}:{label:string;value:unknown}) { return <div className="kpi"><span>{label}</span><strong>{String(value)}</strong></div> }
function OutcomeSection({items}:{items:{key:string;label:string;count:number}[]}) { return <section className="result-section"><span className="eyebrow">OUTCOME DISTRIBUTION</span><div className="outcome-list">{items.length?items.map(item=><div key={item.key}><span>{item.label}</span><b>{item.count}</b><i style={{width:`${Math.min(100,item.count)}%`}}/></div>):<p className="muted">No breakdown returned.</p>}</div></section> }
function CompareSection({full,baseline}:{full:unknown;baseline:unknown}) {
  const fullOverall = asRecord(asRecord(full)?.overall)
  const baseOverall = asRecord(asRecord(baseline)?.overall)
  const keys = Array.from(new Set([...Object.keys(fullOverall ?? {}), ...Object.keys(baseOverall ?? {})]))
  const numericKeys = keys.filter(key => typeof fullOverall?.[key] === 'number' || typeof baseOverall?.[key] === 'number')
  return <section className="result-section"><span className="eyebrow">BASELINE / FULL PIPELINE</span>{numericKeys.length ? numericKeys.slice(0, 6).map(key => { const fullValue = fullOverall?.[key]; const baseValue = baseOverall?.[key]; const fullNum = typeof fullValue === 'number' ? fullValue : null; const baseNum = typeof baseValue === 'number' ? baseValue : null; return <div className="comparison" key={key}><span>{humanizeKey(key)}</span><b>{fullNum === null ? 'Not returned' : formatMetric(fullNum)}</b><small>baseline {baseNum === null ? 'Not returned' : formatMetric(baseNum)}</small>{fullNum !== null && baseNum !== null && <em className="delta">{formatDelta(fullNum, baseNum)}</em>}</div> }) : <p className="muted">No aggregate comparison returned.</p>}</section>
}
function humanizeKey(value: string) { return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, char => char.toUpperCase()) }
function ReadableSection({title,value}:{title:string;value:unknown}) { return <section className="result-section"><span className="eyebrow">{title}</span><Evidence rows={evidenceRows(value)}/></section> }
function Evidence({rows}:{rows:{label:string;value:string}[]}) { return rows.length?<div className="evidence-list">{rows.map(row=><div key={`${row.label}-${row.value}`}><span>{row.label}</span><b>{row.value}</b></div>)}</div>:<p className="muted">No structured evidence returned.</p> }
