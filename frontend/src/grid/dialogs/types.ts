import type { GridColumn, GridRow, VaultOrganization } from '../../api/grid.ts'
import type { Operation } from '../../api/operations.ts'

export interface DialogProps {
  row: GridRow
  rows: GridRow[]
  organizations?: readonly VaultOrganization[]
  onClose: () => void
  onApply: (operations: Operation[]) => void
  // The datatype of a cell, for the live format text under its input.
  datatypeOf?: (row: GridRow, column: GridColumn) => string | null
}

export interface NormalizeDialogProps {
  rows: GridRow[]
  columns: GridColumn[]
  onClose: () => void
}
