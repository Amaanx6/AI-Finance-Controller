// app/runs/page.tsx
'use client'

import { useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, ArrowUpRight, CircleAlert, Play, RefreshCw, ShieldCheck, ExternalLink } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useLatestResults } from '@/lib/queries'
import { asRecord, asString } from '@/lib/api-types'
import { formatMetric, outcomes } from '@/lib/view-models'

export default function RunsPage() {
  const router = useRouter()
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState('Ready for a new investigation.')
  const latest = useLatestResults()
  const result = latest.data
  const resultRecord = asRecord(result)
  const runId = asString(resultRecord?.run_id)
  const hasResult = Boolean(
    result &&
    (result.timestamp || result.run_started_at || result.total_records !== undefined)
  )

  async function start() {
    setRunning(true)
    setMessage('Connecting to the reconciliation engine…')

    try {
      const response = await fetch('/api/run', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': crypto.randomUUID(),
        },
        cache: 'no-store',
      })

      const data: unknown = await response.json()
      const payload = asRecord(data)

      if (!response.ok) {
        const detail = asString(payload?.detail) ?? asString(payload?.message)
        throw new Error(detail ?? 'Unable to start reconciliation.')
      }

      const createdRunId = asString(payload?.run_id)

      if (!createdRunId) {
        throw new Error('The backend returned no run id.')
      }

      router.push(`/runs/${encodeURIComponent(createdRunId)}`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Backend unavailable.')
      setRunning(false)
    }
  }

  return (
    <main className="runs-page">
      <header className="runs-nav">
        <Link href="/" className="brand">
          <span className="brand-mark">A</span>
          <span>arbiter</span>
        </Link>

        <Link href="/" className="back">
          <ArrowLeft size={15} />
          return to story
        </Link>
      </header>

      <section className="runs-shell">
        <div className="eyebrow">CONTROL ROOM / RUNS</div>

        <h1>
          See what the books
          <br />
          <em>are really saying.</em>
        </h1>

        <p className="runs-lede">
          Start a reconciliation to watch Arbiter separate clean matches from the
          records that require judgment.
        </p>

        <section className="run-panel glass">
          <div className="panel-heading">
            <div>
              <span className="micro-label">NEW INVESTIGATION</span>
              <h2>Reconcile the latest dataset</h2>
            </div>
            <ShieldCheck color="var(--mint)" aria-hidden="true" />
          </div>

          <div className="run-meta">
            <span>source / bank + ledger + gateway</span>
            <span>mode / fast path + agent path</span>
          </div>

          <button
            className="primary-btn"
            onClick={start}
            disabled={running}
            type="button"
            aria-busy={running}
          >
            {running ? (
              <RefreshCw className="spin" size={16} />
            ) : (
              <Play size={16} />
            )}
            {running ? 'Starting…' : 'Run reconciliation'}
            <ArrowUpRight size={16} />
          </button>

          <p className="run-message" aria-live="polite">
            {message}
          </p>
        </section>

        <section className="latest" aria-labelledby="latest-heading">
          <span id="latest-heading" className="eyebrow">
            LATEST RECONCILIATION
          </span>

          {latest.isPending ? (
            <div className="latest-state" aria-live="polite">
              <RefreshCw className="spin" size={16} />
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
                The backend has not returned a completed persisted result.
              </small>
            </div>
          ) : (
            <article className="latest-result glass">
              <div className="latest-result-main">
                <div>
                  <span className="micro-label">COMPLETED</span>
                  <strong className="latest-rate">
                    {formatMetric(result?.overall_match_rate)}
                  </strong>
                  <small>
                    {result?.total_records ?? '—'} records
                    <span aria-hidden="true"> · </span>
                    {result?.timestamp || result?.run_started_at || 'Timestamp not returned'}
                  </small>
                </div>

                <div className="latest-outcomes" aria-label="Outcome breakdown">
                  {outcomes(result?.breakdown).slice(0, 4).map((item) => (
                    <div key={item.key} className="latest-outcome">
                      <b>{item.count}</b>
                      <span>{item.label}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="latest-action">
                {runId ? (
                  <Link
                    className="trace-open"
                    href={`/runs/${encodeURIComponent(runId)}`}
                  >
                    <span>View results</span>
                    <ExternalLink size={15} aria-hidden="true" />
                  </Link>
                ) : (
                  <div className="latest-missing-id" role="status">
                    <span>
                      Latest result is available, but the API did not return its run ID.
                    </span>
                    <small>
                      Add <code>run_id</code> to the latest-results response to enable
                      result navigation.
                    </small>
                  </div>
                )}
              </div>
            </article>
          )}
        </section>
      </section>
    </main>
  )
}
