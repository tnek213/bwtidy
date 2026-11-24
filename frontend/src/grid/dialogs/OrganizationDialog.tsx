import {
  Alert,
  Checkbox,
  DialogContentText,
  FormControl,
  FormControlLabel,
  FormGroup,
  FormHelperText,
  FormLabel,
  InputLabel,
  Select,
} from '@mui/material'
import { useId, useState } from 'react'
import { rowName } from '../labels.ts'
import { moveToOrganization } from '../../api/operations.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import { NO_PASSWORD_MANAGER } from '../../shared/noPasswordManager.ts'
import { toggled } from '../../shared/sets.ts'
import { isPersonal, organizationCollections } from '../editing.ts'
import { organizationOption, PICK_A_COLLECTION } from './collectionChoices.tsx'
import { PersonalDialog } from './PersonalDialog.tsx'
import type { DialogProps } from './types.ts'

export function OrganizationDialog(props: DialogProps) {
  return isPersonal(props.row) ? <JoinOrganizationDialog {...props} /> : <PersonalDialog {...props} />
}

function JoinOrganizationDialog({ row, rows, organizations, onClose, onApply }: DialogProps) {
  const byOrganization = organizationCollections(rows, organizations)
  const names = [...byOrganization.keys()]
  const usable = names.filter((name) => (byOrganization.get(name) ?? []).length > 0)
  const [organization, setOrganization] = useState(names.length === 1 && usable.length === 1 ? names[0] : '')
  const [chosen, setChosen] = useState<Set<string>>(() => new Set())
  const collections = byOrganization.get(organization) ?? []
  const labelId = useId()
  function toggle(name: string, on: boolean) {
    setChosen(toggled(chosen, name, on))
  }
  return (
    <FormDialog
      title={`Move ${rowName(row)} to an organization`}
      submitLabel="Move"
      canSubmit={organization !== '' && chosen.size > 0}
      onClose={onClose}
      onSubmit={() =>
        onApply([moveToOrganization(row, organization, collections.filter((name) => chosen.has(name)))])
      }
    >
      <DialogContentText>
        Everyone with access to the chosen collections sees the item.
      </DialogContentText>
      {names.length === 0 ? (
        <Alert severity="info">The vault has no organization, so there is none to choose.</Alert>
      ) : usable.length === 0 ? (
        <Alert severity="info">No organization of the vault has a collection, create one in Bitwarden first.</Alert>
      ) : (
        <FormControl fullWidth>
          <InputLabel id={labelId}>Organization</InputLabel>
          <Select
            labelId={labelId}
            label="Organization"
            value={organization}
            inputProps={NO_PASSWORD_MANAGER}
            onChange={(event) => {
              setOrganization(event.target.value)
              setChosen(new Set())
            }}
          >
            {names.map((name) => organizationOption(name, byOrganization.get(name) ?? []))}
          </Select>
        </FormControl>
      )}
      {organization !== '' && (
        <FormControl component="fieldset">
          <FormLabel component="legend">Collections</FormLabel>
          <FormGroup>
            {collections.map((name) => (
              <FormControlLabel
                key={name}
                label={name}
                control={<Checkbox checked={chosen.has(name)} onChange={(event) => toggle(name, event.target.checked)} />}
              />
            ))}
          </FormGroup>
          {chosen.size === 0 && (
            <FormHelperText>
              {PICK_A_COLLECTION} Bitwarden needs one to move an item into an organization.
            </FormHelperText>
          )}
        </FormControl>
      )}
    </FormDialog>
  )
}
