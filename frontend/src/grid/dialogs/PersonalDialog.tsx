import {
  FormControl,
  InputLabel,
  MenuItem,
  Select,
} from '@mui/material'
import { useId } from 'react'
import { rowName } from '../labels.ts'
import { moveToPersonal } from '../../api/operations.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import { NO_PASSWORD_MANAGER } from '../../shared/noPasswordManager.ts'
import ProseAlert from '../../theme/ProseAlert.tsx'
import type { DialogProps } from './types.ts'

const PERSONAL_VAULT = 'personal'

const MOVE_TO_PERSONAL_WARNING =
  'bwtidy makes a personal copy and moves this item to the trash. Attachments are copied, passkeys and password history are lost. Everyone who shares its collections loses it.'

// Moving between organizations is out of scope, so the only way out is the personal vault.
export function PersonalDialog({ row, onClose, onApply }: DialogProps) {
  const labelId = useId()
  return (
    <FormDialog
      title={`Move ${rowName(row)} out of ${String(row.cells.organization)}`}
      submitLabel="Move"
      canSubmit
      onClose={onClose}
      onSubmit={() => onApply([moveToPersonal(row)])}
    >
      <FormControl fullWidth>
        <InputLabel id={labelId}>Move to</InputLabel>
        <Select labelId={labelId} label="Move to" value={PERSONAL_VAULT} inputProps={NO_PASSWORD_MANAGER}>
          <MenuItem value={PERSONAL_VAULT}>Personal vault</MenuItem>
        </Select>
      </FormControl>
      <ProseAlert severity="warning">{MOVE_TO_PERSONAL_WARNING}</ProseAlert>
    </FormDialog>
  )
}
