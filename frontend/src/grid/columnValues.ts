import type { CellValue, GridRow } from '../api/grid.ts'
import { formatValue } from '../shared/values.ts'

export function hasValue(value: CellValue | undefined): boolean {
  return value !== undefined && value !== '' && value !== false && !(Array.isArray(value) && value.length === 0)
}

export function compareValues(a: CellValue | undefined, b: CellValue | undefined): number {
  if (typeof a === 'number' && typeof b === 'number') {
    return a - b
  }
  return formatValue(a).localeCompare(formatValue(b), undefined, { sensitivity: 'base', numeric: true })
}

export function nonEmptyColumns(rows: GridRow[]): Set<string> {
  const ids = new Set<string>()
  for (const { cells } of rows) {
    for (const [id, value] of Object.entries(cells)) {
      if (hasValue(value)) {
        ids.add(id)
      }
    }
  }
  return ids
}

// How many of the rows have a value in each column, a false boolean counting as empty.
export function columnCounts(rows: GridRow[]): Map<string, number> {
  const counts = new Map<string, number>()
  for (const { cells } of rows) {
    for (const [id, value] of Object.entries(cells)) {
      if (hasValue(value)) {
        counts.set(id, (counts.get(id) ?? 0) + 1)
      }
    }
  }
  return counts
}
