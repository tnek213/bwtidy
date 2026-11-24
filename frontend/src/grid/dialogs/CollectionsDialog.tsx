import {
  Alert,
  Checkbox,
  FormControl,
  FormControlLabel,
  FormGroup,
  FormHelperText,
  FormLabel,
} from '@mui/material'
import { useState } from 'react'
import type { GridColumn } from '../../api/grid.ts'
import { setCollections } from '../../api/operations.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import { toggled } from '../../shared/sets.ts'
import { organizationCollections } from '../editing.ts'
import { NO_COLLECTIONS, PICK_A_COLLECTION } from './collectionChoices.tsx'
import type { DialogProps } from './types.ts'
import { rowName } from '../labels.ts'

export function CollectionsDialog({ row, rows, organizations, column, onClose, onApply }: DialogProps & { column: GridColumn }) {
  const organization = row.cells.organization as string
  const current = Array.isArray(row.cells.collections) ? row.cells.collections : []
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(current))
  const known = organizationCollections(rows, organizations).get(organization) ?? []
  const options = [...new Set([...known, ...current])]
  const unchanged = chosen.size === current.length && current.every((name) => chosen.has(name))
  function toggle(name: string, on: boolean) {
    setChosen(toggled(chosen, name, on))
  }
  return (
    <FormDialog
      title={`${column.label} of ${rowName(row)}`}
      canSubmit={!unchanged && chosen.size > 0}
      onClose={onClose}
      onSubmit={() => onApply([setCollections(row, options.filter((name) => chosen.has(name)))])}
    >
      {options.length === 0 ? (
        <Alert severity="info">
          {organization} {NO_COLLECTIONS}.
        </Alert>
      ) : (
        <FormControl component="fieldset">
          <FormLabel component="legend">Collections of {organization}</FormLabel>
          <FormGroup>
            {options.map((name) => (
              <FormControlLabel
                key={name}
                label={name}
                control={<Checkbox checked={chosen.has(name)} onChange={(event) => toggle(name, event.target.checked)} />}
              />
            ))}
          </FormGroup>
          {chosen.size === 0 && <FormHelperText>{PICK_A_COLLECTION}</FormHelperText>}
        </FormControl>
      )}
    </FormDialog>
  )
}
