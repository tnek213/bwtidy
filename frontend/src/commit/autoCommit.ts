import { type QueryClient, useQuery } from '@tanstack/react-query'
import { createContext, useContext } from 'react'
import { apiFetch } from '../api/api.ts'
import { conflictMarks, type ConflictMarks, joinConflictMarks, noConflictMarks } from '../shared/conflicts.ts'
import type { GridRow } from '../api/grid.ts'
import { accountStorageKey } from '../shared/account.ts'
import { type ApproveOutcome, type QueueEntry, queueQueryKey } from '../api/queue.ts'
import { failureReason, UI_SOURCE } from '../shared/queueText.ts'

// Every UI edit goes into the queue as a "ui" entry, and the backend commits
// the pending ones on its own (docs/spec.md, Auto-commit). This module follows
// that: the status, Commit now, the reload after a commit and its failures.

export interface AutoCommitSettings {
  afterSeconds: number
  maxEntries: number
}

// What the write path of a running commit or approval does.
type CommitPhase = 'syncing' | 'backingUp' | 'writing' | 'reloading'

export interface AutoCommitStatus extends AutoCommitSettings {
  // Pending ui entries.
  pending: number
  committing: boolean
  nextCommitAt: string | null
  // The commits that ended since the worker started, the provider reloads after each.
  commitCount: number
  // Null while no write path runs.
  phase: CommitPhase | null
  // While writing: the changes written so far and all of the commit.
  done?: number
  total?: number
}

export const AFTER_SECONDS_RANGE = [1, 3600] as const
export const MAX_ENTRIES_RANGE = [1, 1000] as const

export const autoCommitQueryKey = [...queueQueryKey, 'auto-commit'] as const

function fetchAutoCommit(): Promise<AutoCommitStatus> {
  return apiFetch<AutoCommitStatus>('/api/queue/auto-commit', { background: true })
}

// The provider polls the status, every other reader shares its cache.
export function useAutoCommitStatus(refetchInterval: number | false = false) {
  return useQuery({ queryKey: autoCommitQueryKey, queryFn: fetchAutoCommit, refetchInterval })
}

export function saveAutoCommit(settings: AutoCommitSettings): Promise<unknown> {
  return apiFetch<unknown>('/api/queue/auto-commit', { method: 'PUT', body: settings })
}

export function commitPending(): Promise<ApproveOutcome> {
  return apiFetch<ApproveOutcome>('/api/queue/commit', { method: 'POST' })
}

export function isUiEntry(entry: QueueEntry): boolean {
  return entry.source === UI_SOURCE
}

// When the pending ui entries will be written, for a tooltip.
export function commitText(status: AutoCommitStatus | undefined, now: number): string {
  const at = status?.nextCommitAt ? Date.parse(status.nextCommitAt) : Number.NaN
  if (Number.isNaN(at)) {
    return status?.committing ? 'Saved after the commit that runs now.' : 'Saved with the next commit.'
  }
  const seconds = Math.ceil((at - now) / 1000)
  return seconds > 0 ? `Saved automatically in ${seconds} s.` : 'Saved in a moment.'
}

// What a running commit does, for the app bar's spinner.
export function phaseText(status: AutoCommitStatus | undefined): string {
  switch (status?.phase) {
    case 'syncing':
      return 'Syncing with the server'
    case 'backingUp':
      return 'Taking a backup'
    case 'writing': {
      const { done, total } = status
      if (done === undefined || total === undefined) {
        return 'Writing the changes'
      }
      return total === 1 ? 'Writing the change' : `Writing ${Math.min(done + 1, total)} of ${total} changes`
    }
    case 'reloading':
      return 'Reloading the vault'
    default:
      return 'Saving changes'
  }
}

interface CommitFailure {
  message: string
  // One line per failed entry: its items and the reason.
  lines: string[]
  // Cells of failed entries that were no revisionDate conflict, tinted until the message is closed.
  errorCells: ReadonlyMap<string, ReadonlySet<string>>
}

export interface AutoCommitState {
  status: AutoCommitStatus | undefined
  // Pending ui entries, the unsaved edits.
  unsaved: number
  committing: boolean
  // Entries a commit took from the pending list, still shown in the grid until it reloaded.
  settling: QueueEntry[]
  failure: CommitFailure | null
  // The conflict marks of entries that failed at commit, like the ones of a refused edit.
  conflicts: ConflictMarks
  closeFailure: () => void
  clearConflicts: () => void
  // Drops what these failed entries added to the snackbar and the grid (after a dismiss).
  forgetFailed: (ids: readonly number[]) => void
  commitNow: () => Promise<void>
}

