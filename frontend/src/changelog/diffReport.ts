import { operationLabel } from '../shared/operationLabels.ts'
import type { ChangeLogEntry } from '../api/changeLog.ts'
import { folderLabel, shownItemIds, sourceLabel } from './rows.ts'

const WHOLE_ITEM = '(whole item)'

interface DiffLine {
  time: string
  source: string
  operation: string
  item: string
  field: string
}

export const DIFF_HEADER = ['Time', 'Source', 'Operation', 'Item', 'Field']

// One line per item and field of each change row, oldest first. A folder row has its folder as the
// only item. A row without fields is one line per item with (whole item). Never a value: the log holds none.
export function diffLines(
  entries: ChangeLogEntry[],
  names: ReadonlyMap<string, string>,
  folderNames: ReadonlyMap<string, string>,
  formatTime: (at: string) => string,
): DiffLine[] {
  const oldestFirst = [...entries].sort((a, b) => a.at.localeCompare(b.at) || a.id - b.id)
  return oldestFirst.flatMap((entry) => {
    const fields = entry.columns.length > 0 ? entry.columns : [WHOLE_ITEM]
    const operation = operationLabel(entry.operationKind)?.label ?? entry.operationKind
    const items = entry.folderId
      ? [folderLabel(entry.folderId, folderNames)]
      : shownItemIds(entry).map((id) => names.get(id) ?? id)
    return items.flatMap((item) =>
      fields.map((field) => ({
        time: formatTime(entry.at),
        source: sourceLabel(entry.source),
        operation,
        item,
        field,
      })),
    )
  })
}

// A tab or a line break in a name would break the table apart.
function cell(text: string): string {
  return text.replace(/[\t\r\n]+/g, ' ')
}

// The table as tab separated text with a header line.
export function diffText(lines: DiffLine[]): string {
  return [DIFF_HEADER, ...lines.map((line) => [line.time, line.source, line.operation, line.item, line.field])]
    .map((columns) => columns.map(cell).join('\t'))
    .join('\n')
}

export function diffSummary(operations: number, lines: number): string {
  return `${operations} ${operations === 1 ? 'operation' : 'operations'}, ${lines} ${lines === 1 ? 'line' : 'lines'}`
}
