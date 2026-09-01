'use client'

import Link from 'next/link'
import { useState } from 'react'
import { ArrowLeft, ArrowUpRight, CircleAlert, Play, RefreshCw, ShieldCheck } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useLatestResults } from '@/lib/queries'
import { asRecord } from '@/lib/api-types'
import { formatMetric, outcomes } from '@/lib/view-models'

function latestRunId(value: unknown) {
  const record = asRecord(value)
  const id = record?.run_id ?? record?.runId
  return typeof id === 'string' && id.trim() ? id : null
}

export default function RunsPage() {
  const router = useRouter()
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState('Ready for a new investigation.')
  const latest = useLatestResults()
  const result = latest.data
  const record = asRecord(result)
  const runId = latestRunId(result)
  const hasResult = Boolean(record && (record.timestamp || record.run_started_at || record.total_records !== undefined))

  async function start() {
    setRunning(true)
    setMessage('Connecting to the reconciliation engine…')
    try {
      const response = await fetch('/api/run', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() } })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || data.message || 'Unable to start run.')
      if (!data.run_id) throw new Error('The backend returned no run id.')
      router.push(`/runs/${encodeURIComponent(data.run_id)}`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Backend unavailable.')
      setRunning(false)
    }
  }

  const breakdown = outcomes(record?.breakdown)

  return <main className="runs-page">
    <header className="runs-nav">
      <Link href="/" className="brand"><span className="brand-mark">A</span>arbiter</Link>
      <Link href="/" className="back"><ArrowLeft size={15}/> return to story</Link>
    </header>

    <section className="runs-shell">
      <div className="eyebrow">CONTROL ROOM / RUNS</div>
      <h1>See what the books<br/><em>are really saying.</em></h1>
      <p className="runs-lede">Start a reconciliation to watch Arbiter separate clean matches from the records that require judgment.</p>

      <section className="run-panel glass">
        <div className="panel-heading"><div><span className="micro-label">NEW INVESTIGATION</span><h2>Reconcile the latest dataset</h2></div><ShieldCheck color="var(--mint)"/></div>
        <div className="run-meta"><span>source / bank + ledger + gateway</span><span>mode / fast path + agent path</span></div>
        <button className="primary-btn" onClick={start} disabled={running}>
          {running ? <RefreshCw className="spin" size={16}/> : <Play size={16}/>} {running ? 'Starting…' : 'Run reconciliation'} <ArrowUpRight size={16}/>
        </button>
        <p className="run-message" aria-live="polite">{message}</p>
      </section>

      <section className="latest">
        <div className="latest-head"><div><span className="eyebrow">LATEST RECONCILIATION</span><p className="latest-context">From the newest completed persisted backend result.</p></div></div>
        {latest.isPending ? <p className="muted" aria-live="polite">Checking persisted results…</p>
        : latest.error ? <div className="unavailable" role="alert"><CircleAlert size={16}/><span>{latest.error.message}</span></div>
        : !hasResult ? <div className="empty glass"><span>NO RECONCILIATIONS YET</span><small>The backend has not returned a completed persisted result.</small></div>
        : <article className="latest-result glass">
            <div className="latest-primary"><span className="micro-label">OVERALL MATCH RATE</span><strong>{formatMetric(record?.overall_match_rate)}</strong><small>{record?.total_records ?? '—'} records · {record?.timestamp || record?.run_started_at || 'Timestamp not returned'}</small></div>
            <div className="latest-grid">{breakdown.slice(0,4).map(item => <div key={item.key}><span>{item.label}</span><b>{item.count}</b></div>)}</div>
            <div className="latest-actions">{runId ? <Link className="primary-btn" href={`/runs/${encodeURIComponent(runId)}`}>View results <ArrowUpRight size={15}/></Link> : <button className="secondary-btn" disabled title="The latest-results response does not include a run_id.">View results unavailable</button>}</div>
          </article>}
      </section>
    </section>
  </main>
}
