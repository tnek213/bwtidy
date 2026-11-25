import {
  Box,
  DialogContentText,
  Divider,
  FormControl,
  FormControlLabel,
  FormLabel,
  Radio,
  RadioGroup,
  Stack,
} from '@mui/material'
import { useState } from 'react'
import { rowName } from '../labels.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import type { MergeConflict } from '../normalize.ts'
import type { NormalizeDialogProps } from './types.ts'
import { ValuePreview } from './ValuePreview.tsx'

interface MergeConflictDialogProps extends NormalizeDialogProps {
  conflicts: MergeConflict[]
  onResolve: (resolutions: Record<string, string>) => void
}

export function MergeConflictDialog({ conflicts, rows, columns, onClose, onResolve }: MergeConflictDialogProps) {
  const [choices, setChoices] = useState<Record<string, string>>({})
  const byId = new Map(columns.map((column) => [column.id, column]))
  const rowsById = new Map(rows.map((row) => [row.id, row]))
  const complete = conflicts.every((conflict) => choices[conflict.itemId] !== undefined)
  return (
    <FormDialog
      title="Choose the values to keep"
      maxWidth="md"
      submitLabel="Merge"
      canSubmit={complete}
      onClose={onClose}
      onSubmit={() => onResolve(choices)}
    >
      <DialogContentText>
        These items have values in more than one of the merged columns. Pick the value each item keeps, the others
        are removed.
      </DialogContentText>
      {conflicts.map((conflict, index) => {
        const row = rowsById.get(conflict.itemId)
        const name = row ? rowName(row) : conflict.itemId
        return (
          <Box key={conflict.itemId}>
            {index > 0 && <Divider sx={{ mb: 2 }} />}
            <FormControl component="fieldset" fullWidth>
              <FormLabel component="legend">{name}</FormLabel>
              <RadioGroup
                aria-label={name}
                value={choices[conflict.itemId] ?? ''}
                onChange={(event) => setChoices({ ...choices, [conflict.itemId]: event.target.value })}
              >
                {conflict.columns.map((columnId) => (
                  <Stack key={columnId} direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                    <FormControlLabel value={columnId} control={<Radio />} label={columnId} sx={{ flexShrink: 0 }} />
                    {row && <ValuePreview row={row} column={byId.get(columnId)} />}
                  </Stack>
                ))}
              </RadioGroup>
            </FormControl>
          </Box>
        )
      })}
    </FormDialog>
  )
}
