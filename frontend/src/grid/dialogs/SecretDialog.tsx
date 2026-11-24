import {
  Checkbox,
  DialogContentText,
  FormControlLabel,
  TextField,
} from '@mui/material'
import { useState } from 'react'
import { type FieldType, setLeaf } from '../../api/operations.ts'
import type { GridColumn } from '../../api/grid.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import { NO_PASSWORD_MANAGER } from '../../shared/noPasswordManager.ts'
import ProseAlert from '../../theme/ProseAlert.tsx'
import { isBooleanFieldOfItem, isTextFieldOfItem } from '../editing.ts'
import { FormatFeedback } from '../FormatEditor.tsx'
import { queuedCell, type ShownRow } from '../queued.ts'
import type { DialogProps } from './types.ts'
import { isMasked } from '../../shared/values.ts'
import { rowName } from '../labels.ts'

export const TEXT_FIELD_WARNING =
  'On this item this is a text field, so the value is stored as plain text and an agent can read it through MCP.'

// A queued edit of the field comes first at the commit: Add field queues a text field, a removal makes it new again.
function isTextFieldAtCommit(row: ShownRow, column: GridColumn): boolean {
  if (column.group !== 'fields' || isBooleanFieldOfItem(row, column.id)) {
    return false
  }
  const queued = queuedCell(row, column.id)
  if (queued) {
    return queued.value !== undefined && !isMasked(queued.value)
  }
  return isTextFieldOfItem(row, column)
}

export function SecretDialog({ row, column, onClose, onApply, datatypeOf }: DialogProps & { column: GridColumn }) {
  const [value, setValue] = useState('')
  const [shown, setShown] = useState(false)
  const textField = isTextFieldAtCommit(row, column)
  const isNew = !textField && row.cells[column.id] === undefined
  const multiline = column.id === 'sshKey.privateKey'
  function submit() {
    // A new custom field in a secret column is hidden like the others.
    const fieldType: FieldType | undefined = isNew && column.group === 'fields' ? 1 : undefined
    onApply([setLeaf(row, column.id, value, fieldType)])
  }
  return (
    <FormDialog
      title={`${column.label} of ${rowName(row)}`}
      canSubmit={value !== ''}
      onClose={onClose}
      onSubmit={submit}
    >
      {textField ? (
        <ProseAlert severity="warning">{TEXT_FIELD_WARNING}</ProseAlert>
      ) : (
        <DialogContentText>
          {isNew
            ? 'The value is stored as a secret.'
            : 'The new value replaces the current one, which is not shown here. Reveal it in the grid if you need it.'}
        </DialogContentText>
      )}
      <TextField
        autoFocus
        label="New value"
        type="text"
        multiline={multiline}
        minRows={multiline ? 4 : undefined}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        slotProps={{
          htmlInput: {
            ...NO_PASSWORD_MANAGER,
            spellCheck: false,
            autoCapitalize: 'off',
            autoCorrect: 'off',
            style: shown ? undefined : { WebkitTextSecurity: 'disc' },
          },
        }}
      />
      <FormatFeedback datatypeId={datatypeOf?.(row, column) ?? null} value={value} />
      <FormControlLabel
        control={<Checkbox checked={shown} onChange={(event) => setShown(event.target.checked)} />}
        label="Show what I type"
      />
    </FormDialog>
  )
}
