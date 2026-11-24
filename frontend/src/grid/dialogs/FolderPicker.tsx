import {
  FormControl,
  InputLabel,
  MenuItem,
  Select,
} from '@mui/material'
import { useId } from 'react'
import type { GridRow } from '../../api/grid.ts'
import { NO_PASSWORD_MANAGER } from '../../shared/noPasswordManager.ts'

export const NEW_FOLDER = '\u0000new'

// The grid API has no folder list, so the choices are the names the loaded items use.
export function folderNames(rows: GridRow[]): string[] {
  return [...new Set(rows.map(({ cells }) => cells.folder).filter((name): name is string => typeof name === 'string'))].sort(
    (a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }),
  )
}

// A folder in use, no folder (''), or NEW_FOLDER for a name the caller asks for.
export function FolderPicker({ folders, value, onChange }: { folders: string[]; value: string; onChange: (value: string) => void }) {
  const labelId = useId()
  return (
    <FormControl fullWidth>
      <InputLabel id={labelId} shrink>
        Folder
      </InputLabel>
      <Select
        labelId={labelId}
        label="Folder"
        value={value}
        notched
        displayEmpty
        inputProps={NO_PASSWORD_MANAGER}
        renderValue={(chosen) => (chosen === '' ? 'No folder' : chosen === NEW_FOLDER ? 'New folder' : chosen)}
        onChange={(event) => onChange(event.target.value)}
      >
        <MenuItem value="">No folder</MenuItem>
        {folders.map((name) => (
          <MenuItem key={name} value={name}>
            {name}
          </MenuItem>
        ))}
        <MenuItem value={NEW_FOLDER}>New folder</MenuItem>
      </Select>
    </FormControl>
  )
}
