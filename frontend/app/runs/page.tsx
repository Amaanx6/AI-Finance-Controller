'use client'

import Link from 'next/link'
import { useState } from 'react'
import {
  ArrowLeft,
  ArrowUpRight,
  CircleAlert,
  Play,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useLatestResults } from '@/lib/queries'
import { formatMetric, outcomes } from '@/lib/view-models'

export default function RunsPage() {
  const router = useRouter()
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState('Ready for a new investigation.')
  const latest = useLatestResults()

  async function start() {
    setRunning(true)
    setMessage('Connecting to the reconciliation engine…')

    try {
      const response = await fetch('/api/run', {
        method: 'POST',
        headers: {
          'Idempotency-Key': crypto.randomUUID(),
        },
      })

      const data = await response.json()

      if (!response.ok) {
        throw new Error(
          data.detail ||
            data.message ||
            'Unable to start the reconciliation.',
        )
      }

      if (!data.run_id) {
        throw new Error('The backend returned no run id.')
      }

      router.push(`/runs/${encodeURIComponent(data.run_id)}`)
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : 'Backend unavailable.',
      )
      setRunning(false)
    }
  }

  const result = latest.data
  const runId = result?.run_id ?? null
  const hasResult = Boolean(
    result &&
      (result.timestamp ||
        result.run_started_at ||
        result.total_records !== undefined),
  )

  const outcomeItems = outcomes(result?.breakdown).slice(0, 4)

  return (
    <main className="runs-page">
      <header className="runs-nav">
        <Link href="/" className="brand">
          <span className="brand-mark">A</span>
          arbiter
        </Link>

        <Link href="/" className="back">
          <ArrowLeft size={15} />
          return to story
        </Link>
      </header>

      <section className="runs-shell">
        <div className="eyebrow">CONTROL ROOM / RUNS</div>

        <h1>
          Your reconciliation
          <br />
          <em>workspace.</em>
        </h1>

        <p className="runs-lede">
          Start a run, follow every stage of the investigation, and return to
          the evidence whenever a decision needs explaining.
        </p>

        <div className="run-panel glass">
          <div className="panel-heading">
            <div>
              <span className="micro-label">NEW INVESTIGATION</span>
              <h2>Reconcile the latest dataset</h2>
            </div>
            <ShieldCheck color="var(--mint)" />
          </div>

          <div className="run-meta">
            <span>source / bank + ledger + gateway</span>
            <span>mode / fast path + agent path</span>
          </div>

          <div className="run-journey" aria-label="Reconciliation journey preview">
            <div><span>01</span><b>Load</b><small>source records</small></div>
            <div><span>02</span><b>Match</b><small>deterministic rules</small></div>
            <div><span>03</span><b>Review</b><small>ambiguous evidence</small></div>
            <div><span>04</span><b>Explain</b><small>results and traces</small></div>
          </div>

          <button
            className="primary-btn"
            onClick={start}
            disabled={running}
            type="button"
          >
            {running ? (
              <RefreshCw className="spin" size={16} />
            ) : (
              <Play size={16} />
            )}
            {running ? 'Starting…' : 'Run reconciliation'}
            <ArrowUpRight size={16} />
          </button>

          <p className="run-message">{message}</p>
        </div>

        <section className="latest">
          <span className="eyebrow">LATEST RECONCILIATION</span>

          {latest.isPending ? (
            <div className="latest-state">
              <span className="latest-state-pulse" />
              Checking persisted results…
            </div>
          ) : latest.error ? (
            <div className="unavailable" role="alert">
              <CircleAlert size={16} />
              <span>{latest.error.message}</span>
            </div>
          ) : !hasResult ? (
            <div className="empty">
              <span>NO RECONCILIATIONS YET</span>
              <small>
                Run the first reconciliation to create a persisted result.
              </small>
            </div>
          ) : (
            <div className="latest-result glass">
              <div className="latest-main">
                <span className="micro-label">COMPLETED</span>
                <strong>{formatMetric(result?.overall_match_rate)}</strong>
                <small>
                  {result?.total_records ?? '—'} records
                  <span aria-hidden="true"> · </span>
                  {result?.timestamp || 'Timestamp unavailable'}
                </small>
              </div>

              <div className="latest-metrics">
                <div>
                  <span>Fast path</span>
                  <b>
                    {result?.breakdown?.fast_path_confirmed ?? 0}
                  </b>
                </div>

                <div>
                  <span>Agent resolved</span>
                  <b>{result?.breakdown?.agent_confirmed ?? 0}</b>
                </div>

                <div>
                  <span>Exceptions</span>
                  <b>{result?.breakdown?.exception ?? 0}</b>
                </div>
              </div>

              <div className="latest-outcomes" aria-label="Latest run outcomes">
                {outcomeItems.map((item) => (
                  <span key={item.key}>
                    <b>{item.count}</b>
                    {item.label}
                  </span>
                ))}
              </div>

              <div className="latest-actions">
                {runId ? (
                  <Link
                    className="trace-open"
                    href={`/runs/${encodeURIComponent(runId)}`}
                  >
                    View results
                    <ArrowUpRight size={15} />
                  </Link>
                ) : (
                  <span className="latest-note">
                    Results are persisted, but this legacy file has no run id.
                  </span>
                )}

                <button
                  className="secondary-btn"
                  type="button"
                  onClick={start}
                  disabled={running}
                >
                  Run new reconciliation
                  <ArrowUpRight size={15} />
                </button>
              </div>
            </div>
          )}
        </section>
      </section>
    </main>
  )
}
