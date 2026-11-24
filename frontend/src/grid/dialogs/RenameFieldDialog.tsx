import {
  DialogContentText,
  TextField,
} from '@mui/material'
import { useState } from 'react'
import { fieldColumn, fieldName } from '../../shared/coordinates.ts'
import type { GridColumn } from '../../api/grid.ts'
import type { Operation } from '../../api/operations.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import { NO_PASSWORD_MANAGER } from '../../shared/noPasswordManager.ts'
import ProseAlert from '../../theme/ProseAlert.tsx'
import { itemsWithAny, renameField } from '../normalize.ts'
import type { NormalizeDialogProps } from './types.ts'

interface RenameFieldDialogProps extends NormalizeDialogProps {
  column: GridColumn
  onApply: (operations: Operation[]) => void
  onMerge: (sources: string[], target: string) => void
}

export function RenameFieldDialog({ column, rows, columns, onClose, onApply, onMerge }: RenameFieldDialogProps) {
  const from = fieldName(column.id)
  const [to, setTo] = useState(from)
  const target = fieldColumn(to)
  // Renaming onto a name some item already has is a merge, with a choice where both have a value.
  const existing = to !== from && columns.some((candidate) => candidate.id === target)
  const affected = rows.filter((row) => itemsWithAny([row], [column.id]).length > 0).length
  function submit() {
    if (existing) {
      onMerge([column.id], target)
    } else {
      onApply([renameField(rows, columns, from, to)])
    }
  }
  return (
    <FormDialog
      title={`Rename field ${from}`}
      submitLabel={existing ? 'Merge' : 'Rename'}
      canSubmit={to !== '' && to !== from}
      onClose={onClose}
      onSubmit={submit}
    >
      <DialogContentText>
        The field is renamed in every item that has it ({affected} {affected === 1 ? 'item' : 'items'}).
      </DialogContentText>
      <TextField
        autoFocus
        label="New name"
        value={to}
        onChange={(event) => setTo(event.target.value)}
        helperText={to !== to.trim() ? 'The name starts or ends with a space.' : undefined}
        slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
      />
      {existing && (
        <ProseAlert severity="info">
          A field named {to} exists already, so this merges {column.id} into {target}. Items with values in both are
          listed for a choice.
        </ProseAlert>
      )}
    </FormDialog>
  )
}
