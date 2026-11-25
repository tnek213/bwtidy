import { columnGroup } from '../shared/coordinates.ts'
import { type ColumnGroup, type GridColumn, type GridRow, NAME_COLUMN } from '../api/grid.ts'

export const GROUP_LABELS: Record<ColumnGroup, string> = {
  item: 'Item',
  login: 'Login',
  card: 'Card',
  identity: 'Identity',
  sshKey: 'SSH key',
  fields: 'Custom fields',
}

// The grid's group headers name the shared coordinate prefix, so the column headers under them can leave it out.
export function groupHeaderLabel(group: ColumnGroup): string {
  return group === 'item' ? GROUP_LABELS.item : `${GROUP_LABELS[group]} (${group})`
}

// A column label is its coordinate, so drop the group's prefix and dot.
export function shortColumnLabel(column: Pick<GridColumn, 'label' | 'group'>): string {
  const prefix = `${column.group}.`
  return column.group !== 'item' && column.label.startsWith(prefix) ? column.label.slice(prefix.length) : column.label
}

// What the grid's column header says for a column id, whether or not the grid has that column.
export function columnHeaderLabel(columnId: string): string {
  return shortColumnLabel({ label: columnId, group: columnGroup(columnId) })
}

export function rowName(row: GridRow): string {
  const name = row.cells[NAME_COLUMN]
  return typeof name === 'string' ? name : row.id
}

// The header tooltip of a column: its full coordinate and how many items have a value in it.
export function columnTooltip(columnId: string, count: number): string {
  return `${columnId}, in ${count === 0 ? 'no item' : count === 1 ? '1 item' : `${count} items`}`
}
