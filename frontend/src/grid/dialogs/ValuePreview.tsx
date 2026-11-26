import {
  Box,
  IconButton,
  Tooltip,
  Typography,
} from '@mui/material'
import type { GridColumn, GridRow } from '../../api/grid.ts'
import { EyeIcon } from '../../shared/EyeIcon.tsx'
import { useRevealedSecret } from '../reveal.ts'
import { formatValue } from '../../shared/values.ts'
import Masked from '../../shared/Masked.tsx'

function RevealValue({ itemId, columnId }: { itemId: string; columnId: string }) {
  const { value: revealed, failed, toggle } = useRevealedSecret(itemId, columnId)

  const shown = revealed !== null
  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, minWidth: 0 }}>
      <Box
        component="span"
        data-testid="secret-value"
        title={failed ? 'Could not reveal the value' : undefined}
        sx={{ fontFamily: shown ? 'monospace' : undefined, color: failed ? 'error.main' : undefined, wordBreak: 'break-all' }}
      >
        {shown ? revealed : <Masked />}
      </Box>
      <Tooltip title={shown ? 'Hide' : 'Reveal'}>
        <IconButton size="small" aria-label={shown ? `Hide ${columnId}` : `Reveal ${columnId}`} onClick={toggle}>
          <EyeIcon off={shown} />
        </IconButton>
      </Tooltip>
    </Box>
  )
}

export function ValuePreview({ row, column }: { row: GridRow; column: GridColumn | undefined }) {
  if (!column) {
    return null
  }
  const value = row.cells[column.id]
  if (column.secret && value !== undefined) {
    return <RevealValue itemId={row.id} columnId={column.id} />
  }
  return (
    <Typography component="span" variant="body2" sx={{ wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>
      {formatValue(value)}
    </Typography>
  )
}
