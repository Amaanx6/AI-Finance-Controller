// app/runs/[runId]/page.tsx
'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, ArrowUpRight, CircleAlert, FileSearch, RefreshCw, ShieldCheck, Sparkles, Zap } from 'lucide-react'
import { useParams, useSearchParams } from 'next/navigation'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useRunExceptions, useRunResults, useRunStatus, normalizeStatus } from '@/lib/queries'
import { asNumber, asString, extractRecordIds } from '@/lib/api-types'
import {
  evidenceRows,
  exceptionSummary,
  formatMetric,
  labelStatus,
  resultSummary,
  scoreRows,
  formatDelta,
} from '@/lib/view-models'
import { TraceDrawer } from '@/components/trace-drawer'

type ActivityItem = {
  id: string
  icon: 'start' | 'progress' | 'fast' | 'agent' | 'complete' | 'error'
  text: string
}

export default function RunPage() {
  const { runId = '' } = useParams<{ runId: string }>()
  const statusQuery = useRunStatus(runId)
  const status = normalizeStatus(statusQuery.data?.status)
  const complete = status === 'completed'
  const failed = status === 'failed'

  return (
    <main className="run-detail">
      <header className="runs-nav">
        <Link href="/runs" className="back">
          <ArrowLeft size={15} />
          all runs
        </Link>

        <Link href="/" className="brand" aria-label="Return to Arbiter landing page">
          <span className="brand-mark">A</span>
          <span>arbiter</span>
        </Link>

        <span className="run-id">RUN {runId || '…'}</span>
      </header>

      <section className="run-content">
        <div className="eyebrow">
          {complete ? 'RESULTS / EVIDENCE' : failed ? 'RUN / ATTENTION' : 'LIVE RUN / INVESTIGATION'}
        </div>

        <h1>
          {complete
            ? 'The evidence is in.'
            : failed
              ? 'The run needs attention.'
              : 'Watching certainty take shape.'}
        </h1>

        <p className="runs-lede">
          {complete
            ? 'A readable account of every decision, from fast path to exception.'
            : failed
              ? 'The reconciliation engine reported a failure. Inspect the status and retry when ready.'
              : 'Live state from the reconciliation engine. Every number below comes from the backend.'}
        </p>

        {statusQuery.error && (
          <p className="run-message" role="alert">
            {statusQuery.error.message}
          </p>
        )}

        {complete ? (
          <Results runId={runId} />
        ) : (
          <Live query={statusQuery} status={status} failed={failed} />
        )}
      </section>
    </main>
  )
}

