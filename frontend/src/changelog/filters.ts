import { useState } from 'react'
import { type GridColumn, type GridRow, useGridData } from '../api/grid.ts'
import { OPERATION_LABELS } from '../shared/operationLabels.ts'
import { type ChangeLogEntry, useChangeLog } from '../api/changeLog.ts'
import type { LogRow } from './rows.ts'

// What the filter bar above the Change log holds. Every part left empty does not filter.
export interface ChangeLogFilters {
  // Local days as the date fields hold them (YYYY-MM-DD).
  from: string
  to: string
  sources: string[]
  kinds: string[]
  columns: string[]
  // Matched against the item names of the grid, sent as the ids of the matching items.
  items: string
  result: '' | 'ok' | 'failed'
}

export const noChangeLogFilters: ChangeLogFilters = {
  from: '',
  to: '',
  sources: [],
  kinds: [],
  columns: [],
  items: '',
  result: '',
}

// Whether any field differs from no filters at all. Text counts as set even when blank, so it can be cleared.
export function hasFilters(filters: ChangeLogFilters): boolean {
  return (
    filters.from !== noChangeLogFilters.from ||
    filters.to !== noChangeLogFilters.to ||
    filters.sources.length > 0 ||
    filters.kinds.length > 0 ||
    filters.columns.length > 0 ||
    filters.items !== noChangeLogFilters.items ||
    filters.result !== noChangeLogFilters.result
  )
}

// The kinds the Operation filter offers, by their labels.
export const FILTER_KINDS = Object.keys(OPERATION_LABELS)

// The coordinates a change is logged under besides the grid's columns.
const ITEM_COORDINATES = ['name', 'folder', 'collections', 'organization']

// The Field filter's choices: the grid's columns a write can change, then what the grid shows another way.
function fieldOptions(columns: GridColumn[] | undefined): string[] {
  const writable = (columns ?? []).filter((column) => !column.readOnly).map((column) => column.id)
  return [...new Set([...ITEM_COORDINATES, ...writable])]
}

// A long id list does not fit in a URL, the Items filter waits for a narrower text.
export const ITEM_LIMIT = 100

// The ids of the items whose name holds the text, case ignored.
function matchingItemIds(rows: GridRow[] | undefined, text: string): string[] {
  const wanted = text.trim().toLowerCase()
  return (rows ?? [])
    .filter((row) => typeof row.cells.name === 'string' && row.cells.name.toLowerCase().includes(wanted))
    .map((row) => row.id)
}

// UTC instants of a local day's first and last millisecond.
function dayStart(day: string): string {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date, 0, 0, 0, 0).toISOString()
}

function dayEnd(day: string): string {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date, 23, 59, 59, 999).toISOString()
}

// The query string of GET /api/change-log for the filters, '' for none. Null when no row can
// match: the Items text names no item.
function changeLogQuery(filters: ChangeLogFilters, itemIds: string[] | undefined): string | null {
  const params = new URLSearchParams()
  if (filters.from) {
    params.append('from', dayStart(filters.from))
  }
  if (filters.to) {
    params.append('to', dayEnd(filters.to))
  }
  for (const source of filters.sources) {
    params.append('source', source)
  }
  for (const kind of filters.kinds) {
    params.append('kind', kind)
  }
  for (const column of filters.columns) {
    params.append('column', column)
  }
  if (itemIds !== undefined) {
    if (itemIds.length === 0) {
      return null
    }
    for (const id of itemIds) {
      params.append('item', id)
    }
  }
  if (filters.result) {
    params.append('result', filters.result)
  }
  const query = params.toString()
  return query === '' ? '' : `?${query}`
}

// With a filter set, a backup row shows only when a shown change row names it.
export function shownRows(rows: LogRow[], entries: ChangeLogEntry[], filtered: boolean): LogRow[] {
  if (!filtered) {
    return rows
  }
  const named = new Set(entries.flatMap((entry) => [entry.backup, entry.restoredBackup]))
  return rows.filter((row) => row.kind === 'change' || named.has(row.backup.name))
}

// The Change log's filters and the rows they let through, in one place for whatever reads the shown rows.
export function useFilteredChangeLog() {
  const [filters, setFilters] = useState<ChangeLogFilters>(noChangeLogFilters)
  const grid = useGridData()
  const rows = grid.data?.rows
  const matched = filters.items.trim() === '' || rows === undefined ? undefined : matchingItemIds(rows, filters.items)
  const itemIds = matched !== undefined && matched.length <= ITEM_LIMIT ? matched : undefined
  const query = changeLogQuery(filters, itemIds)
  const log = useChangeLog(query)
  return {
    filters,
    setFilters,
    // Whether a filter applies, so the backups no change row names are hidden.
    filtered: query !== '',
    matchedItems: matched?.length,
    fields: fieldOptions(grid.data?.columns),
    log,
  }
}
