import type { GridColumn } from '../api/grid.ts'
import type { CellMarks } from './cellMarks.ts'
import { PLAIN_IN_SECRET_TOOLTIP, plainInSecretColumn, unavailableReason } from './editing.ts'
import { hasOwnGeneratorMark, OWN_GENERATOR_TOOLTIP } from './ownGenerator.ts'
import { queuedCell, queuedTooltip, type ShownRow } from './queued.ts'
import { TO_FILL_TOOLTIP } from './toFill.ts'
import { cellMatchesSearch } from './filters.ts'

export const CONFLICT_CELL_TEXT = 'Changed elsewhere since your edit.'
export const FAILED_CELL_TEXT = 'Your edit of this cell could not be saved.'
export const SEARCH_HIT_TEXT = 'Holds a word of the search.'

// What a cell's colors, marks and tooltip say, for a screen reader: the grid sets it as the
// focused cell's description (docs/reference/ui.md, Keyboard). The value is left out, the cell reads it.
export function cellDescription(column: GridColumn, row: ShownRow, marks: CellMarks): string | undefined {
  const queued = queuedCell(row, column.id)
  const unavailable = unavailableReason(column, row)
  const format = !queued && unavailable === null ? marks.formatOf(row.id, column.id)?.hover : undefined
  const lines = [
    queuedTooltip(row, column.id, marks.commitNote()),
    marks.isConflictCell(row.id, column.id) ? CONFLICT_CELL_TEXT : undefined,
    marks.isFailedCell(row.id, column.id) ? FAILED_CELL_TEXT : undefined,
    marks.isToFillCell(row.id, column.id) ? TO_FILL_TOOLTIP : undefined,
    unavailable,
    format,
    plainInSecretColumn(column, row) ? PLAIN_IN_SECRET_TOOLTIP : undefined,
    hasOwnGeneratorMark(column, row) ? OWN_GENERATOR_TOOLTIP : undefined,
    cellMatchesSearch(row.cells[column.id], marks.searchWords()) ? SEARCH_HIT_TEXT : undefined,
  ].filter((line): line is string => !!line)
  return lines.length > 0 ? lines.map(sentence).join(' ') : undefined
}

// One line of a tooltip as a sentence, for a description that runs them together.
function sentence(line: string): string {
  const text = line.replace(/\n+/g, ' ').trim()
  return /[.!?]$/.test(text) ? text : `${text}.`
}
