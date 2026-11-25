import type { DiffValue, QueueEntry } from '../api/queue.ts'
import { columnGroup, FIELDS_PREFIX } from '../shared/coordinates.ts'
import type { CellValue, GridColumn, GridRow } from '../api/grid.ts'
import { formatValue, isMasked } from '../shared/values.ts'
import { UI_SOURCE } from '../shared/queueText.ts'

// The rules for showing pending queue entries in the grid as if they were
// applied. The grid itself keeps the vault values: search, filters and every
// write work on those, only what a cell shows comes from here.

// The pseudo column of a diff row that moves an item to the trash.
const TRASH_COLUMN = 'trash'
// The pseudo column of a diff row that says what a restore does with an item.
// The Queue page shows it, the grid has no cell for it.
const RESTORE_COLUMN = 'restore'

// A new item has no row in the grid until it is written, so its diff rows show on the Queue page only.
const CREATE_ITEM = 'createItem'

const MULTI_COLUMNS = new Set(['login.uris', 'collections'])

export const QUEUED_BLOCKER = 'A queued change waits on the Queue page. Approve or reject it first.'

export const QUEUED_CELL_CLASS = 'bw-queued-cell'
export const QUEUED_TRASH_CLASS = 'bw-queued-trash'

interface QueuedChange {
  entryId: number
  summary: string
  before: DiffValue
  after: DiffValue
  // A UI edit waiting for the auto-commit. Agent proposals wait for the user.
  fromUi: boolean
  // Taken by a commit that runs now.
  committing: boolean
}

// Changes oldest first. The cell shows the newest one.
interface QueuedCell {
  value: CellValue | undefined
  changes: QueuedChange[]
}

interface QueuedRow {
  cells: Map<string, QueuedCell>
  // Empty unless an entry moves the item to the trash.
  trash: QueuedChange[]
}

interface QueuedOverlay {
  rows: Map<string, QueuedRow>
  // Columns no vault item has yet, only a queued change (a rename to a new name, a template adding a field).
  extraColumns: GridColumn[]
}

// A grid row as the grid shows it: the vault values plus what is queued for it.
export type ShownRow = GridRow & { queued?: QueuedRow }

export const EMPTY_OVERLAY: QueuedOverlay = { rows: new Map(), extraColumns: [] }

function cellValue(value: DiffValue): CellValue | undefined {
  if (value === null) {
    return undefined
  }
  return isMasked(value) ? { masked: true } : value
}

function extraColumn(id: string, changes: QueuedChange[]): GridColumn {
  return {
    id,
    label: id,
    group: columnGroup(id),
    secret: changes.some((change) => isMasked(change.after) || isMasked(change.before)),
    multi: MULTI_COLUMNS.has(id),
    readOnly: false,
  }
}

// Entries whose operation no longer fits the vault (diffError) have no rows and
// change nothing. Approved entries are being written and still show, until
// the grid reloaded with their values.
export function buildOverlay(entries: readonly QueueEntry[], columns: readonly GridColumn[]): QueuedOverlay {
  const known = new Set(columns.map((column) => column.id))
  const rows = new Map<string, QueuedRow>()
  const extra = new Map<string, QueuedChange[]>()
  const byId = new Map<number, QueueEntry>()
  for (const entry of entries) {
    const shows = entry.status === 'pending' || entry.status === 'approved'
    if (shows && !entry.diffError && entry.operation.kind !== CREATE_ITEM) {
      byId.set(entry.id, entry)
    }
  }
  const shown = [...byId.values()].sort((a, b) => a.id - b.id)
  for (const entry of shown) {
    for (const { itemId, column, before, after } of entry.diff) {
      if (itemId === null || column === RESTORE_COLUMN) {
        continue
      }
      let row = rows.get(itemId)
      if (!row) {
        row = { cells: new Map(), trash: [] }
        rows.set(itemId, row)
      }
      const change: QueuedChange = {
        entryId: entry.id,
        summary: entry.summary,
        before,
        after,
        fromUi: entry.source === UI_SOURCE,
        committing: entry.status === 'approved',
      }
      if (column === TRASH_COLUMN) {
        if (after === true) {
          row.trash.push(change)
        }
        continue
      }
      const cell = row.cells.get(column) ?? { value: undefined, changes: [] }
      cell.changes.push(change)
      cell.value = cellValue(after)
      row.cells.set(column, cell)
      if (!known.has(column)) {
        extra.set(column, [...(extra.get(column) ?? []), change])
      }
    }
  }
  const extraColumns = [...extra].map(([id, changes]) => extraColumn(id, changes))
  return { rows, extraColumns }
}