const noop = () => {}

const idle: AutoCommitState = {
  status: undefined,
  unsaved: 0,
  committing: false,
  settling: [],
  failure: null,
  conflicts: noConflictMarks,
  closeFailure: noop,
  clearConflicts: noop,
  forgetFailed: noop,
  commitNow: async () => {},
}

export const AutoCommitContext = createContext<AutoCommitState>(idle)

export function useAutoCommit(): AutoCommitState {
  return useContext(AutoCommitContext)
}

// The failed ui entries this browser has reported already, per account (ids start from 1 in
// each account).
const SEEN_FAILED_KEY = 'bwtidy.seenFailedUiEntries'
const SEEN_FAILED_LIMIT = 500

function readSeen(queryClient: QueryClient): Set<number> {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(accountStorageKey(queryClient, SEEN_FAILED_KEY)) ?? '[]')
    return new Set(Array.isArray(stored) ? stored.filter((id): id is number => typeof id === 'number') : [])
  } catch {
    return new Set()
  }
}

// The failed ui entries among these that this browser has not reported yet, which count as
// reported from now on.
export function takeUnreported(queryClient: QueryClient, failed: readonly QueueEntry[]): QueueEntry[] {
  const seen = readSeen(queryClient)
  const fresh = failed.filter((entry) => isUiEntry(entry) && !seen.has(entry.id))
  if (fresh.length > 0) {
    try {
      const ids = [...seen, ...fresh.map((entry) => entry.id)].slice(-SEEN_FAILED_LIMIT)
      localStorage.setItem(accountStorageKey(queryClient, SEEN_FAILED_KEY), JSON.stringify(ids))
    } catch {
      // Without storage a failure may be reported again, which is harmless.
    }
  }
  return fresh
}

function itemNames(entry: QueueEntry): string {
  const names = [...new Set(entry.diff.map((row) => row.itemName).filter((name): name is string => !!name))]
  return names.length > 0 ? names.join(', ') : entry.summary
}

function conflictIds(entry: QueueEntry): string[] {
  // A folder's conflict names no item.
  const named = entry.result?.conflicts?.flatMap((conflict) => (conflict.itemId ? [conflict.itemId] : [])) ?? []
  return named.length > 0 ? named : entry.diff.flatMap((row) => (row.itemId ? [row.itemId] : []))
}

// What one failed entry adds to the snackbar and the grid, kept apart so a
// dismiss of the entry takes back its own marks and leaves the others.
export interface FailedEntryMarks {
  id: number
  line: string
  // A revisionDate conflict, its items get conflict marks instead of the red tint.
  conflicted: boolean
  errorCells: ReadonlyMap<string, ReadonlySet<string>>
  conflicts: ConflictMarks
}

export function failedEntryMarks(
  entries: readonly QueueEntry[],
  before: readonly GridRow[],
  after: readonly GridRow[],
): FailedEntryMarks[] {
  return entries.map((entry) => {
    const line = `${itemNames(entry)}: ${failureReason(entry)}`
    if (entry.result?.error === 'conflicts') {
      const conflicted = new Set(conflictIds(entry))
      return {
        id: entry.id,
        line,
        conflicted: true,
        errorCells: new Map(),
        conflicts: conflictMarks(
          before.filter((row) => conflicted.has(row.id)),
          after,
        ),
      }
    }
    const errorCells = new Map<string, Set<string>>()
    for (const { itemId, column } of entry.diff) {
      if (itemId) {
        errorCells.set(itemId, (errorCells.get(itemId) ?? new Set()).add(column))
      }
    }
    return { id: entry.id, line, conflicted: false, errorCells, conflicts: noConflictMarks }
  })
}

// The snackbar for the failed entries, null when there are none (left).
export function failureOf(parts: readonly FailedEntryMarks[]): CommitFailure | null {
  if (parts.length === 0) {
    return null
  }
  const errorCells = new Map<string, Set<string>>()
  for (const part of parts) {
    for (const [itemId, columns] of part.errorCells) {
      errorCells.set(itemId, new Set([...(errorCells.get(itemId) ?? []), ...columns]))
    }
  }
  const count = parts.length === 1 ? 'a change' : `${parts.length} changes`
  const hint = parts.some((part) => part.conflicted) ? ' Items that changed elsewhere are marked in the reloaded grid.' : ''
  return { message: `Could not save ${count}.${hint}`, lines: parts.map((part) => part.line), errorCells }
}

export function conflictsOf(parts: readonly FailedEntryMarks[]): ConflictMarks {
  return parts.reduce((joined, part) => joinConflictMarks(joined, part.conflicts), noConflictMarks)
}
