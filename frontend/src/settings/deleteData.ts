import { ApiError } from '../api/api.ts'
import type { PendingCounts } from '../api/deleteData.ts'

// The pending counts of a 409, or null when the 409 is something else (a failed commit, a write
// that is running). Only the first body with a numeric pending count is the choice.
export function pendingCountsOf(error: unknown): PendingCounts | null {
  if (!(error instanceof ApiError) || error.status !== 409) {
    return null
  }
  const body = error.body
  if (typeof body === 'object' && body !== null && 'pending' in body && 'mcpPending' in body) {
    const { pending, mcpPending } = body as Record<string, unknown>
    if (typeof pending === 'number' && typeof mcpPending === 'number') {
      return { pending, mcpPending }
    }
  }
  return null
}

export function deleteErrorText(error: unknown): string {
  if (error instanceof ApiError && error.detail) {
    return error.detail
  }
  return 'Nothing was deleted, try again'
}
