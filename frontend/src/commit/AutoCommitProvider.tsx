import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { changeLogQueryKey } from '../api/changeLog.ts'
import { type GridData, gridQueryKey, type GridRow } from '../api/grid.ts'
import { templatesQueryKey } from '../api/templates.ts'
import {
  AutoCommitContext,
  autoCommitQueryKey,
  type AutoCommitState,
  type AutoCommitStatus,
  commitPending,
  conflictsOf,
  type FailedEntryMarks,
  failedEntryMarks,
  failureOf,
  useAutoCommitStatus,
  isUiEntry,
  takeUnreported,
} from './autoCommit.ts'
import { FAST_POLL_MS, fetchQueue, QUEUE_POLL_MS, type QueueEntry, queueKey, queueQueryKey, useQueue } from '../api/queue.ts'

function gridRows(queryClient: QueryClient): GridRow[] {
  return queryClient.getQueryData<GridData>(gridQueryKey)?.rows ?? []
}

// Polls the pending list and the auto-commit status, every second while ui entries wait or a
// commit runs. A ui entry that left the pending list stays in the grid (settling) until a status
// with a new commitCount and no commit running came and the grid reloaded, then new failures are
// reported. One that left while no commit ran or ended (rejected) goes at the next status.
export default function AutoCommitProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  // The ui entries of the pending lists, until a status drops them (dropDecided).
  const [held, setHeld] = useState<ReadonlyMap<number, QueueEntry>>(() => new Map())
  // The commitCount the grid was last reloaded for, null until the first status.
  const reloadedFor = useRef<number | null>(null)
  // The failed entries the snackbar names and whose cells are tinted.
  const [failed, setFailed] = useState<readonly FailedEntryMarks[]>([])
  const [commitError, setCommitError] = useState<string | null>(null)
  // The failed entries whose conflict marks the grid shows.
  const [conflicted, setConflicted] = useState<readonly FailedEntryMarks[]>([])
  const [committingNow, setCommittingNow] = useState(false)

  const cached = queryClient.getQueryData<AutoCommitStatus>(autoCommitQueryKey)
  const pollMs = held.size > 0 || (cached?.pending ?? 0) > 0 || cached?.committing ? FAST_POLL_MS : QUEUE_POLL_MS
  const status = useAutoCommitStatus(pollMs)
  const pending = useQueue('pending', pollMs)

  const reportFailures = useCallback(
    async (before: readonly GridRow[]) => {
      let failed: QueueEntry[]
      try {
        failed = await queryClient.fetchQuery({ queryKey: queueKey('failed'), queryFn: () => fetchQueue('failed', true) })
      } catch {
        return
      }
      const fresh = takeUnreported(queryClient, failed)
      if (fresh.length === 0) {
        return
      }
      const parts = failedEntryMarks(fresh, before, gridRows(queryClient))
      setCommitError(null)
      setFailed(parts)
      setConflicted(parts)
    },
    [queryClient],
  )

  // Drops the held entries no longer pending, unless a commit runs or the grid lacks one that
  // ended. Run when a status arrives, it decides with the pending list that came before it.
  const dropDecided = useCallback(() => {
    const current = queryClient.getQueryData<AutoCommitStatus>(autoCommitQueryKey)
    if (current && !current.committing && current.commitCount === reloadedFor.current) {
      const ids = new Set(queryClient.getQueryData<QueueEntry[]>(queueKey('pending'))?.map((entry) => entry.id))
      setHeld((old) => ([...old.keys()].every((id) => ids.has(id)) ? old : new Map([...old].filter(([id]) => ids.has(id)))))
    }
  }, [queryClient])

  useEffect(() => {
    const entries = pending.data?.filter(isUiEntry) ?? []
    if (entries.length > 0) {
      setHeld((old) => new Map([...old, ...entries.map((entry) => [entry.id, entry] as const)]))
    }
  }, [pending.data])

  const commitCount = status.data?.commitCount
  const commitRuns = status.data?.committing === true
  useEffect(() => {
    reloadedFor.current ??= commitCount ?? null
    if (commitCount === undefined || commitRuns || commitCount === reloadedFor.current) {
      return
    }
    // Set when the next commit starts or ends meanwhile, its own reload drops what settled then.
    let superseded = false
    const before = gridRows(queryClient)
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: gridQueryKey, refetchType: 'all' }),
      queryClient.invalidateQueries({ queryKey: templatesQueryKey }),
      queryClient.invalidateQueries({ queryKey: changeLogQueryKey }),
    ])
      .then(() => reportFailures(before))
      .then(() => {
        if (!superseded) {
          reloadedFor.current = commitCount
          dropDecided()
        }
      })
    return () => {
      superseded = true
    }
  }, [queryClient, reportFailures, dropDecided, commitCount, commitRuns])

  useEffect(dropDecided, [dropDecided, status.dataUpdatedAt])

  // Failures of a commit that ran while no tab was open (the session end, the next unlock).
  useEffect(() => {
    const mountCheck = setTimeout(() => void reportFailures([]), 0)
    return () => clearTimeout(mountCheck)
  }, [reportFailures])

  const commitNow = useCallback(async () => {
    setCommittingNow(true)
    try {
      await commitPending()
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setFailed([])
      setCommitError(`Could not save: ${message}`)
    } finally {
      setCommittingNow(false)
      await queryClient.invalidateQueries({ queryKey: queueQueryKey })
    }
  }, [queryClient])

  const unsaved = pending.data?.filter(isUiEntry).length ?? 0
  const committing = committingNow || commitRuns
  const settling = useMemo(() => {
    const ids = new Set(pending.data?.map((entry) => entry.id))
    return [...held.values()].filter((entry) => !ids.has(entry.id)).map((entry) => ({ ...entry, status: 'approved' as const }))
  }, [held, pending.data])
  const failure = useMemo(
    () => (commitError !== null ? { message: commitError, lines: [], errorCells: new Map() } : failureOf(failed)),
    [commitError, failed],
  )
  const conflicts = useMemo(() => conflictsOf(conflicted), [conflicted])
  const forgetFailed = useCallback((ids: readonly number[]) => {
    const gone = new Set(ids)
    setFailed((parts) => parts.filter((part) => !gone.has(part.id)))
    setConflicted((parts) => parts.filter((part) => !gone.has(part.id)))
  }, [])
  const value = useMemo<AutoCommitState>(
    () => ({
      status: status.data,
      unsaved,
      committing,
      settling,
      failure,
      conflicts,
      closeFailure: () => {
        setFailed([])
        setCommitError(null)
      },
      clearConflicts: () => setConflicted([]),
      forgetFailed,
      commitNow,
    }),
    [status.data, unsaved, committing, settling, failure, conflicts, forgetFailed, commitNow],
  )
  return <AutoCommitContext value={value}>{children}</AutoCommitContext>
}
