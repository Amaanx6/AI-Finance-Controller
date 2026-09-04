'use client'

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import Link from 'next/link'
import { useParams, useRouter, useSearchParams } from 'next/navigation'

import {
  AnimatePresence,
  motion,
  useReducedMotion,
} from 'motion/react'

import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  CircleAlert,
  FileSearch,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'

import dynamic from 'next/dynamic'
import { Brand } from '@/components/brand'

import {
  isNotFoundError,
  normalizeStatus,
  useRunExceptions,
  useRunResults,
  useRunStatus,
} from '@/lib/queries'

import {
  asNumber,
  extractRecordIds,
} from '@/lib/api-types'

import {
  exceptionSummary,
  formatMetric,
  labelStatus,
  resultSummary,
} from '@/lib/view-models'

import { TraceDrawer } from '@/components/trace-drawer'

const ResultsCharts = dynamic(
  () => import('@/components/results/BklitCharts'),
  {
    ssr: false,
    loading: () => (
      <div
        className="chart-skeleton"
        aria-label="Loading reconciliation charts"
      />
    ),
  },
)

type RunPhase =
  | 'loading'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'not-found'
  | 'unknown'

type Activity = {
  id: string
  text: string
  kind: 'progress' | 'fast' | 'agent' | 'status'
}

type Snapshot = {
  processed: number
  fast: number
  agent: number
  status: string
}

export default function RunPage() {
  const { runId = '' } = useParams<{ runId: string }>()
  const router = useRouter()

  /*
   * BOTH queries live here.
   *
   * This is the critical architectural fix.
   */
  const statusQuery = useRunStatus(runId)
  const resultQuery = useRunResults(runId)

  const status = normalizeStatus(
    statusQuery.data?.status,
  )

  const statusNotFound = isNotFoundError(
    statusQuery.error,
  )

  const hasPersistedResult = Boolean(
    resultQuery.data,
  )

  /*
   * State machine:
   *
   * completed status
   *       OR
   * 404 status + persisted result
   *       = completed
   */
  const phase: RunPhase = useMemo(() => {
    if (status === 'completed') {
      return 'completed'
    }

    if (status === 'failed') {
      return 'failed'
    }

    if (status === 'cancelled') {
      return 'cancelled'
    }

    if (statusNotFound && hasPersistedResult) {
      return 'completed'
    }

    if (statusNotFound && isNotFoundError(resultQuery.error)) {
      return 'not-found'
    }

    if (status === 'running' || status === 'pending') {
      return 'running'
    }

    if (
      statusQuery.isPending &&
      !statusQuery.error
    ) {
      return 'loading'
    }

    if (
      resultQuery.isPending &&
      !statusQuery.data &&
      !statusQuery.error
    ) {
      return 'loading'
    }

    if (statusQuery.error && !statusNotFound) {
      return 'unknown'
    }

    return 'unknown'
  }, [
    hasPersistedResult,
    resultQuery.error,
    resultQuery.isPending,
    status,
    statusNotFound,
    statusQuery.data,
    statusQuery.error,
    statusQuery.isPending,
  ])

  /* A run page may initially receive 425 while its result is still being
   * written. Once status becomes terminal, request the immutable payload once
   * more instead of leaving the page on the earlier 425 error. */
  useEffect(() => {
    if (status === 'completed' && !resultQuery.data) {
      void resultQuery.refetch()
    }
  }, [resultQuery, status])

  if (!runId) {
    return (
      <RunError message="No run id was supplied." />
    )
  }

  return (
    <main className={`run-detail ${phase === 'completed' ? 'run-results-mode' : 'run-live-mode'}`}>
      <header className="runs-nav">
        <Link
          href="/runs"
          className="back"
        >
          <ArrowLeft size={15} />
          all runs
        </Link>

        <Brand />

        <span className="run-id">
          RUN {runId}
        </span>
      </header>

      <section className="run-content">
        <div className="eyebrow">
          {phase === 'completed'
            ? 'RESULTS / EVIDENCE'
          : phase === 'failed'
              ? 'RUN / FAILED'
              : phase === 'not-found'
                ? 'RUN / NOT FOUND'
              : phase === 'loading'
                ? 'RUN / OPENING'
                : 'LIVE RUN / INVESTIGATION'}
        </div>

        <motion.h1
          key={phase}
          initial={{
            opacity: 0,
            y: 8,
          }}
          animate={{
            opacity: 1,
            y: 0,
          }}
          transition={{
            duration: 0.3,
          }}
        >
          {phase === 'completed'
            ? 'The evidence is in.'
          : phase === 'failed'
              ? 'The run needs attention.'
          : phase === 'cancelled'
              ? 'The run was cancelled.'
              : phase === 'not-found'
                ? 'This investigation could not be found.'
              : phase === 'loading'
                ? 'Opening the investigation.'
                : phase === 'unknown'
                  ? 'The run needs a closer look.'
                  : 'Watching certainty take shape.'}
        </motion.h1>

        <p className="runs-lede">
          {phase === 'completed'
            ? 'A readable account of every decision, from fast path to exception.'
          : phase === 'failed'
              ? 'The reconciliation engine reported a failure for this run.'
              : phase === 'not-found'
                ? 'Neither the active run store nor persisted results contain this run id.'
              : phase === 'loading'
                ? 'Loading the latest state and persisted evidence.'
                : phase === 'unknown'
                  ? 'The current run state could not be determined.'
                  : 'Live state from the reconciliation engine. No simulated activity.'}
        </p>

        {(phase === 'unknown' || phase === 'not-found') && (
          <div
            className="unavailable"
            role="alert"
          >
            <CircleAlert size={16} />
            <span>
              {phase === 'not-found'
                ? 'This run id is not present in the active service or persisted evidence.'
                : statusQuery.error instanceof Error
                ? statusQuery.error.message
                : 'Unable to determine the current run state.'}
            </span>
          </div>
        )}

        {phase === 'completed' ? (
          <Results
            runId={runId}
            resultQuery={resultQuery}
          />
        ) : phase === 'loading' ? (
          <LoadingRun />
        ) : phase === 'not-found' ? null : (
          <Live
            runId={runId}
            query={statusQuery}
            status={status}
            failed={phase === 'failed' || phase === 'cancelled'}
            onCancelled={() => router.replace('/runs')}
          />
        )}
      </section>
    </main>
  )
}

