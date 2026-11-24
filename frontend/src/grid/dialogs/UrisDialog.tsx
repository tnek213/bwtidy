import {
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
} from '@mui/material'
import { useState } from 'react'
import type { GridColumn } from '../../api/grid.ts'
import { removeLeaf, setLeaf } from '../../api/operations.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import { NO_PASSWORD_MANAGER } from '../../shared/noPasswordManager.ts'
import { FormatFeedback } from '../FormatEditor.tsx'
import type { DialogProps } from './types.ts'
import { rowName } from '../labels.ts'

export function UrisDialog({ row, column, onClose, onApply, datatypeOf }: DialogProps & { column: GridColumn }) {
  const current = row.cells[column.id]
  const [uris, setUris] = useState<string[]>(() => (Array.isArray(current) && current.length > 0 ? current : ['']))
  const cleaned = uris.map((uri) => uri.trim()).filter(Boolean)
  const unchanged = Array.isArray(current) && cleaned.join('\n') === current.join('\n')
  const datatypeId = datatypeOf?.(row, column) ?? null
  function submit() {
    onApply([cleaned.length > 0 ? setLeaf(row, column.id, cleaned) : removeLeaf(row, column.id)])
  }
  return (
    <FormDialog
      title={`${column.label} of ${rowName(row)}`}
      canSubmit={!unchanged && !(current === undefined && cleaned.length === 0)}
      onClose={onClose}
      onSubmit={submit}
    >
      {uris.map((uri, index) => (
        <Stack key={index} direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <TextField
            autoFocus={index === 0}
            fullWidth
            size="small"
            label={`URI ${index + 1}`}
            value={uri}
            onChange={(event) => setUris(uris.map((old, at) => (at === index ? event.target.value : old)))}
            helperText={datatypeId === null ? undefined : <FormatFeedback datatypeId={datatypeId} value={uri.trim()} />}
            slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
          />
          <IconButton aria-label={`Remove URI ${index + 1}`} onClick={() => setUris(uris.filter((_, at) => at !== index))}>
            ✕
          </IconButton>
        </Stack>
      ))}
      <Box>
        <Button onClick={() => setUris([...uris, ''])}>Add URI</Button>
      </Box>
    </FormDialog>
  )
}
