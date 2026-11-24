import type { RowClassRules } from 'ag-grid-community'
import type { AgGridReact } from 'ag-grid-react'
import { type RefObject, useEffect, useMemo, useRef } from 'react'
import { type AutoCommitStatus, commitText } from '../commit/autoCommit.ts'
import type { CellMarks } from './cellMarks.ts'
import { CONFLICT_ROW_CLASS, type ConflictMarks } from '../shared/conflicts.ts'
import type { CellFormats } from './formats.ts'
import { isQueuedForTrash, QUEUED_TRASH_CLASS, type ShownRow } from './queued.ts'
import { isToFill, type ToFill } from './toFill.ts'

interface CellMarkSources {
  conflicts: ConflictMarks
  failedCells: ConflictMarks | undefined
  toFill: ToFill
  formats: CellFormats
  searchWords: string[]
  commitStatus: AutoCommitStatus | undefined
}

// Owns the marks the grid's class rules and tooltips read: conflicts, failed cells, the to-fill
// list, formats, search hits and the commit note. It gives the cells their marks and the rows
// their class rules, and redraws the grid when a mark changes.
// The values sit in refs so the marks and the row class rules stay the same functions across
// renders: AG Grid applies class rules again on a redraw, not when a new rule function arrives,
// and new column definitions on every render would redraw the grid each time.
export function useCellMarks(
  gridRef: RefObject<AgGridReact<ShownRow> | null>,
  { conflicts, failedCells, toFill, formats, searchWords, commitStatus }: CellMarkSources,
): { marks: CellMarks; rowClassRules: RowClassRules<ShownRow> } {
  const conflictsRef = useRef(conflicts)
  const failedRef = useRef(failedCells)
  const toFillRef = useRef(toFill)
  const formatsRef = useRef(formats)
  useEffect(() => {
    conflictsRef.current = conflicts
    failedRef.current = failedCells
    toFillRef.current = toFill
    formatsRef.current = formats
    gridRef.current?.api?.redrawRows()
  }, [gridRef, conflicts, failedCells, toFill, formats])
  const statusRef = useRef(commitStatus)
  useEffect(() => {
    statusRef.current = commitStatus
  }, [commitStatus])
  // Set while rendering, so typing in the search box does not rebuild the column definitions.
  const wordsRef = useRef(searchWords)
  wordsRef.current = searchWords
  useEffect(() => {
    gridRef.current?.api?.refreshCells({ force: true })
  }, [gridRef, searchWords])
  const marks = useMemo<CellMarks>(
    () => ({
      isConflictCell: (itemId, columnId) => conflictsRef.current.get(itemId)?.has(columnId) ?? false,
      isFailedCell: (itemId, columnId) => failedRef.current?.get(itemId)?.has(columnId) ?? false,
      isToFillCell: (itemId, columnId) => isToFill(toFillRef.current, itemId, columnId),
      formatOf: (itemId, columnId) => formatsRef.current.get(itemId)?.get(columnId),
      searchWords: () => wordsRef.current,
      commitNote: () => commitText(statusRef.current, Date.now()),
    }),
    [],
  )
  const rowClassRules = useMemo<RowClassRules<ShownRow>>(
    () => ({
      [QUEUED_TRASH_CLASS]: ({ data }) => isQueuedForTrash(data),
      [CONFLICT_ROW_CLASS]: ({ data }) => data !== undefined && conflictsRef.current.has(data.id),
    }),
    [],
  )
  return { marks, rowClassRules }
}
