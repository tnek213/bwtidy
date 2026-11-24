import type { QueueEntry } from '../api/queue.ts'

type RetryKind = 'retry' | 'overwrite'

// How a failed entry is retried, null when it cannot be: one cut off mid-commit may be
// written already, a merge needs a choice per item, a hidden value deleted at a restart is gone.
export function retryKind(entry: QueueEntry): RetryKind | null {
  const error = entry.result?.error
  if (entry.status !== 'failed' || entry.valueGone || error === 'interrupted' || error === 'mergeConflicts') {
    return null
  }
  return error === 'conflicts' ? 'overwrite' : 'retry'
}

// What a failed entry without Retry says instead, null when it says nothing more.
export function noRetryNote(entry: QueueEntry): string | null {
  if (entry.status !== 'failed') {
    return null
  }
  if (entry.valueGone) {
    return 'The value is gone after a restart. Type it again in the grid.'
  }
  if (entry.result?.error === 'mergeConflicts') {
    return 'Propose the merge again with a choice per item.'
  }
  return null
}

// One line per item a conflict entry found changed elsewhere and column of it the entry writes,
// one for the folder of a rename or delete.
export function conflictLines(entry: QueueEntry): string[] {
  const lines: string[] = []
  for (const conflict of entry.result?.conflicts ?? []) {
    if (conflict.folderId !== undefined) {
      const name = conflict.expected ?? conflict.actual ?? conflict.folderId
      lines.push(`The folder ${name} was changed elsewhere since you edited it.`)
      continue
    }
    const rows = entry.diff.filter((row) => row.itemId === conflict.itemId)
    const item = rows.find((row) => row.itemName)?.itemName ?? conflict.itemId
    const columns = [...new Set(rows.map((row) => row.column))]
    for (const column of columns.length > 0 ? columns : ['item']) {
      const line = `${item}'s ${column} was changed elsewhere since you edited it.`
      if (!lines.includes(line)) {
        lines.push(line)
      }
    }
  }
  return lines
}
