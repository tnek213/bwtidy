import { Menu, MenuItem } from '@mui/material'
import { FOLDER_COLUMN } from '../api/grid.ts'
import type { HeaderActions } from './normalize.ts'
import { RARE_FIELDS_COLUMN } from './rareFields.ts'

export interface HeaderMenuState {
  columnId: string
  anchor: HTMLElement
}

interface HeaderMenuProps {
  state: HeaderMenuState | null
  selected: ReadonlySet<string>
  nearDuplicates: HeaderActions['nearDuplicates']
  onClose: () => void
  onRename: (columnId: string) => void
  onMergeInto: (columnId: string) => void
  onToggleSelected: (columnId: string) => void
  onMergeGroup: (columnIds: string[]) => void
  onManageFolders: () => void
  onSetRareFieldsMax: () => void
}

export default function HeaderMenu({ state, selected, nearDuplicates, onClose, ...actions }: HeaderMenuProps) {
  const columnId = state?.columnId ?? ''
  const group = nearDuplicates.get(columnId)?.group
  function run(action: () => void) {
    onClose()
    action()
  }
  // The folder column's menu is about the folders themselves, not about a field.
  if (columnId === FOLDER_COLUMN) {
    return (
      <Menu open={state !== null} anchorEl={state?.anchor} onClose={onClose}>
        <MenuItem onClick={() => run(actions.onManageFolders)}>Manage folders…</MenuItem>
      </Menu>
    )
  }
  // Rare fields is bwtidy's own column, its menu sets its N.
  if (columnId === RARE_FIELDS_COLUMN) {
    return (
      <Menu open={state !== null} anchorEl={state?.anchor} onClose={onClose}>
        <MenuItem onClick={() => run(actions.onSetRareFieldsMax)}>Set N</MenuItem>
      </Menu>
    )
  }
  return (
    <Menu open={state !== null} anchorEl={state?.anchor} onClose={onClose}>
      <MenuItem onClick={() => run(() => actions.onRename(columnId))}>
        Rename field…
      </MenuItem>
      <MenuItem onClick={() => run(() => actions.onMergeInto(columnId))}>
        Merge into…
      </MenuItem>
      {group && (
        <MenuItem onClick={() => run(() => actions.onMergeGroup(group.columns))}>
          Merge near duplicates…
        </MenuItem>
      )}
      <MenuItem onClick={() => run(() => actions.onToggleSelected(columnId))}>
        {selected.has(columnId) ? 'Deselect for merge' : 'Select for merge'}
      </MenuItem>
    </Menu>
  )
}
