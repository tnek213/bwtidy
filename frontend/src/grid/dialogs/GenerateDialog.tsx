import { DialogContentText } from '@mui/material'
import type { GridColumn } from '../../api/grid.ts'
import { generateLeaf } from '../../api/operations.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import type { DialogProps } from './types.ts'
import { rowName } from '../labels.ts'

const GENERATE_QUESTION = "Replace the value with a generated one? The old value stays in the item's password history."

export function GenerateDialog({ row, column, onClose, onApply }: DialogProps & { column: GridColumn }) {
  return (
    <FormDialog
      title={`Generate new value for ${column.label} of ${rowName(row)}`}
      submitLabel="Generate"
      canSubmit
      onClose={onClose}
      onSubmit={() => onApply([generateLeaf(row, column.id)])}
    >
      <DialogContentText>{GENERATE_QUESTION}</DialogContentText>
    </FormDialog>
  )
}
