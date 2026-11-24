import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { apiFetch, apiFetchWithHeaders } from './api.ts'
import type { Operation } from './operations.ts'

export interface ChangeLogEntry {
  id: number
  at: string
  source: string
  operationKind: string
  itemIds: string[]
  // The items the operation created, which a restore of the row trashes.
  createdItemIds: string[]
  // The coordinates the operation wrote, as the grid names them. Empty for a whole-item kind.
  columns: string[]
  result: string
  backup: string | null
  // A restore row: the backup it put back and the change log row it was planned for.
  restoredBackup: string | null
  restoredChangeId: number | null
  // The first later restore that undid this change, with the items of this row it put back.
  restoredBy: RestoredBy | null
  // The folder a rename, delete or restore of a folder was about (the one a restore created
  // again), null on every other row.
  folderId: string | null
  // An attachment rename's attachment and the copy that replaced it (null when it failed before),
  // null on every other row. Absent from an older answer.
  attachmentIds?: AttachmentIds | null
}

export interface AttachmentIds {
  before: string
  after: string | null
}

export interface RestoredBy {
  id: number
  at: string
  itemIds: string[]
}

export const changeLogQueryKey = ['change-log'] as const

// The most rows GET /api/change-log answers.
export const CHANGE_LOG_LIMIT = 200

interface ChangeLogAnswer {
  entries: ChangeLogEntry[]
  // More rows matched than the answer holds.
  more: boolean
}

// The newest rows that match the query (changeLogQuery in filters.ts), none for a null query.
export function useChangeLog(query: string | null = '') {
  return useQuery({
    queryKey: [...changeLogQueryKey, 'entries', query],
    queryFn: async (): Promise<ChangeLogAnswer> => {
      if (query === null) {
        return { entries: [], more: false }
      }
      const { body, headers } = await apiFetchWithHeaders<ChangeLogEntry[]>(`/api/change-log${query}`)
      return { entries: body.sort((a, b) => b.at.localeCompare(a.at)), more: headers.get('X-Bwtidy-More') === 'true' }
    },
    // The rows of the last filters stay while the next ones load, so typing does not blank the table.
    placeholderData: keepPreviousData,
    refetchOnMount: 'always',
  })
}

export interface Backup {
  name: string
  size: number
  time: string
}

// Under the change log's key, so the reload after every commit (which takes a backup) reloads it too.
const backupsQueryKey = [...changeLogQueryKey, 'backups'] as const

// The backups that exist now, newest first. The retention and Delete remove old ones.
export function useBackups() {
  return useQuery({
    queryKey: backupsQueryKey,
    queryFn: () => apiFetch<Backup[]>('/api/backups'),
    refetchOnMount: 'always',
  })
}

export function downloadUrl(backup: Backup): string {
  return `/api/backups/${encodeURIComponent(backup.name)}`
}

export function deleteBackup(name: string): Promise<void> {
  return apiFetch(`/api/backups/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

interface RestorePlanItem {
  itemId: string
  itemName: string | null
  action: string
  text: string
}

// What the restore of a folder row does with the folder, name being its name now (else the
// backup's).
interface RestorePlanFolder {
  folderId: string
  name: string
  action: string
  text: string
}

export interface RestorePlan {
  changeLogId: number
  backup: string
  summary: string
  operation: Extract<Operation, { kind: 'restoreItems' | 'restoreFolder' | 'renameAttachment' }>
  items: RestorePlanItem[]
  // Set for a folder row, whose items are empty.
  folder?: RestorePlanFolder
}

export function useRestorePlan(changeLogId: number | string) {
  return useQuery({
    queryKey: [...changeLogQueryKey, 'restore-plan', changeLogId],
    queryFn: () => apiFetch<RestorePlan>(`/api/restore-plan?changeLogId=${encodeURIComponent(changeLogId)}`),
    // A refusal (422) says why at once, asking again changes nothing.
    retry: false,
    // The plan depends on the vault now, an old one would carry old revisionDates.
    gcTime: 0,
    refetchOnWindowFocus: false,
  })
}
