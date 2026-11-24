import { DialogContentText } from '@mui/material'
import { rowName } from '../labels.ts'
import { trashItem } from '../../api/operations.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import type { DialogProps } from './types.ts'

export function TrashDialog({ row, onClose, onApply }: DialogProps) {
  return (
    <FormDialog
      title="Move to trash"
      submitLabel="Move to trash"
      canSubmit
      onClose={onClose}
      onSubmit={() => onApply([trashItem(row)])}
    >
      <DialogContentText>
        Move “{rowName(row)}” to the trash? It can be restored from the trash in Bitwarden.
      </DialogContentText>
    </FormDialog>
  )
}
