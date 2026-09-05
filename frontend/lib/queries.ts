'use client'

import {
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'

import { api } from '@/lib/api'
import {
  isTerminalStatus,
  normalizeStatus,
  type RunResultsResponse,
  type RunStatusResponse,
} from '@/lib/api-types'

export const queryKeys = {
  status: (runId: string) => ['status', runId] as const,
  results: (runId: string) => ['results', runId] as const,
  exceptions: (runId: string) => ['exceptions', runId] as const,
  trace: (recordId: string, runId = '') => ['trace', runId, recordId] as const,
  latestResults: () => ['latest-results'] as const,
  allResults: () => ['all-results'] as const,
}

type ErrorWithStatus = {
  status?: number
  response?: {
    status?: number
  }
}

function getErrorStatus(error: unknown): number | undefined {
  if (!error || typeof error !== 'object') {
    return undefined
  }

  const candidate = error as ErrorWithStatus

  if (typeof candidate.status === 'number') {
    return candidate.status
  }

  if (
    candidate.response &&
    typeof candidate.response.status === 'number'
  ) {
    return candidate.response.status
  }

  return undefined
}

export function isNotFoundError(error: unknown): boolean {
  return getErrorStatus(error) === 404
}

export function isTransientResultError(error: unknown): boolean {
  const status = getErrorStatus(error)

  return status === 425 || status === 404
}

export function useRunStatus(runId: string) {
  return useQuery<RunStatusResponse>({
    queryKey: queryKeys.status(runId),

    queryFn: () => api.status(runId),

    enabled: Boolean(runId),

    staleTime: 0,

    refetchOnWindowFocus: true,

    retry: (failureCount, error) => {
      /*
       * A 404 means this run is not present in the transient
       * in-memory RunStore. It MUST NOT be retried forever.
       *
       * The page will independently check persisted results.
       */
      if (isNotFoundError(error)) {
        return false
      }

      return failureCount < 2
    },

    refetchInterval: (query) => {
      const data = query.state.data
      const error = query.state.error

      /*
       * Terminal backend state.
       */
      if (data && isTerminalStatus(data.status)) {
        return false
      }

      /*
       * Persisted run not present in transient memory.
       * Stop polling. The results query becomes authoritative.
       */
      if (error && isNotFoundError(error)) {
        return false
      }

      /*
       * Any other known/non-terminal status keeps polling.
       */
      return 2000
    },
  })
}

export function useLatestResults() {
  return useQuery({
    queryKey: queryKeys.latestResults(),

    queryFn: api.latestResults,

    /*
     * Latest persisted result may change after a new reconciliation.
     */
    staleTime: 15_000,

    refetchOnWindowFocus: true,

    retry: 1,
  })
}

export function useAllResults() {
  return useQuery({ queryKey: queryKeys.allResults(), queryFn: async () => (await api.sessionRuns()).runs, staleTime: 15_000, refetchOnWindowFocus: true, retry: 1 })
}

export function useRunResults(
  runId: string,
  enabled = true,
): UseQueryResult<RunResultsResponse> {
  return useQuery<RunResultsResponse>({
    queryKey: queryKeys.results(runId),

    queryFn: () => api.results(runId),

    /*
     * IMPORTANT:
     * This query must be allowed to run even when the status query
     * returns 404. Old completed runs can exist in results/*.json
     * while no longer existing in the in-memory RunStore.
     */
    enabled: Boolean(runId) && enabled,

    /*
     * Completed result files are immutable for a given run.
     */
    staleTime: Infinity,
    gcTime: Infinity,

    refetchOnWindowFocus: false,

    retry: (failureCount, error) => {
      /*
       * 404 = no persisted result exists.
       * 425 = run is still processing.
       * Neither should be repeatedly hammered.
       */
      if (isTransientResultError(error)) {
        return false
      }

      return failureCount < 1
    },
  })
}

export function useRunExceptions(
  runId: string,
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.exceptions(runId),

    queryFn: () => api.exceptions(runId),

    enabled: Boolean(runId) && enabled,

    staleTime: Infinity,
    gcTime: Infinity,

    refetchOnWindowFocus: false,

    retry: 1,
  })
}

export function useReasoningTrace(
  recordId: string,
  runId = '',
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.trace(recordId, runId),

    queryFn: () => api.trace(recordId, runId),

    enabled: Boolean(recordId) && enabled,

    staleTime: Infinity,
    gcTime: Infinity,

    refetchOnWindowFocus: false,

    retry: 1,
  })
}

export function usePrefetchTrace() {
  const client = useQueryClient()

  return (recordId: string, runId = '') =>
    client.prefetchQuery({
      queryKey: queryKeys.trace(recordId, runId),
      queryFn: () => api.trace(recordId, runId),
      staleTime: Infinity,
    })
}

export {
  isTerminalStatus,
  normalizeStatus,
}