function fieldKey(columnId: string): [string, string] {
  const name = columnId.slice(FIELDS_PREFIX.length)
  return [name.toLowerCase(), name]
}

function fieldBefore(a: string, b: string): boolean {
  const [foldA, nameA] = fieldKey(a)
  const [foldB, nameB] = fieldKey(b)
  return foldA < foldB || (foldA === foldB && nameA < nameB)
}

// The vault's columns with the extra ones in place: a field among the fields
// in name order (the order the backend sorts them in), a path at the end.
export function withExtraColumns(columns: readonly GridColumn[], extra: readonly GridColumn[]): GridColumn[] {
  const all = [...columns]
  for (const column of extra) {
    const at =
      column.group === 'fields' ? all.findIndex((other) => other.group === 'fields' && fieldBefore(column.id, other.id)) : -1
    if (at < 0) {
      all.push(column)
    } else {
      all.splice(at, 0, column)
    }
  }
  return all
}

export function shownRows(rows: readonly GridRow[], overlay: QueuedOverlay): ShownRow[] {
  if (overlay.rows.size === 0) {
    return [...rows]
  }
  return rows.map((row) => {
    const queued = overlay.rows.get(row.id)
    return queued ? { ...row, queued } : row
  })
}

export function queuedCell(row: ShownRow | undefined, columnId: string): QueuedCell | undefined {
  return row?.queued?.cells.get(columnId)
}

export function isQueuedForTrash(row: ShownRow | undefined): boolean {
  return (row?.queued?.trash.length ?? 0) > 0
}

export function shownValue(row: ShownRow | undefined, columnId: string): CellValue | undefined {
  const cell = queuedCell(row, columnId)
  return cell ? cell.value : row?.cells[columnId]
}

// The cells a row shows, for telling which columns are empty.
export function shownCells(row: ShownRow): GridRow['cells'] {
  if (!row.queued || row.queued.cells.size === 0) {
    return row.cells
  }
  const cells = { ...row.cells }
  for (const [columnId, cell] of row.queued.cells) {
    cells[columnId] = cell.value
  }
  return cells
}

// A cell with an agent's proposal waits for the user. The user's own unsaved edits can be edited again.
export function queuedBlocker(row: ShownRow, columnId: string): string | null {
  const cell = queuedCell(row, columnId)
  return cell && cell.changes.some((change) => !change.fromUi) ? QUEUED_BLOCKER : null
}

function diffText(value: DiffValue): string {
  return value === null ? '' : formatValue(cellValue(value))
}

function summaryOf(change: QueuedChange): string {
  return change.summary.replace(/\.+$/, '')
}

// The user's own edits follow each other (a commit checks a later one against bwtidy's write before it), proposals do not.
function conflictNote(all: QueuedChange[]): string[] {
  const changes = all.filter((change) => !change.fromUi)
  if (changes.length < 2) {
    return []
  }
  const later = changes.slice(1).map((change) => `#${change.entryId}`)
  const verb = later.length === 1 ? 'fails' : 'fail'
  return [`Approved in order, ${later.join(', ')} ${verb} with a conflict.`]
}

function heading(change: QueuedChange): string {
  if (!change.fromUi) {
    return 'Queued'
  }
  return change.committing ? 'Saving' : 'Unsaved'
}

// When the unsaved ones are written, said once after the changes.
function commitLines(changes: QueuedChange[], commitNote: string | undefined): string[] {
  return commitNote && changes.some((change) => change.fromUi && !change.committing) ? [commitNote] : []
}

export function cellTooltip(cell: QueuedCell, commitNote?: string): string {
  const lines = cell.changes.map(
    (change) => `${heading(change)} in #${change.entryId}: ${summaryOf(change)}. Was: ${diffText(change.before)}`,
  )
  return [...lines, ...conflictNote(cell.changes), ...commitLines(cell.changes, commitNote)].join('\n')
}

function trashTooltip(changes: QueuedChange[], commitNote?: string): string {
  const lines = changes.map((change) => `${heading(change)} for the trash in #${change.entryId}: ${summaryOf(change)}.`)
  return [...lines, ...conflictNote(changes), ...commitLines(changes, commitNote)].join('\n')
}

// The tooltip a cell gets from the queue, or null when nothing is queued for it.
// commitNote says when the auto-commit writes the unsaved edits.
export function queuedTooltip(row: ShownRow | undefined, columnId: string, commitNote?: string): string | null {
  const cell = queuedCell(row, columnId)
  if (cell) {
    return cellTooltip(cell, commitNote)
  }
  return row?.queued && row.queued.trash.length > 0 ? trashTooltip(row.queued.trash, commitNote) : null
}
