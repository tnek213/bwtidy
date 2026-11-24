// Mirrors backend/src/bwtidy/items/coordinates.py

import type { ColumnGroup } from '../api/grid.ts'

export const GROUP_ORDER: ColumnGroup[] = ['item', 'login', 'card', 'identity', 'sshKey', 'fields']

export const FIELDS_PREFIX = 'fields.'

const OCCURRENCE = /#([1-9][0-9]*)$/

// A name that itself ends in #N carries its occurrence, so the column parses back to the same field.
export function fieldColumn(name: string, occurrence = 1): string {
  return occurrence > 1 || OCCURRENCE.test(name) ? `${FIELDS_PREFIX}${name}#${occurrence}` : `${FIELDS_PREFIX}${name}`
}

// A trailing #N is always the occurrence.
export function parseFieldColumn(columnId: string): { name: string; occurrence: number } | null {
  if (!columnId.startsWith(FIELDS_PREFIX)) {
    return null
  }
  const name = columnId.slice(FIELDS_PREFIX.length)
  const match = OCCURRENCE.exec(name)
  return match === null ? { name, occurrence: 1 } : { name: name.slice(0, match.index), occurrence: Number(match[1]) }
}

export function isFieldColumn(columnId: string): boolean {
  return columnId.startsWith(FIELDS_PREFIX)
}

// The field name of a field column, never its occurrence.
export function fieldName(columnId: string): string {
  return columnId.slice(FIELDS_PREFIX.length).replace(OCCURRENCE, '')
}

// The group a column id belongs to: its prefix before the first dot, or item.
export function columnGroup(columnId: string): ColumnGroup {
  if (isFieldColumn(columnId)) {
    return 'fields'
  }
  const dot = columnId.indexOf('.')
  const head = dot < 0 ? '' : columnId.slice(0, dot)
  return GROUP_ORDER.find((group) => group === head) ?? 'item'
}
