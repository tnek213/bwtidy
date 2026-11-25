import { apiFetch } from './api.ts'

// Deleting the account's data, or the whole account (docs/spec.md, Settings, Deleting data).

// What to do with the pending entries, null on the first try (a 409 then names them).
export type PendingChoice = 'write' | 'drop' | null

// The counts the backend answers 200 with for Delete my data.
export interface DeleteResult {
  backups: number
  changeLog: number
  queue: number
  // Backups kept because a pending restore proposal still needs them.
  keptBackups: string[]
}

// The 409 of the first try while entries are pending: how many UI edits and MCP proposals.
export interface PendingCounts {
  pending: number
  mcpPending: number
}

// The header POST /api/bw-logout needs too, which a page on another site cannot send.
const REQUEST_HEADERS = { 'X-Bwtidy-Request': '1' }

export function deleteMyData(pending: PendingChoice): Promise<DeleteResult> {
  return apiFetch<DeleteResult>('/api/account/delete-data', { method: 'POST', body: { pending }, headers: REQUEST_HEADERS })
}

export function deleteMyAccount(pending: PendingChoice): Promise<void> {
  return apiFetch('/api/account/delete', { method: 'POST', body: { pending }, headers: REQUEST_HEADERS })
}

