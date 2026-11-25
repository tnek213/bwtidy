import type { GridRow } from '../api/grid.ts'
import { formatValue, isMasked } from './values.ts'

// Item id to the columns whose shown value changed. Every key is a row in
// conflict, also when the set is empty (the change was in a masked cell).
export type ConflictMarks = ReadonlyMap<string, ReadonlySet<string>>

export const noConflictMarks: ConflictMarks = new Map()

export const CONFLICT_ROW_CLASS = 'bw-conflict-row'
export const CONFLICT_CELL_CLASS = 'bw-conflict-cell'

// A light purple, apart from the amber of warnings and other highlights. The
// cell tint lies on top of the row tint.
export const conflictStyles = {
  [`& .${CONFLICT_ROW_CLASS}`]: { backgroundColor: 'rgba(156, 39, 176, 0.07)' },
  [`& .${CONFLICT_CELL_CLASS}`]: { backgroundColor: 'rgba(156, 39, 176, 0.14)' },
}

// Always differs in a conflict and says nothing about what changed.
const IGNORED = new Set(['revisionDate'])

// Compares the conflicting rows as the grid showed them with the rows after
// the reload. An item missing from `after` vanished and gets no marks.
// Masked cells are never compared: their change cannot be seen.
export function conflictMarks(before: readonly GridRow[], after: readonly GridRow[]): ConflictMarks {
  const afterById = new Map(after.map((row) => [row.id, row]))
  const marks = new Map<string, Set<string>>()
  for (const old of before) {
    const now = afterById.get(old.id)
    if (!now) {
      continue
    }
    const changed = new Set<string>()
    for (const column of new Set([...Object.keys(old.cells), ...Object.keys(now.cells)])) {
      const was = old.cells[column]
      const is = now.cells[column]
      if (IGNORED.has(column) || isMasked(was) || isMasked(is)) {
        continue
      }
      if (formatValue(was) !== formatValue(is)) {
        changed.add(column)
      }
    }
    marks.set(old.id, changed)
  }
  return marks
}

// Marks of two sources together, a row's changed columns joined.
export function joinConflictMarks(a: ConflictMarks, b: ConflictMarks): ConflictMarks {
  if (b.size === 0) {
    return a
  }
  if (a.size === 0) {
    return b
  }
  const joined = new Map<string, ReadonlySet<string>>(a)
  for (const [itemId, columns] of b) {
    joined.set(itemId, new Set([...(joined.get(itemId) ?? []), ...columns]))
  }
  return joined
}