function Live({
  query,
  status,
  failed,
}: {
  query: ReturnType<typeof useRunStatus>
  status: string
  failed: boolean
}) {
  const reduced = useReducedMotion()
  const firstSeenRef = useRef<number>(Date.now())
  const previous = useRef({
    processed: 0,
    fast: 0,
    agent: 0,
    status: '',
  })
  const [elapsed, setElapsed] = useState(0)

  const processed = asNumber(query.data?.records_processed)
  const total = asNumber(query.data?.total_records)
  const fast = asNumber(query.data?.fast_path_resolved_so_far)
  const agent = asNumber(query.data?.agent_resolved_so_far)

  const progress =
    processed !== null && total !== null && total > 0
      ? Math.min(100, (processed / total) * 100)
      : 0

  useEffect(() => {
    let timer = 0

    const tick = () => {
      setElapsed(Math.max(0, Date.now() - firstSeenRef.current))
      timer = window.setTimeout(tick, 1000)
    }

    tick()
    return () => window.clearTimeout(timer)
  }, [])

  const activity = useMemo<ActivityItem[]>(() => {
    const items: ActivityItem[] = []

    if (query.data && previous.current.status !== query.data.status) {
      items.push({
        id: `status-${query.data.status}`,
        icon: query.data.status.toLowerCase() === 'failed' ? 'error' : 'start',
        text: `Backend state: ${labelStatus(query.data.status)}.`,
      })
    }

    if (processed !== null && processed > previous.current.processed) {
      items.push({
        id: `processed-${processed}`,
        icon: 'progress',
        text: `${processed} of ${total ?? '—'} records processed.`,
      })
    }

    if (fast !== null && fast > previous.current.fast) {
      items.push({
        id: `fast-${fast}`,
        icon: 'fast',
        text: `Fast path resolved ${fast - previous.current.fast} additional record${fast - previous.current.fast === 1 ? '' : 's'}.`,
      })
    }

    if (agent !== null && agent > previous.current.agent) {
      items.push({
        id: `agent-${agent}`,
        icon: 'agent',
        text: `Agent-resolved count increased to ${agent}.`,
      })
    }

    if (failed && query.data?.error) {
      items.push({
        id: 'error',
        icon: 'error',
        text: query.data.error,
      })
    }

    return items
  }, [agent, failed, fast, processed, query.data, total])

  useEffect(() => {
    previous.current = {
      processed: processed ?? previous.current.processed,
      fast: fast ?? previous.current.fast,
      agent: agent ?? previous.current.agent,
      status: query.data?.status ?? previous.current.status,
    }
  }, [agent, fast, processed, query.data?.status])

  const visibleActivity = activity.slice(-4)

  return (
    <motion.div
      className="live-panel glass liquid-focal live-panel-enhanced"
      aria-live="polite"
      initial={reduced ? false : { opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduced ? 0 : 0.55, ease: 'easeOut' }}
    >
      <div className="live-heading">
        <span className={`status-dot ${failed ? 'status-error' : ''}`}>
          <span aria-hidden="true">●</span>
          {failed ? 'attention' : 'live'}
        </span>
        <span>{labelStatus(status)}</span>
      </div>

      <div className="live-progress-copy">
        <div>
          <span className="micro-label">RECORDS PROCESSED</span>
          <strong>
            {processed ?? '—'}
            <span>/</span>
            {total ?? '—'}
          </strong>
        </div>
        <span className="live-percent">{Math.round(progress)}%</span>
      </div>

      <div
        className="progress"
        role="progressbar"
        aria-label="Reconciliation progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress)}
      >
        <motion.i
          animate={{ width: `${progress}%` }}
          transition={{
            duration: reduced ? 0 : 0.7,
            ease: [0.22, 1, 0.36, 1],
          }}
        />
      </div>

      <div className="live-stats">
        <Stat reduced={Boolean(reduced)} icon={<Zap size={15} aria-hidden="true" />} label="fast path" value={fast ?? '—'} />
        <Stat reduced={Boolean(reduced)} icon={<Sparkles size={15} aria-hidden="true" />} label="agent path" value={agent ?? '—'} />
        <Stat reduced={Boolean(reduced)} icon={<RefreshCw size={15} aria-hidden="true" />} label="elapsed" value={formatDuration(elapsed)} />
      </div>

      <div className="live-investigation">
        <div className="live-investigation-head">
          <div>
            <span className="eyebrow">CURRENT ACTIVITY</span>
            <h2>
              {failed
                ? 'The run needs attention.'
                : status === 'completed'
                  ? 'Investigation complete.'
                  : agent && agent > 0
                    ? 'Agent investigation is active.'
                    : fast && fast > 0
                      ? 'Deterministic matching is resolving the clear cases.'
                      : 'The reconciliation engine is processing the dataset.'}
            </h2>
          </div>

          <div className="activity-pulse" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        </div>

        <div className="activity-list">
          <AnimatePresence initial={false}>
            {visibleActivity.length > 0 ? (
              visibleActivity.map((item) => (
                <motion.div
                  key={item.id}
                  className="activity-item"
                  initial={reduced ? false : { opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={reduced ? undefined : { opacity: 0, x: 10 }}
                  transition={{ duration: reduced ? 0 : 0.32 }}
                >
                  <ActivityIcon type={item.icon} />
                  <span>{item.text}</span>
                </motion.div>
              ))
            ) : (
              <motion.div
                className="activity-item muted"
                initial={reduced ? false : { opacity: 0 }}
                animate={{ opacity: 1 }}
              >
                <RefreshCw size={14} className="spin" aria-hidden="true" />
                <span>Waiting for the next backend status update…</span>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {failed && (
        <div className="run-message error-message" role="alert">
          {query.data?.error || 'No failure detail returned.'}
        </div>
      )}
    </motion.div>
  )
}

function Stat({
  icon,
  label,
  value,
  reduced,
}: {
  icon: React.ReactNode
  label: string
  value: string | number
  reduced: boolean
}) {
  return (
    <div className="live-stat">
      <div className="live-stat-icon">{icon}</div>
      <div>
        <span>{label}</span>
        <AnimatePresence mode="wait" initial={false}>
          <motion.b
            key={String(value)}
            initial={reduced ? false : { opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduced ? undefined : { opacity: 0, y: -5 }}
            transition={{ duration: reduced ? 0 : 0.2 }}
          >
            {value}
          </motion.b>
        </AnimatePresence>
      </div>
    </div>
  )
}

function ActivityIcon({ type }: { type: ActivityItem['icon'] }) {
  if (type === 'fast') return <Zap size={14} aria-hidden="true" />
  if (type === 'agent') return <Sparkles size={14} aria-hidden="true" />
  if (type === 'error') return <CircleAlert size={14} aria-hidden="true" />
  if (type === 'complete') return <ShieldCheck size={14} aria-hidden="true" />
  return <RefreshCw size={14} aria-hidden="true" />
}

function formatDuration(ms: number) {
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60

  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

function Results({ runId }: { runId: string }) {
  const results = useRunResults(runId)
  const exceptions = useRunExceptions(runId)
  const search = useSearchParams()
  const initialTrace = search.get('trace')
  const [traceOpen, setTraceOpen] = useState(Boolean(initialTrace))
  const result = results.data
  const summary = resultSummary(result)
  const ids = extractRecordIds(exceptions.data?.exceptions)
  const traceId = initialTrace || ids[0] || ''

  useEffect(() => {
    if (search.get('trace')) {
      setTraceOpen(true)
    }
  }, [search])

  return (
    <div className="results">
      <div className="result-banner glass">
        <div>
          <span className="eyebrow">COMPLETED RUN / BACKEND EVIDENCE</span>
          <h2>Results are ready to inspect.</h2>
          <p>
            {runId} · {result?.timestamp || result?.run_started_at || 'Timestamp not returned'}
          </p>
        </div>
        <ShieldCheck color="var(--mint)" size={28} aria-hidden="true" />
      </div>

      {ids.length > 0 && (
        <section className="result-section highlighted">
          <span className="eyebrow">HIGHLIGHTED CASES</span>
          <h2>The Verifier mattered.</h2>
          <p>
            {ids.length} backend-provided exception{ids.length === 1 ? '' : 's'} need inspection.
          </p>

          <div className="trace-row">
            <CircleAlert size={16} aria-hidden="true" />
            <div>
              <b>{ids[0]}</b>
              <small>Open the trace to inspect the real evidence and final decision.</small>
            </div>
            <TraceButton onClick={() => setTraceOpen(true)} />
          </div>
        </section>
      )}

      {results.isPending ? (
        <p aria-live="polite" className="state-inline">
          <RefreshCw size={15} className="spin" />
          Loading persisted result…
        </p>
      ) : results.error ? (
        <p className="run-message" role="alert">
          {results.error.message}
        </p>
      ) : (
        <>
          <section className="result-section">
            <span className="eyebrow">PROOF / KPI</span>
            <div className="kpi-grid">
              <Kpi label="overall match rate" value={formatMetric(summary.overall)} />
              <Kpi label="records" value={summary.total ?? 'Not returned'} />
              <Kpi
                label="exceptions"
                value={summary.outcomes.find((x) => x.key === 'exception')?.count ?? 'Not returned'}
              />
            </div>
          </section>

          <section className="result-grid">
            <OutcomeSection items={summary.outcomes} />
            <CompareSection
              full={result?.full_pipeline_scores}
              baseline={result?.baseline_scores}
            />
          </section>

          <section className="result-grid">
            <ReadableSection title="PATTERN ANALYSIS" value={result?.breakdown} />
            <ReadableSection title="PERFORMANCE" value={result?.performance} />
          </section>
        </>
      )}

      {exceptions.isPending ? (
        <p aria-live="polite" className="state-inline">
          <RefreshCw size={15} className="spin" />
          Loading exceptions…
        </p>
      ) : exceptions.error ? (
        <p className="run-message" role="alert">
          {exceptions.error.message}
        </p>
      ) : (
        <section className="result-section">
          <span className="eyebrow">EXCEPTIONS / DEAD LETTER QUEUE</span>
          {exceptionSummary(exceptions.data).map((item) => (
            <details className="evidence-details" key={item.id}>
              <summary>
                <CircleAlert size={15} aria-hidden="true" />
                <span>{item.id}</span>
              </summary>
              <Evidence rows={item.rows} />
              <TraceButton onClick={() => setTraceOpen(true)} />
            </details>
          ))}
        </section>
      )}

      <TraceDrawer
        open={traceOpen}
        onOpenChange={setTraceOpen}
        recordId={traceId}
      />
    </div>
  )
}

function Kpi({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="kpi">
      <span>{label}</span>
      <strong>{String(value)}</strong>
    </div>
  )
}

function OutcomeSection({
  items,
}: {
  items: { key: string; label: string; count: number }[]
}) {
  const total = items.reduce((sum, item) => sum + item.count, 0)

  return (
    <section className="result-section">
      <span className="eyebrow">OUTCOME DISTRIBUTION</span>
      <div className="outcome-list">
        {items.length ? (
          items.map((item) => {
            const share = total > 0 ? (item.count / total) * 100 : 0
            return (
              <div key={item.key} className="outcome-item">
                <div>
                  <span>{item.label}</span>
                  <b>{item.count}</b>
                </div>
                <i
                  aria-hidden="true"
                  style={{ width: `${Math.min(100, share)}%` }}
                />
              </div>
            )
          })
        ) : (
          <p className="muted">No breakdown returned.</p>
        )}
      </div>
    </section>
  )
}

function CompareSection({
  full,
  baseline,
}: {
  full: unknown
  baseline: unknown
}) {
  const fullRows = scoreRows(full)
  const baselineRows = scoreRows(baseline)
  const baselineByLabel = new Map(
    baselineRows.map((row) => [row.label.toLowerCase(), row])
  )

  return (
    <section className="result-section">
      <span className="eyebrow">BASELINE / FULL PIPELINE</span>

      {fullRows.length ? (
        <div className="comparison-list">
          {fullRows.map((row) => {
            const baselineRow = baselineByLabel.get(row.label.toLowerCase())
            const difference =
              baselineRow && typeof row.numericValue === 'number'
                ? row.numericValue - baselineRow.numericValue
                : null

            return (
              <div className="comparison" key={row.label}>
                <div>
                  <span>{row.label}</span>
                  <b>{row.value}</b>
                </div>
                <small>
                  baseline {baselineRow?.value ?? 'Not returned'}
                </small>
                {difference !== null && (
                  <em className={difference >= 0 ? 'delta-positive' : 'delta-negative'}>
                    {formatDelta(difference)}
                  </em>
                )}
              </div>
            )
          })}
        </div>
      ) : (
        <p className="muted">No comparable metrics returned.</p>
      )}
    </section>
  )
}

function ReadableSection({ title, value }: { title: string; value: unknown }) {
  return (
    <section className="result-section">
      <span className="eyebrow">{title}</span>
      <Evidence rows={evidenceRows(value)} />
    </section>
  )
}

function Evidence({
  rows,
}: {
  rows: { label: string; value: string }[]
}) {
  return rows.length ? (
    <div className="evidence-list">
      {rows.map((row) => (
        <div key={`${row.label}-${row.value}`}>
          <span>{row.label}</span>
          <b>{row.value}</b>
        </div>
      ))}
    </div>
  ) : (
    <p className="muted">No structured evidence returned.</p>
  )
}

function TraceButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="trace-open" onClick={onClick} type="button">
      <FileSearch size={15} aria-hidden="true" />
      Inspect trace
      <ArrowUpRight size={15} aria-hidden="true" />
    </button>
  )
}
