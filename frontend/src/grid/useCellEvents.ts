import type {
  CellContextMenuEvent,
  CellDoubleClickedEvent,
  CellEditRequestEvent,
  CellKeyDownEvent,
  FullWidthCellKeyDownEvent,
} from 'ag-grid-community'
import type { AgGridReact } from 'ag-grid-react'
import type { RefObject } from 'react'
import type { CellMenuState } from './CellMenu.tsx'
import type { DialogState } from './dialogs/index.tsx'
import { editBlocker, editKind, isBooleanColumn, isInline, pointsToCellMenu, unavailableReason } from './editing.ts'
import { type GridColumn, NAME_COLUMN, TEMPLATE_COLUMN } from '../api/grid.ts'
import { type Operation, removeLeaf, setLeaf } from '../api/operations.ts'
import type { Feedback } from './writer.ts'
import { queuedCell, type ShownRow } from './queued.ts'
import { RARE_FIELDS_COLUMN } from './rareFields.ts'

interface CellEventInputs {
  columnsById: ReadonlyMap<string, GridColumn>
  openCellDialog: (dialog: DialogState) => void
  showMenu: (menu: CellMenuState) => void
  onFeedback: (feedback: Feedback) => void
  onApply: (operations: Operation[]) => void
}

function editedValue(column: GridColumn, value: unknown): string | boolean {
  if (isBooleanColumn(column)) {
    return value === true
  }
  return value === null || value === undefined ? '' : String(value)
}

// A key that types a character, which on an editable cell would start AG Grid's editor.
function isTyping(key: KeyboardEvent): boolean {
  return key.key.length === 1 && !key.ctrlKey && !key.metaKey && !key.altKey
}

// What a double click, a key, a right click or an inline edit on a grid cell does: start AG Grid's
// inline editor, open a cell dialog or the cell menu, or queue the edit. startEdit is also the cell
// menu's Edit value.
export function useCellEvents(
  gridRef: RefObject<AgGridReact<ShownRow> | null>,
  { columnsById, openCellDialog, showMenu, onFeedback, onApply }: CellEventInputs,
) {
  // Inline kinds start AG Grid's editor, the others open a dialog.
  function startEdit(column: GridColumn, row: ShownRow, rowIndex: number | null) {
    const blocker = editBlocker(column, row)
    if (blocker !== null) {
      // A grey cell explains itself on hover.
      if ((!column.readOnly || pointsToCellMenu(column)) && blocker !== unavailableReason(column, row)) {
        onFeedback({ severity: 'warning', message: blocker })
      }
      return
    }
    const kind = editKind(column)
    if (kind === null) {
      return
    }
    if (isInline(kind)) {
      if (rowIndex !== null) {
        gridRef.current?.api.startEditingCell({ rowIndex, colKey: column.id })
      }
      return
    }
    // The editor starts from an unsaved edit's value. A secret dialog never shows one, and tells a text field by the vault and the queued edits.
    const queued = queuedCell(row, column.id)
    const shown = queued && kind !== 'secret' ? { ...row, cells: { ...row.cells, [column.id]: queued.value } } : row
    openCellDialog({ kind, row: shown, column })
  }

  function cellOf(event: { data: ShownRow | undefined; column: { getColId(): string } }) {
    const column = columnsById.get(event.column.getColId())
    return column && event.data ? { column, row: event.data } : null
  }

  function handleDoubleClick(event: CellDoubleClickedEvent<ShownRow>) {
    const cell = cellOf(event)
    const kind = cell && editKind(cell.column)
    // AG Grid starts inline editors on a double click by itself.
    if (cell && !(isInline(kind) && editBlocker(cell.column, cell.row) === null)) {
      startEdit(cell.column, cell.row, event.rowIndex)
    }
  }

  function openMenu(event: CellContextMenuEvent<ShownRow> | CellKeyDownEvent<ShownRow>, position: CellMenuState['position']) {
    const rowIndex = event.rowIndex ?? 0
    // The template and Rare fields columns are GridPage's own, not coordinates, so cellOf does not know them.
    const columnId = event.column.getColId()
    if ((columnId === TEMPLATE_COLUMN || columnId === RARE_FIELDS_COLUMN) && event.data) {
      showMenu({ row: event.data, column: null, rowIndex, position })
      return
    }
    const cell = cellOf(event)
    if (cell) {
      showMenu({ ...cell, rowIndex, position })
    }
  }

  function handleContextMenu(event: CellContextMenuEvent<ShownRow>) {
    const mouse = event.event
    if (mouse instanceof MouseEvent) {
      openMenu(event, { top: mouse.clientY, left: mouse.clientX })
    }
  }

  function handleKeyDown(event: CellKeyDownEvent<ShownRow> | FullWidthCellKeyDownEvent<ShownRow>) {
    const key = event.event
    if (!('column' in event) || !(key instanceof KeyboardEvent) || event.api.getEditingCells().length > 0) {
      return
    }
    if (key.key === 'ContextMenu' || (key.key === 'F10' && key.shiftKey)) {
      key.preventDefault()
      const rect = (key.target as HTMLElement).getBoundingClientRect()
      openMenu(event, { top: rect.bottom, left: rect.left })
      return
    }
    const cell = cellOf(event)
    const kind = cell && editKind(cell.column)
    if (key.key === 'Enter' && cell && kind !== null && !isInline(kind)) {
      startEdit(cell.column, cell.row, event.rowIndex)
    } else if (cell && pointsToCellMenu(cell.column) && (key.key === 'Enter' || isTyping(key))) {
      startEdit(cell.column, cell.row, event.rowIndex)
    }
  }

  function handleEditRequest(event: CellEditRequestEvent<ShownRow>) {
    const column = columnsById.get(event.column.getColId())
    if (!column) {
      return
    }
    const row = event.data
    const value = editedValue(column, event.newValue)
    if (value === editedValue(column, event.oldValue)) {
      return
    }
    if (value === '') {
      if (column.id === NAME_COLUMN) {
        onFeedback({ severity: 'error', message: 'An item needs a name.' })
        return
      }
      onApply([removeLeaf(row, column.id)])
      return
    }
    onApply([setLeaf(row, column.id, value)])
  }

  return {
    startEdit,
    gridEvents: {
      onCellEditRequest: handleEditRequest,
      onCellDoubleClicked: handleDoubleClick,
      onCellKeyDown: handleKeyDown,
      onCellContextMenu: handleContextMenu,
    },
  }
}
