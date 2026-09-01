'use client'

import dynamic from 'next/dynamic'
import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, ArrowUpRight, CircleAlert, Clock3, Play, RefreshCw, ShieldCheck } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useAllResults, useLatestResults, useRunResults } from '@/lib/queries'
import { formatMetric, outcomes } from '@/lib/view-models'
import type { RunResultsSummary } from '@/lib/api-types'
import { Brand } from '@/components/brand'

const ResultsCharts = dynamic(() => import('@/components/results/BklitCharts'), { ssr: false, loading: () => <div className="chart-skeleton" aria-label="Loading dashboard charts" /> })

function formatDate(value: string | null | undefined) {
  if (!value) return 'Date unavailable'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString([], { month: 'short', day: 'numeric', year: 'numeric' })
}

function summaryLabel(run: RunResultsSummary) {
  const confirmed = (run.breakdown?.fast_path_confirmed ?? 0) + (run.breakdown?.agent_confirmed ?? 0)
  return `${confirmed} resolved · ${run.breakdown?.exception ?? 0} need attention`
}

function metricBlock(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function formatMoney(value: unknown, currency: unknown = 'INR') {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: String(currency || 'INR'), maximumFractionDigits: 0 }).format(value)
}

export default function RunsPage() {
  const router = useRouter()
  const latest = useLatestResults()
  const history = useAllResults()
  const [selectedId, setSelectedId] = useState('')
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState('Ready to investigate a new dataset.')
  const [caseFilter, setCaseFilter] = useState<'all' | 'review'>('review')
  const [caseSearch, setCaseSearch] = useState('')
  const selected = useRunResults(selectedId, Boolean(selectedId))

  useEffect(() => {
    if (!selectedId) setSelectedId(history.data?.find((run) => run.run_id)?.run_id ?? latest.data?.run_id ?? '')
  }, [history.data, latest.data?.run_id, selectedId])

  async function start() {
    setRunning(true); setMessage('Starting a new investigation…')
    try {
      const response = await fetch('/api/run', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() } })
      const data = await response.json()
      if (!response.ok || !data.run_id) throw new Error(data.detail || 'Unable to start the reconciliation.')
      router.push(`/runs/${encodeURIComponent(data.run_id)}`)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Backend unavailable.'); setRunning(false) }
  }

  const result = selected.data ?? latest.data
  const runList = history.data ?? []
  const breakdown = result?.breakdown
  const resolved = (breakdown?.fast_path_confirmed ?? 0) + (breakdown?.agent_confirmed ?? 0)
  const exceptionCount = breakdown?.exception ?? 0
  const outcomeItems = useMemo(() => outcomes(breakdown).filter((item) => item.count > 0), [breakdown])
  const detailRows = useMemo(() => {
    const scores = result?.full_pipeline_scores
    const rows = scores && typeof scores === 'object' && 'detail_rows' in scores ? scores.detail_rows : []
    if (!Array.isArray(rows)) return []
    return rows.filter((row): row is Record<string, unknown> => Boolean(row && typeof row === 'object'))
  }, [result])
  const reviewRows = useMemo(() => detailRows.filter((row) => {
    const id = String(row.bank_record_id ?? '').toLowerCase()
    const pattern = String(row.pattern ?? '').toLowerCase()
    const status = String(row.predicted_status ?? '').toLowerCase()
    const correct = row.correct === true
    const matchesSearch = !caseSearch || `${id} ${pattern} ${status}`.includes(caseSearch.toLowerCase())
    const matchesFilter = caseFilter === 'all' || !correct || status === 'exception'
    return matchesSearch && matchesFilter
  }).slice(0, 12), [caseFilter, caseSearch, detailRows])

  return <main className="runs-page dashboard-page">
    <header className="runs-nav"><Brand /><span className="dashboard-nav-title">Reconciliation workspace</span><Link href="/" className="back"><ArrowLeft size={15} /> return to story</Link></header>
    <section className="dashboard-shell">
      <aside className="dashboard-sidebar glass">
        <div className="sidebar-heading"><div><span className="eyebrow">WORKSPACE</span><h2>Runs</h2></div><button className="icon-btn" type="button" aria-label="Refresh runs" onClick={() => void history.refetch()}><RefreshCw size={15} /></button></div>
        <button className="primary-btn dashboard-new-run" onClick={start} disabled={running} type="button">{running ? <RefreshCw className="spin" size={16} /> : <Play size={16} />}{running ? 'Starting…' : 'New reconciliation'}<ArrowUpRight size={15} /></button>
        <div className="sidebar-section-label">RECENT RUNS</div>
        {history.isPending ? <p className="muted">Loading persisted runs…</p> : history.error ? <p className="run-message" role="alert">{history.error.message}</p> : runList.length ? <div className="run-history-list">{runList.map((run) => { const key = run.run_id ?? (run.timestamp ? `legacy:${run.timestamp}` : ''); return <button type="button" className={`run-history-item ${selectedId === key ? 'selected' : ''}`} key={key} disabled={!key} onClick={() => key && setSelectedId(key)}><span className="run-history-status"><i /> completed</span><b>{formatDate(run.timestamp || run.run_started_at)}</b><small>{run.run_id || 'Persisted result · legacy identity'}</small><span>{summaryLabel(run)}</span></button> })}</div> : <div className="empty"><span>NO RUNS YET</span><small>Start a reconciliation to create the first durable result.</small></div>}
        <div className="sidebar-footer"><span><ShieldCheck size={14} /> Persisted evidence</span><span><Clock3 size={14} /> Read-only history</span></div>
      </aside>

      <div className="dashboard-main">
        <div className="dashboard-heading"><div><span className="eyebrow">CONTROL ROOM / OVERVIEW</span><h1>Understand every run<br /><em>at a glance.</em></h1><p>Review throughput, outcomes, and the cases that need attention—without losing the evidence behind each decision.</p></div><div className="dashboard-date"><span>SELECTED RUN</span><b>{result?.run_id || 'No run selected'}</b></div></div>
        {result ? <>
          <section className="dashboard-hero glass"><div><span className="micro-label">SELECTED INVESTIGATION</span><h2>{exceptionCount ? `${exceptionCount} records need attention.` : 'Every record resolved cleanly.'}</h2><p>{resolved} of {result.total_records ?? '—'} records resolved · {formatMetric(result.overall_match_rate)} overall match rate.</p><small>{formatDate(result.timestamp || result.run_started_at)} · {result.provider_mode || 'Provider unavailable'}</small></div>{result.run_id ? <Link className="trace-open" href={`/runs/${encodeURIComponent(result.run_id)}`}>Open full run <ArrowUpRight size={15} /></Link> : <span className="latest-note">Legacy persisted result · detail shown here</span>}</section>
          <section className="dashboard-kpis"><DashboardKpi label="Match rate" value={formatMetric(result.overall_match_rate)} detail="Full pipeline outcome" tone="mint" /><DashboardKpi label="Resolved" value={resolved} detail={`${result.total_records ?? '—'} total records`} tone="mint" /><DashboardKpi label="Automatic" value={breakdown?.fast_path_confirmed ?? 0} detail="Fast-path matches" tone="blue" /><DashboardKpi label="Attention" value={exceptionCount} detail="Exceptions surfaced" tone="amber" /></section>
          <section className="dashboard-baseline glass"><div><span className="eyebrow">DECISION QUALITY</span><h2>Arbiter knows when to stop.</h2><p>Compare the full pipeline with the rules-only baseline before trusting an outcome.</p></div><div className="baseline-stats"><BaselineStat label="Accuracy" baseline={metricBlock(metricBlock(result.baseline_scores).overall).accuracy} arbiter={metricBlock(metricBlock(result.full_pipeline_scores).overall).accuracy} /><BaselineStat label="Precision" baseline={metricBlock(metricBlock(result.baseline_scores).overall).precision} arbiter={metricBlock(metricBlock(result.full_pipeline_scores).overall).precision} /><BaselineStat label="Recall" baseline={metricBlock(metricBlock(result.baseline_scores).overall).recall} arbiter={metricBlock(metricBlock(result.full_pipeline_scores).overall).recall} /></div></section>
          <section className="repro-card glass"><div><span className="eyebrow">REPRODUCIBILITY</span><h2>Every result can be explained and replayed.</h2></div><div className="repro-meta"><span>Dataset <b>{String(metricBlock(result.reproducibility).dataset_version ?? 'Not recorded')}</b></span><span>Provider <b>{String(metricBlock(result.reproducibility).provider ?? result.provider_mode ?? 'Not recorded')}</b></span><span>Model <b>{String(metricBlock(result.reproducibility).model ?? 'Provider default')}</b></span><span>Prompts <b>{String(metricBlock(result.reproducibility).prompt_version ?? 'Not recorded')}</b></span></div></section>
          <section className="financial-impact"><div><span className="eyebrow">FINANCIAL IMPACT</span><h2>Follow the money, not just the records.</h2><p>Value is separated by how safely each decision was reached.</p></div><div className="financial-grid"><FinancialStat label="Total value" value={formatMoney(metricBlock(result.financial_impact).total_value, metricBlock(result.financial_impact).currency)} /><FinancialStat label="Auto-reconciled" value={formatMoney(metricBlock(result.financial_impact).automatically_reconciled_value, metricBlock(result.financial_impact).currency)} /><FinancialStat label="Needs review" value={formatMoney(metricBlock(result.financial_impact).value_requiring_review, metricBlock(result.financial_impact).currency)} tone="attention" /></div></section>
          <div className="dashboard-outcomes"><div><span className="eyebrow">OUTCOME SNAPSHOT</span><h2>How the selected run performed</h2></div><div className="outcome-pills">{outcomeItems.map((item) => <span key={item.key}><i className={`outcome-dot ${item.key}`} /><b>{item.count}</b> {item.label}</span>)}</div></div>
          <section className="review-queue" aria-labelledby="review-queue-title"><div className="review-queue-heading"><div><span className="eyebrow">ATTENTION QUEUE</span><h2 id="review-queue-title">Cases that need a human decision.</h2><p>Start with the records Arbiter could not safely auto-confirm.</p></div><div className="review-controls"><div className="segmented-control" role="group" aria-label="Case filter"><button className={caseFilter === 'review' ? 'active' : ''} onClick={() => setCaseFilter('review')} type="button">Needs review</button><button className={caseFilter === 'all' ? 'active' : ''} onClick={() => setCaseFilter('all')} type="button">All cases</button></div><input value={caseSearch} onChange={(event) => setCaseSearch(event.target.value)} placeholder="Search record or pattern" aria-label="Search cases" /></div></div>{reviewRows.length ? <div className="review-list">{reviewRows.map((row) => <article className="review-row" key={String(row.bank_record_id)}><div className="review-row-id"><span className={`review-status ${String(row.predicted_status) === 'exception' ? 'exception' : row.correct === false ? 'warning' : 'resolved'}`} /> <b>{String(row.bank_record_id ?? 'Unknown record')}</b><small>{String(row.pattern ?? 'Unclassified').replaceAll('_', ' ')}</small></div><div className="review-row-summary"><span>{row.correct === false ? 'Needs verification' : String(row.predicted_status ?? 'resolved')}</span><small>{row.predicted_ledger_ids ? `Ledger: ${String(row.predicted_ledger_ids)}` : 'No ledger evidence returned'}</small></div><button className="secondary-btn" type="button" disabled={!result.run_id} title={result.run_id ? 'Open evidence trace' : 'Legacy result has no live trace route'} onClick={() => result.run_id && router.push(`/runs/${encodeURIComponent(result.run_id)}?record=${encodeURIComponent(String(row.bank_record_id ?? ''))}`)}>{result.run_id ? 'Inspect case' : 'Historical only'} {result.run_id && <ArrowUpRight size={14} />}</button></article>)}</div> : <div className="review-empty">No cases match this view.</div>}</section>
          <ResultsCharts result={result as Record<string, unknown>} summary={{ overall: result.overall_match_rate ?? null, total: result.total_records ?? null, outcomes: outcomeItems }} />
        </> : <div className="dashboard-empty glass"><CircleAlert size={22} /><h2>No persisted reconciliation selected</h2><p>Start a new run or choose one from the history panel.</p><button className="primary-btn" onClick={start} disabled={running} type="button"><Play size={15} /> Run reconciliation</button></div>}
      </div>
    </section>
  </main>
}

function DashboardKpi({ label, value, detail, tone }: { label: string; value: unknown; detail: string; tone: string }) { return <article className={`dashboard-kpi ${tone}`}><span>{label}</span><strong>{String(value)}</strong><small>{detail}</small></article> }

function BaselineStat({ label, baseline, arbiter }: { label: string; baseline: unknown; arbiter: unknown }) {
  const asPercent = (value: unknown) => typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '—'
  return <div><span>{label}</span><strong>{asPercent(arbiter)}</strong><small>baseline {asPercent(baseline)}</small></div>
}

function FinancialStat({ label, value, tone = '' }: { label: string; value: string; tone?: string }) { return <div className={`financial-stat ${tone}`}><span>{label}</span><strong>{value}</strong></div> }