/* -------------------------------------------------------------------------- */
/* LOADING                                                                    */
/* -------------------------------------------------------------------------- */

function LoadingRun() {
  return (
    <motion.div
      className="live-panel glass liquid-focal"
      initial={{
        opacity: 0,
        y: 10,
      }}
      animate={{
        opacity: 1,
        y: 0,
      }}
    >
      <div className="live-topline">
        <span className="status-dot">
          ● loading
        </span>

        <span>OPENING</span>
      </div>

      <div className="loading-run-state">
        <div className="loading-orb" />

        <span className="micro-label">
          INVESTIGATION
        </span>

        <h2>
          Loading live state and persisted evidence.
        </h2>

        <p>
          Checking the run without starting another reconciliation.
        </p>
      </div>
    </motion.div>
  )
}

/* -------------------------------------------------------------------------- */
/* LIVE                                                                      */
/* -------------------------------------------------------------------------- */

function Live({
  runId,
  query,
  status,
  failed,
  onCancelled,
}: {
  runId: string
  query: ReturnType<typeof useRunStatus>
  status: string
  failed: boolean
  onCancelled: () => void
}) {
  const reduced = useReducedMotion()

  const startedAtRef = useRef<number>(
    Date.now(),
  )

  const previousRef = useRef<Snapshot | null>(
    null,
  )

  const [now, setNow] = useState(
    () => Date.now(),
  )

  const [activities, setActivities] =
    useState<Activity[]>([])
  const [cancelling, setCancelling] = useState(false)

  async function cancelRun() {
    if (cancelling || status === 'completed' || status === 'cancelled') {
      return
    }

    setCancelling(true)

    try {
      const response = await fetch(
        `/api/runs/${encodeURIComponent(runId)}/cancel`,
        { method: 'POST' },
      )

      const data = await response.json().catch(() => null)

      if (!response.ok) {
        throw new Error(
          data?.detail ||
          data?.message ||
          'Unable to cancel this run.',
        )
      }

      onCancelled()
    } catch (error) {
      console.error('Cancel run failed:', error)
      setCancelling(false)
    }
  }

  /* The fast matcher can resolve its deterministic population before the
   * browser receives its very first polling response. Begin the visual story
   * at zero, then reveal the already-real snapshot shortly afterwards. This
   * does not delay, alter, or fabricate backend progress. */
  const [hasRevealedFirstSnapshot, setHasRevealedFirstSnapshot] =
    useState(false)

  const processed =
    asNumber(
      query.data?.records_processed,
    ) ?? 0

  const total =
    asNumber(
      query.data?.total_records,
    ) ?? 0

  const fast =
    asNumber(
      query.data?.fast_path_resolved_so_far,
    ) ?? 0

  const agent =
    asNumber(
      query.data?.agent_resolved_so_far,
    ) ?? 0

  const actualProgress =
    total > 0
      ? Math.min(
          100,
          Math.max(
            0,
            (processed / total) * 100,
          ),
        )
      : 0

  const visualProcessed =
    hasRevealedFirstSnapshot
      ? processed
      : 0

  const visualFast =
    hasRevealedFirstSnapshot
      ? fast
      : 0

  const visualAgent =
    hasRevealedFirstSnapshot
      ? agent
      : 0

  const progress =
    hasRevealedFirstSnapshot
      ? actualProgress
      : 0

  useEffect(() => {
    if (!query.data || hasRevealedFirstSnapshot) {
      return
    }

    const timer = window.setTimeout(
      () => setHasRevealedFirstSnapshot(true),
      1800,
    )

    return () => window.clearTimeout(timer)
  }, [hasRevealedFirstSnapshot, query.data])

  const remaining =
    Math.max(
      0,
      total - processed,
    )

  const agentQueue = Math.max(0, total - fast)

  const stages = [
    {
      label: 'Dataset loaded',
      detail: total > 0 ? `${total} bank records ready` : 'Reading source records',
      state: total > 0 ? 'done' : 'active',
    },
    {
      label: 'Fast matching',
      detail: hasRevealedFirstSnapshot
        ? `${fast} confirmed automatically · ${agentQueue} sent to review`
        : 'Separating deterministic matches',
      state: hasRevealedFirstSnapshot ? 'done' : 'active',
    },
    {
      label: 'Evidence review',
      detail: agentQueue > 0
        ? `${agent} of ${agentQueue} ambiguous records resolved`
        : 'No ambiguous records require review',
      state: agentQueue === 0 || agent >= agentQueue ? 'done' : 'active',
    },
    {
      label: 'Results',
      detail: 'Evidence dashboard prepared when review completes',
      state: 'waiting',
    },
  ]

  /*
   * Client-observed elapsed time.
   *
   * This is deliberately NOT presented as backend
   * processing time.
   */
  useEffect(() => {
    let cancelled = false

    const timer = window.setInterval(() => {
      if (!cancelled) {
        setNow(Date.now())
      }
    }, 1000)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const elapsedSeconds = Math.max(
    0,
    Math.floor(
      (now - startedAtRef.current) /
        1000,
    ),
  )

  /*
   * Derive activity from actual polling deltas.
   *
   * First snapshot is intentionally ignored.
   * This prevents:
   * "64 additional records processed"
   * "Agent count increased to 26"
   */
  useEffect(() => {
    if (!query.data) {
      return
    }

    const current: Snapshot = {
      processed,
      fast,
      agent,
      status: query.data.status,
    }

    const previous =
      previousRef.current

    if (!previous) {
      previousRef.current = current
      return
    }

    const changes: Activity[] = []

    const processedDelta =
      processed - previous.processed

    const fastDelta =
      fast - previous.fast

    const agentDelta =
      agent - previous.agent

    if (processedDelta > 0) {
      changes.push({
        id: `processed-${processed}-${now}`,
        text: `${processedDelta} record${
          processedDelta === 1
            ? ''
            : 's'
        } processed.`,
        kind: 'progress',
      })
    }

    if (fastDelta > 0) {
      changes.push({
        id: `fast-${fast}-${now}`,
        text: `${fastDelta} record${
          fastDelta === 1
            ? ''
            : 's'
        } resolved through the fast path.`,
        kind: 'fast',
      })
    }

    if (agentDelta > 0) {
      changes.push({
        id: `agent-${agent}-${now}`,
        text: `${agentDelta} record${
          agentDelta === 1
            ? ''
            : 's'
        } resolved through the agent path.`,
        kind: 'agent',
      })
    }

    if (
      query.data.status !==
      previous.status
    ) {
      changes.push({
        id: `status-${query.data.status}-${now}`,
        text: `Run entered ${labelStatus(
          query.data.status,
        ).toLowerCase()} state.`,
        kind: 'status',
      })
    }

    if (changes.length > 0) {
      setActivities(
        (existing) =>
          [
            ...changes,
            ...existing,
          ].slice(0, 5),
      )
    }

    previousRef.current = current
  }, [
    agent,
    fast,
    now,
    processed,
    query.data,
  ])

  const currentState = useMemo(() => {
    if (failed) {
      return (
        query.data?.error ||
        'The backend reported a failure.'
      )
    }

    if (status === 'unknown') {
      return 'The backend state is currently unavailable.'
    }

    if (status === 'pending') {
      return 'Preparing the reconciliation.'
    }

    if (processed === 0) {
      return 'Preparing the reconciliation.'
    }

    if (
      agent > 0 &&
      remaining > 0
    ) {
      return `Resolving ${remaining} ambiguous record${
        remaining === 1
          ? ''
          : 's'
      }.`
    }

    if (
      fast > 0 &&
      remaining > 0
    ) {
      return 'Separating deterministic matches.'
    }

    if (
      remaining === 0 &&
      status !== 'completed'
    ) {
      return 'Finishing the investigation.'
    }

    return 'Reconciliation in progress.'
  }, [
    agent,
    failed,
    fast,
    processed,
    query.data?.error,
    remaining,
    status,
  ])

  const formatClock = useCallback(
    (seconds: number) => {
      const minutes =
        Math.floor(seconds / 60)

      const remainder =
        seconds % 60

      return `${String(
        minutes,
      ).padStart(2, '0')}:${String(
        remainder,
      ).padStart(2, '0')}`
    },
    [],
  )

  return (
    <div className="live-liquid">
    <motion.div
      className="live-panel glass"
      initial={
        reduced
          ? false
          : {
              opacity: 0,
              y: 12,
            }
      }
      animate={{
        opacity: 1,
        y: 0,
      }}
      transition={{
        duration: reduced ? 0 : 0.35,
      }}
    >
      <div className="live-topline">
        <div className="live-heading">
          <span className="status-dot">
            ● {failed ? 'attention' : 'live'}
          </span>

          <span>
            {failed
              ? 'FAILED'
              : labelStatus(status)}
          </span>
        </div>

        <span className="live-run-id">
          {runId}
        </span>
      </div>

      {!failed && status !== 'completed' && status !== 'cancelled' && (
        <div className="live-actions">
          <span>Need to stop this investigation?</span>
          <button className="cancel-run-btn" type="button" onClick={cancelRun} disabled={cancelling}>
            {cancelling ? 'Cancelling…' : 'Cancel run'}
          </button>
        </div>
      )}

      <div className="live-hero-row">
        <div>
          <span className="micro-label">
            RECONCILIATION PROGRESS
          </span>

          <motion.div
            className="progress-number"
            key={Math.round(progress)}
            initial={
              reduced
                ? false
                : {
                    opacity: 0.35,
                    y: 6,
                  }
            }
            animate={{
              opacity: 1,
              y: 0,
            }}
            transition={{
              duration: reduced ? 0 : 0.2,
            }}
          >
            {Math.round(progress)}%
          </motion.div>

          <span className="progress-caption">
            {total > 0
              ? `${visualProcessed} / ${total} records`
              : 'Preparing dataset'}
          </span>
        </div>

        <div className="observed-time">
          <span className="micro-label">
            OBSERVED TIME
          </span>

          <strong>
            {formatClock(
              elapsedSeconds,
            )}
          </strong>
        </div>
      </div>

      <div
        className="progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress)}
        aria-label={`Run progress ${Math.round(
          progress,
        )} percent`}
      >
        <motion.i
          animate={{
            width: `${progress}%`,
          }}
          transition={
            reduced
              ? {
                  duration: 0,
                }
              : {
                  type: 'spring',
                  stiffness: 120,
                  damping: 22,
                }
          }
        />
      </div>

      <section className="run-stage-map" aria-label="Reconciliation stages">
        <div className="run-stage-intro">
          <span className="micro-label">INVESTIGATION MAP</span>
          <p>Every record follows a visible path from source data to evidence.</p>
        </div>
        <ol>
          {stages.map((stage, index) => (
            <li className={`run-stage ${stage.state}`} key={stage.label}>
              <span className="stage-index">{String(index + 1).padStart(2, '0')}</span>
              <span className="stage-copy"><b>{stage.label}</b><small>{stage.detail}</small></span>
              <span className="stage-state">{stage.state === 'done' ? 'Complete' : stage.state === 'active' ? 'Active' : 'Waiting'}</span>
            </li>
          ))}
        </ol>
      </section>

      <div className="live-stats">
        <Stat
          label="processed"
          value={
            total > 0
              ? `${visualProcessed} / ${total}`
              : visualProcessed
          }
        />

        <Stat
          label="fast path"
          value={visualFast}
        />

        <Stat
          label="agent path"
          value={visualAgent}
        />
      </div>

      <div className="current-state-card">
        <div className="current-state-head">
          <span className="micro-label">
            CURRENT STATE
          </span>

          <span>
            {failed
              ? 'attention'
              : 'active'}
          </span>
        </div>

        <h2>
          {hasRevealedFirstSnapshot
            ? currentState
            : 'Preparing the live investigation.'}
        </h2>

        <p>
          {total > 0
            ? hasRevealedFirstSnapshot
              ? `${processed} of ${total} records processed.`
              : `0 of ${total} records shown while the first live snapshot opens.`
            : 'Waiting for the first progress signal.'}
        </p>
      </div>

      <div className="activity-feed">
        <div className="micro-label">
          RECENT ACTIVITY
        </div>

        {activities.length === 0 ? (
          <div className="activity-empty">
            <span className="status-dot">
              →
            </span>

            <span>
              Waiting for the first state transition.
            </span>
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {activities.map(
              (item) => (
                <motion.div
                  key={item.id}
                  className="activity-item"
                  initial={
                    reduced
                      ? false
                      : {
                          opacity: 0,
                          y: -6,
                        }
                  }
                  animate={{
                    opacity: 1,
                    y: 0,
                  }}
                  exit={{
                    opacity: 0,
                    height: 0,
                  }}
                  transition={{
                    duration: reduced
                      ? 0
                      : 0.2,
                  }}
                >
                  <span
                    className={`activity-icon ${item.kind}`}
                  >
                    {item.kind ===
                    'status' ? (
                      '→'
                    ) : (
                      <Check
                        size={13}
                      />
                    )}
                  </span>

                  <span>
                    {item.text}
                  </span>
                </motion.div>
              ),
            )}
          </AnimatePresence>
        )}
      </div>

      {failed && (
        <p
          className="run-message"
          role="alert"
        >
          {query.data?.error ||
            'No failure detail returned.'}
        </p>
      )}
    </motion.div>
    </div>
  )
}

function Stat({
  label,
  value,
}: {
  label: string
  value: unknown
}) {
  return (
    <div>
      <span>{label}</span>
      <b>{String(value)}</b>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* RESULTS                                                                    */
/* -------------------------------------------------------------------------- */

function Results({
  runId,
  resultQuery,
}: {
  runId: string
  resultQuery: ReturnType<
    typeof useRunResults
  >
}) {
  const searchParams =
    useSearchParams()

  const exceptions =
    useRunExceptions(runId)

  const result =
    resultQuery.data

  const [traceOpen, setTraceOpen] =
    useState(
      Boolean(
        searchParams.get(
          'trace',
        ),
      ),
    )

  const [
    selectedTraceId,
    setSelectedTraceId,
  ] = useState(
    searchParams.get(
      'trace',
    ) || '',
  )

  const summary =
    resultSummary(result)

  const exceptionItems =
    exceptionSummary(
      exceptions.data,
    )

  const recordIds =
    extractRecordIds([
      ...(exceptions.data
        ?.exceptions ?? []),
      ...(exceptions.data
        ?.dead_letter_queue ?? []),
    ])

  const traceId =
    selectedTraceId ||
    recordIds[0] ||
    ''

  useEffect(() => {
    const requested =
      searchParams.get(
        'trace',
      )

    if (!requested) {
      return
    }

    setSelectedTraceId(
      requested,
    )
    setTraceOpen(true)
  }, [searchParams])

  const openTrace = useCallback(
    (recordId: string) => {
      setSelectedTraceId(
        recordId,
      )
      setTraceOpen(true)

      const next =
        new URLSearchParams(
          searchParams.toString(),
        )

      next.set(
        'trace',
        recordId,
      )

      const query =
        next.toString()

      window.history.replaceState(
        window.history.state,
        '',
        `${window.location.pathname}${
          query
            ? `?${query}`
            : ''
        }`,
      )
    },
    [searchParams],
  )

  const closeTrace =
    useCallback(
      (open: boolean) => {
        setTraceOpen(
          open,
        )

        if (open) {
          return
        }

        const next =
          new URLSearchParams(
            searchParams.toString(),
          )

        next.delete(
          'trace',
        )

        const query =
          next.toString()

        window.history.replaceState(
          window.history.state,
          '',
          query
            ? `${window.location.pathname}?${query}`
            : window.location.pathname,
        )
      },
      [searchParams],
    )

  const fastConfirmed =
    result?.breakdown
      ?.fast_path_confirmed ??
    0

  const agentConfirmed =
    result?.breakdown
      ?.agent_confirmed ??
    0

  const exceptionCount =
    result?.breakdown
      ?.exception ??
    0

  return (
    <div className="results">
      <motion.div
        className="result-banner glass"
        initial={{
          opacity: 0,
          y: 10,
        }}
        animate={{
          opacity: 1,
          y: 0,
        }}
      >
        <div>
          <span className="eyebrow">
            COMPLETED RUN / BACKEND EVIDENCE
          </span>

          <h2>
            {exceptionCount > 0
              ? `${exceptionCount} records need attention.`
              : 'Every record resolved cleanly.'}
          </h2>

          <p>
            {fastConfirmed + agentConfirmed} of {summary.total ?? '—'} records
            resolved · {formatMetric(summary.overall)} overall match rate.
          </p>
          <small className="result-technical-meta">{runId} · {result?.timestamp || result?.run_started_at || 'Timestamp unavailable'}</small>
        </div>

        <ShieldCheck
          color="var(--mint)"
          size={28}
        />
      </motion.div>

      <section className="results-journey" aria-label="Completed reconciliation journey">
        <div><span>01</span><b>Loaded</b><small>{summary.total ?? '—'} records</small></div>
        <div><span>02</span><b>Matched</b><small>{fastConfirmed} automatically</small></div>
        <div><span>03</span><b>Reviewed</b><small>{agentConfirmed} by agent path</small></div>
        <div><span>04</span><b>Explained</b><small>{exceptionCount} exceptions surfaced</small></div>
      </section>

      <section className="result-section">
        <span className="eyebrow">
          PROOF / KPI
        </span>

        <div className="kpi-grid">
          <Kpi
            label="overall match rate"
            value={formatMetric(
              summary.overall,
            )}
          />

          <Kpi
            label="records"
            value={
              summary.total ??
              '—'
            }
          />



          <Kpi
            label="fast path confirmed"
            value={fastConfirmed}
          />

          <Kpi
            label="agent confirmed"
            value={agentConfirmed}
          />

          <Kpi
            label="exceptions"
            value={exceptionCount}
          />
        </div>
      </section>

      {resultQuery.isPending ? (
        <div
          className="chart-skeleton"
          aria-label="Loading results"
        />
      ) : resultQuery.error ? (
        <p
          className="run-message"
          role="alert"
        >
          {resultQuery.error.message}
        </p>
      ) : (
        <>
          <ResultsCharts
            result={
              result as Record<
                string,
                unknown
              >
            }
            summary={{
              overall:
                summary.overall ?? null,
              total:
                summary.total ?? null,
              outcomes:
                summary.outcomes,
            }}
          />

          <section className="result-grid">
            <section className="result-section">
              <span className="eyebrow">ATTENTION QUEUE / EXCEPTIONS</span>
              <h2 className="section-title">Cases that need a human-readable explanation.</h2>
              <p className="muted">These records were not silently forced into a match. Open a case to see the proposal, challenge, evidence, and final decision.</p>

              {exceptions.isPending ? (
                <div className="chart-skeleton" />
              ) : exceptions.error ? (
                <p
                  className="run-message"
                  role="alert"
                >
                  {exceptions.error.message}
                </p>
              ) : exceptionItems.length ? (
                <div className="evidence-details exception-queue">
                  {exceptionItems
                    .slice(0, 8)
                    .map((item) => (
                      <div
                        className="exception-card"
                        key={item.id}
                      >
                          <div className="exception-card-head">
                          <span className="exception-id">
                            {item.id}
                          </span>

                            <span className="exception-status">Needs review</span>
                        </div>

                        <p>
                          <b>{item.reason}</b>
                        </p>

                        <button
                          className="trace-open case-trace-action"
                          type="button"
                          onClick={() =>
                            openTrace(
                              item.id,
                            )
                          }
                        >
                          Open evidence
                          <FileSearch
                            size={15}
                          />
                        </button>
                      </div>
                    ))}
                </div>
              ) : (
                <p className="muted">
                  Every record resolved
                  cleanly.
                </p>
              )}
            </section>

            <section className="result-section">
              <span className="eyebrow">HOW TO READ A CASE</span>
              <p className="muted">Each case opens the backend trace as a guided evidence review. Start with the decision, then expand the timeline only when you need the underlying proposal details.</p>
              <div className="case-legend"><span><i className="legend-dot mint" />confirmed evidence</span><span><i className="legend-dot amber" />requires review</span><span><i className="legend-dot rose" />exception outcome</span></div>
            </section>
          </section>
        </>
      )}

      <TraceDrawer
        open={traceOpen}
        onOpenChange={
          closeTrace
        }
        recordId={traceId}
        runId={runId}
        showTrigger={false}
      />
    </div>
  )
}

function Kpi({
  label,
  value,
}: {
  label: string
  value: unknown
}) {
  return (
    <div className="kpi">
      <span>{label}</span>
      <strong>
        {String(value)}
      </strong>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* ERROR                                                                      */
/* -------------------------------------------------------------------------- */

function RunError({
  message,
}: {
  message: string
}) {
  return (
    <main className="run-detail">
      <section className="run-content">
        <p
          className="run-message"
          role="alert"
        >
          {message}
        </p>
      </section>
    </main>
  )
}
