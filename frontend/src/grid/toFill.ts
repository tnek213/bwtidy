import { apiFetch } from '../api/api.ts'
import { type GridColumn, type GridRow, isToolNote } from '../api/grid.ts'
import { type ShownRow, shownValue } from './queued.ts'
import { hasValue } from './columnValues.ts'

// The secret columns still to fill in, by item id (GET /api/grid's toFill).
export type ToFill = Record<string, string[]>

export const TO_FILL_CELL_CLASS = 'bw-to-fill-cell'

export const TO_FILL_TOOLTIP = 'Fill this in: double click to set it'

export function toFillCount(toFill: ToFill): number {
  return Object.values(toFill).reduce((count, columns) => count + columns.length, 0)
}

export function isToFill(toFill: ToFill, itemId: string, columnId: string): boolean {
  return toFill[itemId]?.includes(columnId) ?? false
}

export function toFillRows<Row extends GridRow>(rows: Row[], toFill: ToFill): Row[] {
  return rows.filter((row) => (toFill[row.id]?.length ?? 0) > 0)
}

export type ToFillAction = 'mark' | 'unmark'

// A listed cell can be unmarked. An empty secret cell the item has can be marked,
// unless an unsaved edit fills it. A filled cell offers neither.
export function toFillAction(column: GridColumn, row: ShownRow, toFill: ToFill): ToFillAction | null {
  if (isToolNote(row)) {
    return null
  }
  if (isToFill(toFill, row.id, column.id)) {
    return 'unmark'
  }
  if (column.secret && (row.emptySecrets ?? []).includes(column.id) && !hasValue(shownValue(row, column.id))) {
    return 'mark'
  }
  return null
}

// The change is queued, the answer is the list with it.
export async function markToFill(itemId: string, column: string, mark: boolean): Promise<ToFill> {
  const answer = await apiFetch<{ toFill: ToFill }>('/api/to-fill', {
    method: 'POST',
    body: { itemId, column, mark },
  })
  return answer.toFill
}
