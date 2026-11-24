import type { QueueEntry } from '../api/queue.ts'

// Edits the user made in the web UI, committed automatically. Everything else came through MCP.
export const UI_SOURCE = 'ui'

export function failureReason(entry: QueueEntry): string {
  return entry.result?.detail ?? entry.result?.error ?? 'No reason given'
}
