import { Divider, ListSubheader, Menu, MenuItem } from '@mui/material'
import type { ReactElement } from 'react'
import { canGenerate, editBlocker, removeOperation, TOOL_NOTE_BLOCKER } from './editing.ts'
import { ATTACHMENTS_COLUMN, type GridColumn, type GridRow, isToolNote } from '../api/grid.ts'
import type { Operation } from '../api/operations.ts'
import { type Revealer, revealerText } from './revealers.ts'
import type { ToFillAction } from './toFill.ts'

export interface CellMenuState {
  row: GridRow
  // Null on a column that holds no value of the item to edit (the template and Rare fields columns): the menu has row actions only.
  column: GridColumn | null
  // The grid column of the cell, also where column is null.
  columnId: string
  rowIndex: number
  position: { top: number; left: number }
}

interface CellMenuProps {
  state: CellMenuState | null
  onClose: () => void
  onEdit: (column: GridColumn, row: GridRow, rowIndex: number) => void
  onApply: (operations: Operation[]) => void
  onAddField: (row: GridRow) => void
  onTrash: (row: GridRow) => void
  onConvert: (row: GridRow) => void
  canConvert: (row: GridRow) => boolean
  onMergeInto: (row: GridRow) => void
  onGenerate: (column: GridColumn, row: GridRow) => void
  onGeneratorSettings: (row: GridRow) => void
  onRenameAttachments: (row: GridRow) => void
  toFillAction: (column: GridColumn, row: GridRow) => ToFillAction | null
  onToFill: (row: GridRow, column: GridColumn, mark: boolean) => void
  // The eyes the cell shows.
  revealers: (row: GridRow, columnId: string) => Revealer[]
}

function cellEntries(state: CellMenuState, run: (action: () => void) => void, props: CellMenuProps): ReactElement[] {
  const { row, column, rowIndex } = state
  const reveal = props.revealers(row, state.columnId).map((revealer) => (
    <MenuItem key={`reveal:${revealer.label}`} onClick={() => run(revealer.toggle)}>
      {revealerText(revealer)}
    </MenuItem>
  ))
  if (column === null) {
    return reveal
  }
  const entries: ReactElement[] = [...reveal]
  if (editBlocker(column, row) === null) {
    entries.push(
      <MenuItem key="edit" onClick={() => run(() => props.onEdit(column, row, rowIndex))}>
        Edit value
      </MenuItem>,
    )
  }
  const remove = removeOperation(column, row)
  if (remove) {
    entries.push(
      <MenuItem key="remove" onClick={() => run(() => props.onApply([remove]))}>
        Remove value
      </MenuItem>,
    )
  }
  const toFill = props.toFillAction(column, row)
  if (toFill !== null) {
    const mark = toFill === 'mark'
    entries.push(
      <MenuItem key="toFill" onClick={() => run(() => props.onToFill(row, column, mark))}>
        {mark ? 'Mark to fill in' : 'Unmark'}
      </MenuItem>,
    )
  }
  if (canGenerate(column, row)) {
    entries.push(
      <MenuItem key="generate" onClick={() => run(() => props.onGenerate(column, row))}>
        Generate new value
      </MenuItem>,
      <MenuItem key="generatorSettings" onClick={() => run(() => props.onGeneratorSettings(row))}>
        Generator settings for this item
      </MenuItem>,
    )
  }
  if (column.id === ATTACHMENTS_COLUMN && !isToolNote(row) && (row.attachments ?? []).length > 0) {
    entries.push(
      <MenuItem key="renameAttachments" onClick={() => run(() => props.onRenameAttachments(row))}>
        Rename attachments
      </MenuItem>,
    )
  }
  return entries
}

function rowEntries(state: CellMenuState, run: (action: () => void) => void, props: CellMenuProps): ReactElement[] {
  const { row } = state
  // Every action on bwtidy's own note is refused by the backend, so none is offered.
  const disabled = isToolNote(row)
  const entries = [
    <MenuItem key="addField" disabled={disabled} onClick={() => run(() => props.onAddField(row))}>
      Add field
    </MenuItem>,
  ]
  if (props.canConvert(row)) {
    entries.push(
      <MenuItem key="convert" disabled={disabled} onClick={() => run(() => props.onConvert(row))}>
        Convert to template…
      </MenuItem>,
    )
  }
  entries.push(
    <MenuItem key="merge" disabled={disabled} onClick={() => run(() => props.onMergeInto(row))}>
      Merge into item…
    </MenuItem>,
    <MenuItem key="trash" disabled={disabled} onClick={() => run(() => props.onTrash(row))}>
      Move to trash
    </MenuItem>,
  )
  return entries
}

// AG Grid Community has no context menu of its own.
export default function CellMenu(props: CellMenuProps) {
  const { state, onClose } = props
  function run(action: () => void) {
    onClose()
    action()
  }
  const cell = state ? cellEntries(state, run, props) : []
  const row = state ? rowEntries(state, run, props) : []
  return (
    <Menu
      open={state !== null}
      onClose={onClose}
      anchorReference="anchorPosition"
      anchorPosition={state?.position}
    >
      {state && isToolNote(state.row) && <MenuItem disabled>{TOOL_NOTE_BLOCKER}</MenuItem>}
      {cell.length > 0 && <ListSubheader role="presentation">Cell</ListSubheader>}
      {cell}
      {cell.length > 0 && row.length > 0 && <Divider />}
      {row.length > 0 && <ListSubheader role="presentation">Row</ListSubheader>}
      {row}
    </Menu>
  )
}
