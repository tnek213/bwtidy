import type { AttachmentIds, Backup, ChangeLogEntry, RestoredBy } from '../api/changeLog.ts'

// What the Items column adds for an attachment rename: the attachment ids before and after.
export function attachmentText(ids: AttachmentIds): string {
  return ids.after ? `Attachment ${ids.before} replaced by ${ids.after}` : `Attachment ${ids.before}`
}

// The tooltip of a row a later restore undid: when, the row's fields and the items by name.
export function restoredText(
  entry: ChangeLogEntry,
  restoredBy: RestoredBy,
  names: ReadonlyMap<string, string>,
  formatTime: (at: string) => string,
): string {
  const items = restoredBy.itemIds.map((id) => names.get(id) ?? id).join(', ')
  const what = entry.columns.length > 0 ? `${entry.columns.join(', ')} on ${items}` : items
  return `Restored ${formatTime(restoredBy.at)}: ${what}`
}

const SHOWN_COLUMNS = 3

// The coordinates a row shows: the first three, then how many more the tooltip lists.
export function columnsText(columns: string[]): string {
  const shown = columns.slice(0, SHOWN_COLUMNS).join(', ')
  const more = columns.length - SHOWN_COLUMNS
  return more > 0 ? `${shown} +${more} more` : shown
}

export function backupNames(backups: Backup[] | undefined): ReadonlySet<string> | undefined {
  return backups && new Set(backups.map((backup) => backup.name))
}

const UNITS = ['B', 'kB', 'MB', 'GB']

export function formatSize(bytes: number): string {
  let value = bytes
  let unit = 0
  while (value >= 1000 && unit < UNITS.length - 1) {
    value /= 1000
    unit += 1
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1
  return `${value.toFixed(digits)} ${UNITS[unit]}`
}

export type LogRow = { kind: 'change'; at: string; entry: ChangeLogEntry } | { kind: 'backup'; at: string; backup: Backup }

// The change log rows and the backups as one list, newest first. A backup is taken before the
// write it belongs to, so it sorts right under that write's row.
export function mergeRows(entries: ChangeLogEntry[], backups: Backup[] | undefined): LogRow[] {
  const rows: LogRow[] = entries.map((entry) => ({ kind: 'change', at: entry.at, entry }))
  for (const backup of backups ?? []) {
    rows.push({ kind: 'backup', at: backup.time, backup })
  }
  return rows.sort((a, b) => {
    const difference = Date.parse(b.at) - Date.parse(a.at)
    return Number.isNaN(difference) ? b.at.localeCompare(a.at) : difference
  })
}

// For each row, whether it is a change row that names the same backup file as the change row right above it.
// The page fades the Backup and Action cells of such rows, so a batch reads as one group.
export function sharesBackupWithAbove(rows: LogRow[]): boolean[] {
  return rows.map((row, index) => {
    const above = rows[index - 1]
    return (
      row.kind === 'change' &&
      above?.kind === 'change' &&
      row.entry.backup !== null &&
      row.entry.backup === above.entry.backup
    )
  })
}

// Display only: the API and SQLite keep the raw values. A value without an entry is shown as it is.
export const RESULT_LABELS: Record<string, string> = { ok: 'OK', failed: 'Failed' }

export const SOURCE_LABELS: Record<string, string> = { ui: 'UI', mcp: 'MCP', mixed: 'Mixed' }

export function resultLabel(result: string): string {
  return Object.hasOwn(RESULT_LABELS, result) ? RESULT_LABELS[result] : result
}

export function sourceLabel(source: string): string {
  return Object.hasOwn(SOURCE_LABELS, source) ? SOURCE_LABELS[source] : source
}

// A row that touched no existing item (a createItem) names the items it created instead.
export function shownItemIds(entry: ChangeLogEntry): string[] {
  return entry.itemIds.length > 0 ? entry.itemIds : entry.createdItemIds
}

// The Items column text of a folder row, and the Item of its diff report lines: the folder's name,
// else its id when the vault lacks it.
export function folderLabel(folderId: string, folderNames: ReadonlyMap<string, string>): string {
  return `Folder ${folderNames.get(folderId) ?? folderId}`
}

// A row can be restored when it was written, its backup still exists and it names something to
// put back: items, or the folder of a folder row. The tool note's rows (template saves) cannot:
// the templates are restored by saving them on the Types page.
export function canRestore(entry: ChangeLogEntry, backups: ReadonlySet<string> | undefined, toolNoteId?: string | null) {
  return (
    entry.result === 'ok' &&
    entry.backup !== null &&
    backups?.has(entry.backup) === true &&
    (shownItemIds(entry).length > 0 || (entry.folderId ?? null) !== null) &&
    !(toolNoteId && entry.itemIds.includes(toolNoteId))
  )
}
