import { useQuery } from '@tanstack/react-query'
import { createContext } from 'react'
import { ApiError, apiFetch } from '../api/api.ts'
import { fieldName, isFieldColumn } from '../shared/coordinates.ts'
import { type GridColumn, gridQueryKey, type GridRow, isToolNote } from '../api/grid.ts'
import type { ItemRef, MergeColumnsOperation, Operation } from '../api/operations.ts'
import { hasValue } from './columnValues.ts'

interface NearDuplicateGroup {
  key: string
  columns: string[]
}

export interface MergeConflict {
  itemId: string
  columns: string[]
}

// Under the grid key, so the reload after every write refreshes it too.
const nearDuplicatesQueryKey = [...gridQueryKey, 'near-duplicates'] as const

export function useNearDuplicates() {
  return useQuery({
    queryKey: nearDuplicatesQueryKey,
    queryFn: () => apiFetch<NearDuplicateGroup[]>('/api/near-duplicates'),
    retry: false,
  })
}

const TYPE_GROUPS = new Set(['login', 'card', 'identity', 'sshKey'])

// Custom fields and the built-in leaves of a type can take part in a column merge.
export function isMergeable(column: GridColumn): boolean {
  if (column.readOnly || column.multi) {
    return false
  }
  return column.group === 'fields' || TYPE_GROUPS.has(column.group)
}

function itemRef(row: GridRow): ItemRef {
  return { itemId: row.id, revisionDate: row.revisionDate }
}

// The items a normalizing operation may touch: all but bwtidy's own note.
export function normalizable(rows: GridRow[]): GridRow[] {
  return rows.filter((row) => !isToolNote(row))
}

// Every item that has the field, under any of its occurrence columns.
function itemsWithField(rows: GridRow[], columns: GridColumn[], name: string): ItemRef[] {
  const ids = columns.filter((column) => isFieldColumn(column.id) && fieldName(column.id) === name).map((column) => column.id)
  return normalizable(rows)
    .filter((row) => ids.some((id) => row.cells[id] !== undefined))
    .map(itemRef)
}

export function itemsWithAny(rows: GridRow[], columnIds: string[]): GridRow[] {
  return normalizable(rows).filter((row) => columnIds.some((id) => row.cells[id] !== undefined))
}

// Items the server will ask about: values in more than one of the columns.
export function expectedConflicts(rows: GridRow[], columnIds: string[]): GridRow[] {
  return normalizable(rows).filter((row) => columnIds.filter((id) => hasValue(row.cells[id])).length > 1)
}

export function renameField(rows: GridRow[], columns: GridColumn[], from: string, to: string): Operation {
  return { kind: 'renameField', from, to, items: itemsWithField(rows, columns, from) }
}

export function mergeColumns(rows: GridRow[], sources: string[], target: string): MergeColumnsOperation {
  return { kind: 'mergeColumns', sources, target, items: itemsWithAny(rows, sources).map(itemRef) }
}

// append names the target list columns that get the source's entries added
// instead of replaced.
export function mergeItems(from: GridRow, to: GridRow, mapping: Record<string, string>, append: string[] = []): Operation {
  return {
    kind: 'mergeItems',
    fromItemId: from.id,
    fromRevisionDate: from.revisionDate,
    toItemId: to.id,
    toRevisionDate: to.revisionDate,
    mapping,
    append,
  }
}

export function mergeConflictsOf(error: unknown): MergeConflict[] | null {
  if (!(error instanceof ApiError) || error.status !== 409) {
    return null
  }
  const { body } = error
  if (typeof body !== 'object' || body === null || !('mergeConflicts' in body) || !Array.isArray(body.mergeConflicts)) {
    return null
  }
  return body.mergeConflicts.filter(
    (conflict): conflict is MergeConflict =>
      typeof conflict === 'object' && conflict !== null && typeof conflict.itemId === 'string' && Array.isArray(conflict.columns),
  )
}

// The column with most values goes first, as the natural merge target.
export function byValueCount(rows: GridRow[], columnIds: string[]): string[] {
  const count = (id: string) => normalizable(rows).filter((row) => hasValue(row.cells[id])).length
  return [...columnIds].sort((a, b) => count(b) - count(a))
}

// Leaves an item can give away in a merge: its values except name and the
// organizational columns, which belong to the item itself.
export function movableColumns(columns: GridColumn[], row: GridRow): GridColumn[] {
  return columns.filter(
    (column) =>
      !column.readOnly && (column.group !== 'item' || column.id === 'notes') && hasValue(row.cells[column.id]),
  )
}

// Where a leaf of another item can go in this item: its custom fields, its
// notes and the built-in leaves of its type, with the same shape (list or not).
export function targetColumnsFor(columns: GridColumn[], target: GridRow, source: GridColumn): GridColumn[] {
  return columns.filter(
    (column) =>
      !column.readOnly &&
      column.multi === source.multi &&
      (column.group === 'fields' || column.id === 'notes' || column.group === target.cells.type),
  )
}

const NEAR_DUPLICATE_COLORS = ['#e65100', '#6a1b9a', '#00838f', '#c62828', '#2e7d32', '#1565c0']

// Same indexes as NEAR_DUPLICATE_COLORS, lighter for a dark background.
const NEAR_DUPLICATE_DARK_COLORS = ['#ffb74d', '#ce93d8', '#4dd0e1', '#ef9a9a', '#81c784', '#64b5f6']

export interface NearDuplicate {
  group: NearDuplicateGroup
  color: string
  darkColor: string
}

export function nearDuplicatesByColumn(groups: NearDuplicateGroup[]): Map<string, NearDuplicate> {
  const byColumn = new Map<string, NearDuplicate>()
  groups.forEach((group, index) => {
    const color = NEAR_DUPLICATE_COLORS[index % NEAR_DUPLICATE_COLORS.length]
    const darkColor = NEAR_DUPLICATE_DARK_COLORS[index % NEAR_DUPLICATE_DARK_COLORS.length]
    for (const column of group.columns) {
      byColumn.set(column, { group, color, darkColor })
    }
  })
  return byColumn
}

export interface HeaderActions {
  nearDuplicates: Map<string, NearDuplicate>
  openMenu: (columnId: string, anchor: HTMLElement) => void
  mergeColumns: (columnIds: string[]) => void
}

export const HeaderContext = createContext<HeaderActions>({
  nearDuplicates: new Map(),
  openMenu: () => {},
  mergeColumns: () => {},
})
