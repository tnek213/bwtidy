import {
  Checkbox,
  FormControlLabel,
} from '@mui/material'
import { useState } from 'react'
import type { GridColumn } from '../../api/grid.ts'
import { setLeaf } from '../../api/operations.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import ProseAlert from '../../theme/ProseAlert.tsx'
import type { DialogProps } from './types.ts'
import { rowName } from '../labels.ts'

export const BOOLEAN_FIELD_NOTE =
  'On this item this is a boolean field, so it holds true or false as plain text and an agent can read it through MCP.'

// A boolean field in a secret column takes true or false, never a typed value.
export function BooleanFieldDialog({ row, column, onClose, onApply }: DialogProps & { column: GridColumn }) {
  const current = row.cells[column.id]
  const was = current === true || current === 'true'
  const [checked, setChecked] = useState(was)
  return (
    <FormDialog
      title={`${column.label} of ${rowName(row)}`}
      canSubmit={current === undefined || checked !== was}
      onClose={onClose}
      onSubmit={() => onApply([setLeaf(row, column.id, checked)])}
    >
      <ProseAlert severity="info">{BOOLEAN_FIELD_NOTE}</ProseAlert>
      <FormControlLabel
        control={<Checkbox autoFocus checked={checked} onChange={(event) => setChecked(event.target.checked)} />}
        label="True"
      />
    </FormDialog>
  )
}
