// The Rare fields column (docs/spec.md, Grid): the custom fields and built-in slots at most N items
// use, shown for each item as JSON in one cell. It is no field of the vault, bwtidy computes it.
import { GROUP_ORDER } from '../shared/coordinates.ts'
import { type CellValue, type ColumnGroup, type GridColumn, type GridRow, isToolNote } from '../api/grid.ts'
import { isMasked, MASK } from '../shared/values.ts'
import { shortColumnLabel } from './labels.ts'
import { hasValue } from './columnValues.ts'
import { cellMatchesSearch } from './filters.ts'

// The grid's own column, like the template column. A coordinate is a fixed path or fields.<name>, so it never collides.
export const RARE_FIELDS_COLUMN = 'rareFields'

export const RARE_FIELDS_LIMIT = 99

// Every item of one of these types has all of its type's slots, so a slot counts only with a value.
const SLOT_GROUPS: ReadonlySet<ColumnGroup> = new Set(['login', 'card', 'identity', 'sshKey'])

// The N typed, null unless it is a whole number from 0 to RARE_FIELDS_LIMIT.
export function parseRareFieldsMax(text: string): number | null {
  if (!/^\d{1,2}$/.test(text.trim())) {
    return null
  }
  const value = Number(text.trim())
  return value <= RARE_FIELDS_LIMIT ? value : null
}

export function rareFieldsHeader(max: number): string {
  return max > 0 ? `Rare fields ≤ ${max}` : 'Rare fields (off)'
}

// Custom fields and the writable built-in slots. Never the item's own columns (name, notes, type,
// folder, organization, collections, favorite, reprompt), the read-only info columns or the template.
function isRareCandidate(column: GridColumn): boolean {
  return column.group === 'fields' || (SLOT_GROUPS.has(column.group) && !column.readOnly)
}

const FIELD_LISTS = ['emptyTextFields', 'emptySecrets', 'booleanFields', 'linkedFields'] as const

// A custom field is used when the item has it, an empty value too (an empty field has no cell, the
// row lists it instead). A built-in slot only when it holds a value.
function usesColumn(row: GridRow, column: GridColumn): boolean {
  if (column.group !== 'fields') {
    return hasValue(row.cells[column.id])
  }
  return row.cells[column.id] !== undefined || FIELD_LISTS.some((list) => row[list]?.includes(column.id) === true)
}

// The ids of the candidate columns that between 1 and max items use, counted over every row but the
// tool note (not only the rows a filter shows, so the set does not change with a filter). None for max 0.
export function rareColumns(columns: readonly GridColumn[], rows: readonly GridRow[], max: number): Set<string> {
  const rare = new Set<string>()
  if (max <= 0) {
    return rare
  }
  const counted = rows.filter((row) => !isToolNote(row))
  for (const column of columns) {
    if (!isRareCandidate(column)) {
      continue
    }
    let count = 0
    for (const row of counted) {
      if (usesColumn(row, column) && ++count > max) {
        break
      }
    }
    if (count >= 1 && count <= max) {
      rare.add(column.id)
    }
  }
  return rare
}

// The rare columns in the grid's order, the order of an item's JSON.
export function orderedRareColumns(columns: readonly GridColumn[], rare: ReadonlySet<string>): GridColumn[] {
  return GROUP_ORDER.flatMap((group) => columns.filter((column) => column.group === group && rare.has(column.id)))
}

interface RareEntry {
  columnId: string
  // The column's label as the grid's header shows it, the full coordinate when two would be the same.
  key: string
  // Undefined for an empty custom field, which shows as "".
  value: CellValue | undefined
}

// The rare columns the item uses, with their vault values (a secret stays masked).
export function rareEntries(row: GridRow, columns: readonly GridColumn[]): RareEntry[] {
  const used = columns.filter((column) => usesColumn(row, column))
  const labels = used.map(shortColumnLabel)
  return used.map((column, index) => ({
    columnId: column.id,
    key: labels.indexOf(labels[index]) === labels.lastIndexOf(labels[index]) ? labels[index] : column.label,
    value: row.cells[column.id],
  }))
}

// A value in JSON, a list as a JSON array. Never called on a masked value.
export function jsonValue(value: CellValue | undefined): string {
  if (value === undefined) {
    return '""'
  }
  if (Array.isArray(value)) {
    return `[${value.map((entry) => JSON.stringify(entry)).join(', ')}]`
  }
  return JSON.stringify(value)
}

export function jsonKey(key: string): string {
  return `${JSON.stringify(key)}: `
}

// The cell as text, a secret as the mask: what the column sorts by and its hover text shows.
export function rareText(entries: readonly RareEntry[]): string {
  if (entries.length === 0) {
    return ''
  }
  const parts = entries.map((entry) => jsonKey(entry.key) + (isMasked(entry.value) ? MASK : jsonValue(entry.value)))
  return `{ ${parts.join(', ')} }`
}

// Like a text cell: a shown value (never a secret one) holds a search word.
export function rareMatchesSearch(entries: readonly RareEntry[], words: string[]): boolean {
  return entries.some((entry) => cellMatchesSearch(entry.value, words))
}
