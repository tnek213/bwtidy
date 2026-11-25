import { ListItemText, MenuItem } from '@mui/material'

export const NO_COLLECTIONS = 'has no collections, create one in Bitwarden first'
export const PICK_A_COLLECTION = 'Pick at least one collection.'

// An organization with no collections cannot take an item, so its option is disabled and says why.
export function organizationOption(name: string, collections: readonly string[]) {
  const empty = collections.length === 0
  return (
    <MenuItem key={name} value={name} disabled={empty}>
      <ListItemText primary={name} secondary={empty ? NO_COLLECTIONS : undefined} />
    </MenuItem>
  )
}
