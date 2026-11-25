import {
  DialogContentText,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
} from '@mui/material'
import { useId, useMemo, useState } from 'react'
import { isFieldColumn } from '../../shared/coordinates.ts'
import { TYPE_LABELS } from '../../shared/templateRules.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import { NO_PASSWORD_MANAGER } from '../../shared/noPasswordManager.ts'
import ProseAlert from '../../theme/ProseAlert.tsx'
import { byValueCount, expectedConflicts, isMergeable, itemsWithAny } from '../normalize.ts'
import type { NormalizeDialogProps } from './types.ts'

interface MergeColumnsDialogProps extends NormalizeDialogProps {
  // The columns to merge. With fixedSource set, the target is picked among the
  // other columns and fixedSource always stays a source.
  columnIds: string[]
  fixedSource?: string
  onMerge: (sources: string[], target: string) => void
}

export function MergeColumnsDialog(props: MergeColumnsDialogProps) {
  const { columnIds, fixedSource, rows, columns, onClose, onMerge } = props
  const byId = useMemo(() => new Map(columns.map((column) => [column.id, column])), [columns])
  const candidates = columnIds.filter((id) => id !== fixedSource)
  const [target, setTarget] = useState(() => byValueCount(rows, candidates)[0] ?? '')
  const labelId = useId()
  const others = columns.filter((column) => isMergeable(column) && !columnIds.includes(column.id))
  const options = [...candidates, ...others.map((column) => column.id)]
  const sources = columnIds.filter((id) => id !== target)
  const targetColumn = byId.get(target)
  const affected = itemsWithAny(rows, sources)
  const choices = expectedConflicts(affected, [...sources, target]).length
  const builtIn = targetColumn && !isFieldColumn(target) ? targetColumn.group : null
  const lacking = builtIn ? affected.filter((row) => row.cells.type !== builtIn).length : 0
  const secretIntoText =
    targetColumn !== undefined && !targetColumn.secret && sources.some((id) => byId.get(id)?.secret === true)
  return (
    <FormDialog
      title="Merge columns"
      submitLabel="Merge"
      canSubmit={target !== '' && sources.length > 0}
      onClose={onClose}
      onSubmit={() => onMerge(sources, target)}
    >
      <FormControl fullWidth>
        <InputLabel id={labelId}>Target column</InputLabel>
        <Select
          labelId={labelId}
          label="Target column"
          value={target}
          inputProps={NO_PASSWORD_MANAGER}
          onChange={(event) => setTarget(event.target.value)}
        >
          {options.map((id) => (
            <MenuItem key={id} value={id}>
              {id}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
      {target !== '' && (
        <DialogContentText>
          {sources.join(', ')} {sources.length === 1 ? 'moves' : 'move'} into {target} in {affected.length}{' '}
          {affected.length === 1 ? 'item' : 'items'}.
          {choices > 0 && ` ${choices} of them ${choices === 1 ? 'has' : 'have'} more than one value, you choose which to keep next.`}
        </DialogContentText>
      )}
      {lacking > 0 && builtIn && (
        <ProseAlert severity="warning">
          {lacking} {lacking === 1 ? 'item is' : 'items are'} not of type {TYPE_LABELS[builtIn] ?? builtIn} and{' '}
          {lacking === 1 ? 'has' : 'have'} no {target}. Convert them through a template instead.
        </ProseAlert>
      )}
      {secretIntoText && (
        <ProseAlert severity="warning">
          A hidden value cannot move into {target}, which is not secret. Items where that happens are refused.
        </ProseAlert>
      )}
    </FormDialog>
  )
}
