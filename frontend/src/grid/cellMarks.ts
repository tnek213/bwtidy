import type { CellFormat } from './formats.ts'

// What the cell class rules and tooltips of the grid read when they run.

// A cell of a UI edit the auto-commit could not write, for no revisionDate conflict.
export const FAILED_CELL_CLASS = 'bw-failed-cell'

type IsMarkedCell = (itemId: string, columnId: string) => boolean

// Read by the cell callbacks when they run, so the column definitions stay put while these change.
export interface CellMarks {
  isConflictCell: IsMarkedCell
  isFailedCell: IsMarkedCell
  // A secret cell on the to-fill list.
  isToFillCell: IsMarkedCell
  // The cell's format, when the template the row's template column shows has a datatype on it.
  formatOf: (itemId: string, columnId: string) => CellFormat | undefined
  searchWords: () => string[]
  // When the auto-commit writes the unsaved edits, for the tooltips.
  commitNote: () => string | undefined
}

export const noCellMarks: CellMarks = {
  isConflictCell: () => false,
  isFailedCell: () => false,
  isToFillCell: () => false,
  formatOf: () => undefined,
  searchWords: () => [],
  commitNote: () => undefined,
}
