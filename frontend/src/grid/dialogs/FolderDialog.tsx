import {
  TextField,
} from '@mui/material'
import { useState } from 'react'
import { rowName } from '../labels.ts'
import { moveToFolder } from '../../api/operations.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import { NO_PASSWORD_MANAGER } from '../../shared/noPasswordManager.ts'
import { FolderPicker, folderNames, NEW_FOLDER } from './FolderPicker.tsx'
import type { DialogProps } from './types.ts'

export function FolderDialog({ row, rows, onClose, onApply }: DialogProps) {
  const current = typeof row.cells.folder === 'string' ? row.cells.folder : ''
  const [choice, setChoice] = useState(current)
  const [newName, setNewName] = useState('')
  const target = choice === NEW_FOLDER ? newName.trim() : choice
  return (
    <FormDialog
      title={`Folder of ${rowName(row)}`}
      canSubmit={(choice !== NEW_FOLDER || target !== '') && target !== current}
      onClose={onClose}
      onSubmit={() => onApply([moveToFolder(row, target)])}
    >
      <FolderPicker folders={folderNames(rows)} value={choice} onChange={setChoice} />
      {choice === NEW_FOLDER && (
        <TextField
          autoFocus
          label="New folder name"
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
        />
      )}
    </FormDialog>
  )
}
