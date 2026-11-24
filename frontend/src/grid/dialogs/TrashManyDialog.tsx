import {
  DialogContentText,
  List,
  ListItem,
  ListItemText,
} from '@mui/material'
import type { GridRow } from '../../api/grid.ts'
import { type Operation, trashItem } from '../../api/operations.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import { rowName } from '../labels.ts'

// One confirmation for the ticked rows of the grid, each item becomes its own queue entry.
export function TrashManyDialog({
  rows,
  onClose,
  onApply,
}: {
  rows: GridRow[]
  onClose: () => void
  onApply: (operations: Operation[]) => void
}) {
  const single = rows.length === 1
  return (
    <FormDialog
      title={`Move ${rows.length} ${single ? 'item' : 'items'} to the trash?`}
      submitLabel="Move to trash"
      canSubmit={rows.length > 0}
      onClose={onClose}
      onSubmit={() => onApply(rows.map(trashItem))}
    >
      <List dense disablePadding aria-label="Items to move to the trash" sx={{ maxHeight: 240, overflow: 'auto' }}>
        {rows.map((row) => (
          <ListItem key={row.id} disableGutters sx={{ py: 0 }}>
            <ListItemText primary={rowName(row)} />
          </ListItem>
        ))}
      </List>
      <DialogContentText>
        {single ? 'It can' : 'They can'} be restored from the trash in Bitwarden or from the Change log.
      </DialogContentText>
    </FormDialog>
  )
}
