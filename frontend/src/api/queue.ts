import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api.ts'
import type { Masked } from './grid.ts'
import type { Operation } from './operations.ts'

export type QueueStatus = 'pending' | 'approved' | 'rejected' | 'applied' | 'failed'

export type DiffValue = string | number | boolean | string[] | Masked | null

// A URI as the diff of a setUris shows it, match as its word, null for the client's default.
export interface ShownUri {
  uri: string
  match: string | null
}

export interface DiffRow {
  itemId: string | null
  itemName: string | null
  column: string
  before: DiffValue
  after: DiffValue
  // A setUris row: both URI lists with every match setting, which before and after leave out.
  uris?: { before: ShownUri[]; after: ShownUri[] }
}

interface QueueResult {
  error?: string
  detail?: string
  backup?: string | null
  // The items a failed entry found changed elsewhere (error "conflicts"), or the folder of a
  // rename or delete (folderId instead of itemId, expected the name the entry showed and actual
  // the folder's name then, null when it was gone).
  conflicts?: { itemId?: string; folderId?: string; expected?: string | null; actual?: string | null }[]
}

export interface QueueEntry {
  id: number
  createdAt: string
  source: string
  summary: string
  status: QueueStatus
  decidedAt: string | null
  operation: { kind?: string } & Record<string, unknown>
  diff: DiffRow[]
  diffError: string | null
  result: QueueResult | null
  // A failed entry whose hidden value was deleted when the worker started again.
  valueGone?: boolean
}

export interface ApproveOutcome {
  applied: number[]
  failed: number[]
  pending: number[]
  backup: string | null
}

export const QUEUE_POLL_MS = 10_000

// While the auto-commit has UI edits to write, so the grid follows it closely.
export const FAST_POLL_MS = 1_000

export const queueQueryKey = ['queue'] as const

export function queueKey(status: QueueStatus) {
  return [...queueQueryKey, status] as const
}

// The pending list is polled for the app bar badge, the others only for the
// unread counts of the Queue page. A polled list is a background request that
// does not keep an idle session alive.
export function useQueue(status: QueueStatus, pollMs = status === 'pending' ? QUEUE_POLL_MS : undefined) {
  return useQuery({
    queryKey: queueKey(status),
    queryFn: () => fetchQueue(status, pollMs !== undefined),
    refetchInterval: pollMs ?? false,
  })
}

export function fetchQueue(status: QueueStatus, background = false): Promise<QueueEntry[]> {
  return apiFetch<QueueEntry[]>(`/api/queue?status=${status}`, { background })
}

export function approveEntries(ids: number[]): Promise<ApproveOutcome> {
  return apiFetch<ApproveOutcome>('/api/queue/approve', { method: 'POST', body: { ids } })
}

export function rejectEntries(ids: number[]): Promise<{ rejected: number[] }> {
  return apiFetch<{ rejected: number[] }>('/api/queue/reject', { method: 'POST', body: { ids } })
}

interface DismissOutcome {
  dismissed: number[]
  // Ids that were unknown or not failed, left alone.
  notFailed: number[]
}

// Deletes failed entries, nobody reads a record of a dismissed failure.
export function dismissEntries(ids: number[]): Promise<DismissOutcome> {
  return apiFetch<DismissOutcome>('/api/queue/dismiss', { method: 'POST', body: { ids } })
}

// Puts failed entries back and applies them at once. A conflict needs overwrite, which writes
// over the change made elsewhere (a backup holds it). 409 for an entry that cannot be retried.
export function retryEntries(ids: number[], overwrite: boolean): Promise<ApproveOutcome> {
  return apiFetch<ApproveOutcome>('/api/queue/retry', { method: 'POST', body: { ids, overwrite } })
}

interface QueuedResult {
  ids: number[]
}

// Queued from source "ui" and checked against the vault at once: 409 and 422 as POST /api/operations answers them.
export function queueOperations(operations: Operation[]): Promise<QueuedResult> {
  return apiFetch<QueuedResult>('/api/queue', { method: 'POST', body: { operations } })
}
