import {
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  TextField,
} from '@mui/material'
import { useId, useState } from 'react'
import { fieldColumn } from '../../shared/coordinates.ts'
import type { GridRow } from '../../api/grid.ts'
import { type FieldType, setLeaf } from '../../api/operations.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import { NO_PASSWORD_MANAGER } from '../../shared/noPasswordManager.ts'
import { isBooleanFieldOfItem, isLinkedFieldOfItem } from '../editing.ts'
import type { DialogProps } from './types.ts'
import { rowName } from '../labels.ts'

// An existing field keeps its type, and an empty one has no cell (and maybe no column) to edit it in.
function addFieldTarget(
  row: GridRow,
  name: string,
  column: string,
): { refusal: string | null; fixedType: FieldType | null; note: string | undefined } {
  if (name === '') {
    return { refusal: null, fixedType: null, note: undefined }
  }
  if (row.cells[column] !== undefined) {
    return { refusal: 'The item already has a field with this name. Edit its cell instead.', fixedType: null, note: undefined }
  }
  if (isBooleanFieldOfItem(row, column)) {
    return { refusal: 'The item has a boolean field of this name, it takes true or false and no typed value.', fixedType: null, note: undefined }
  }
  if (isLinkedFieldOfItem(row, column)) {
    return { refusal: 'The item has a linked field of this name, it has no value of its own.', fixedType: null, note: undefined }
  }
  if ((row.emptyTextFields ?? []).includes(column)) {
    return { refusal: null, fixedType: 0, note: 'The item has an empty text field of this name, the value goes into it as plain text.' }
  }
  if ((row.emptySecrets ?? []).includes(column)) {
    return { refusal: null, fixedType: 1, note: 'The item has an empty hidden field of this name, the value goes into it as a secret.' }
  }
  return { refusal: null, fixedType: null, note: undefined }
}

export function AddFieldDialog({ row, onClose, onApply }: DialogProps) {
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [chosenType, setChosenType] = useState<FieldType>(0)
  const typeLabelId = useId()
  const column = fieldColumn(name)
  const { refusal, fixedType, note } = addFieldTarget(row, name, column)
  const fieldType = fixedType ?? chosenType
  return (
    <FormDialog
      title={`Add a field to ${rowName(row)}`}
      submitLabel="Add"
      canSubmit={name !== '' && refusal === null}
      onClose={onClose}
      onSubmit={() => onApply([setLeaf(row, column, value, fieldType)])}
    >
      <TextField
        autoFocus
        label="Name"
        value={name}
        onChange={(event) => setName(event.target.value)}
        error={refusal !== null}
        helperText={refusal ?? note}
        slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
      />
      <FormControl fullWidth disabled={fixedType !== null}>
        <InputLabel id={typeLabelId}>Type</InputLabel>
        <Select
          labelId={typeLabelId}
          label="Type"
          value={fieldType}
          inputProps={NO_PASSWORD_MANAGER}
          onChange={(event) => setChosenType(Number(event.target.value) as FieldType)}
        >
          <MenuItem value={0}>Text</MenuItem>
          <MenuItem value={1}>Hidden</MenuItem>
        </Select>
      </FormControl>
      <TextField
        label="Value"
        type="text"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        slotProps={{
          htmlInput: {
            ...NO_PASSWORD_MANAGER,
            spellCheck: false,
            autoCapitalize: 'off',
            autoCorrect: 'off',
            style: fieldType === 1 ? { WebkitTextSecurity: 'disc' } : undefined,
          },
        }}
      />
    </FormDialog>
  )
}
