import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/api.ts'
import { queueOperations, queueQueryKey } from '../api/queue.ts'
import { conflictMarks, type ConflictMarks, noConflictMarks } from '../shared/conflicts.ts'
import { type GridData, gridQueryKey, type GridRow } from '../api/grid.ts'
import { FIRST_OPERATION, type Operation } from '../api/operations.ts'
import { rowName } from './labels.ts'

interface Conflict {
  itemId: string
  expected: string
  actual: string
}

function conflictsOf(body: unknown): Conflict[] {
  if (typeof body !== 'object' || body === null || !('conflicts' in body) || !Array.isArray(body.conflicts)) {
    return []
  }
  return body.conflicts.filter(
    (conflict): conflict is Conflict => typeof conflict === 'object' && conflict !== null && typeof conflict.itemId === 'string',
  )
}

export type Feedback =
  | { severity: 'success'; message: string }
  | { severity: 'warning' | 'error'; message: string; items?: string[] }

// A rename or delete of a folder that changed elsewhere names the folder, not an item.
function isFolderConflict(body: unknown): boolean {
  if (typeof body !== 'object' || body === null || !('conflicts' in body) || !Array.isArray(body.conflicts)) {
    return false
  }
  return body.conflicts.some((conflict: unknown) => typeof conflict === 'object' && conflict !== null && 'folderId' in conflict)
}

function mergeConflictIds(body: unknown): string[] | null {
  if (typeof body !== 'object' || body === null || !('mergeConflicts' in body) || !Array.isArray(body.mergeConflicts)) {
    return null
  }
  return body.mergeConflicts.map((conflict: { itemId?: unknown }) => String(conflict?.itemId))
}

function feedbackFor(error: unknown, names: (itemId: string) => string, count: number): Feedback {
  const merges = error instanceof ApiError && error.status === 409 ? mergeConflictIds(error.body) : null
  if (merges !== null) {
    return {
      severity: 'warning',
      message: 'Nothing was saved: these items already have a field with the new name. Merge the two columns instead.',
      items: merges.map(names),
    }
  }
  if (error instanceof ApiError && error.status === 409 && isFolderConflict(error.body)) {
    return {
      severity: 'warning',
      message: 'Nothing was saved: the folder changed elsewhere since the grid loaded. The grid was reloaded, check the folders and try again.',
    }
  }
  if (error instanceof ApiError && error.status === 409) {
    const items = conflictsOf(error.body).map((conflict) => names(conflict.itemId))
    return {
      severity: 'warning',
      message: 'Nothing was saved: these items changed elsewhere since the grid loaded. The grid was reloaded, check the values and try again.',
      items,
    }
  }
  if (error instanceof ApiError && error.status === 422) {
    const detail = count === 1 ? error.detail?.replace(FIRST_OPERATION, '') : error.detail
    return { severity: 'error', message: `Nothing was saved: ${detail ?? 'the change is not allowed.'}` }
  }
  const message = error instanceof Error ? error.message : String(error)
  return { severity: 'error', message: `Could not save: ${message}` }
}

// Queues every edit (POST /api/queue) and returns as soon as the backend
// took it, the auto-commit writes it later. The queue is reloaded after
// success, so the grid shows the queued value. A refused edit reloads the grid
// too, so the next edit is based on the revisionDates the server has now, and
// a revisionDate conflict leaves marks on the rows it found, compared with the
// reloaded rows, until the next accepted edit.
export function useWriter(rows: GridRow[], onQueued?: () => void) {
  const queryClient = useQueryClient()
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [conflicts, setConflicts] = useState<ConflictMarks>(noConflictMarks)
  const rowsRef = useRef(rows)
  useEffect(() => {
    rowsRef.current = rows
  }, [rows])
  const onQueuedRef = useRef(onQueued)
  useEffect(() => {
    onQueuedRef.current = onQueued
  }, [onQueued])

  // onError may take over an error the caller handles itself (a merge that
  // needs choices). It returns true when it did, and no feedback is shown.
  const apply = useCallback(
    async (operations: Operation[], onError?: (error: unknown) => boolean): Promise<boolean> => {
      try {
        await queueOperations(operations)
      } catch (error) {
        let before: GridRow[] | null = null
        if (error instanceof ApiError && error.status === 409) {
          const ids = new Set(conflictsOf(error.body).map((conflict) => conflict.itemId))
          if (ids.size > 0) {
            before = rowsRef.current.filter((row) => ids.has(row.id))
          }
        }
        const names = (itemId: string) => {
          const row = rowsRef.current.find((candidate) => candidate.id === itemId)
          return row ? rowName(row) : itemId
        }
        setFeedback(onError?.(error) ? null : feedbackFor(error, names, operations.length))
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: gridQueryKey }),
          queryClient.invalidateQueries({ queryKey: queueQueryKey }),
        ])
        if (before !== null) {
          setConflicts(conflictMarks(before, queryClient.getQueryData<GridData>(gridQueryKey)?.rows ?? []))
        }
        return false
      }
      setFeedback(null)
      setConflicts(noConflictMarks)
      onQueuedRef.current?.()
      // The pending list and the auto-commit status, both under the queue key.
      void queryClient.invalidateQueries({ queryKey: queueQueryKey })
      return true
    },
    [queryClient],
  )

  return { apply, feedback, setFeedback, conflicts }
}
