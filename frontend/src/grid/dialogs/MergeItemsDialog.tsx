import {
  Alert,
  Autocomplete,
  FormControl,
  FormControlLabel,
  FormHelperText,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'
import { useState } from 'react'
import { isFieldColumn, fieldColumn } from '../../shared/coordinates.ts'
import type { GridColumn, GridRow } from '../../api/grid.ts'
import type { Operation } from '../../api/operations.ts'
import { FormDialog } from '../../shared/FormDialog.tsx'
import { NO_PASSWORD_MANAGER } from '../../shared/noPasswordManager.ts'
import { toggled } from '../../shared/sets.ts'
import ProseAlert from '../../theme/ProseAlert.tsx'
import { mergeItems, movableColumns, normalizable, targetColumnsFor } from '../normalize.ts'
import type { NormalizeDialogProps } from './types.ts'
import { ValuePreview } from './ValuePreview.tsx'
import { TYPE_LABELS } from '../../shared/templateRules.ts'
import { rowName } from '../labels.ts'
import { hasValue } from '../columnValues.ts'

const DONT_MOVE = ''

// A built-in leaf the target item has no slot for becomes a custom field
// named after its key, as in a type conversion.
function fallbackField(column: GridColumn): string | null {
  if (isFieldColumn(column.id) || column.id === 'notes' || column.multi) {
    return null
  }
  return fieldColumn(column.id.slice(column.id.lastIndexOf('.') + 1))
}

function targetOptions(columns: GridColumn[], target: GridRow, source: GridColumn): string[] {
  const options = targetColumnsFor(columns, target, source).map((column) => column.id)
  const fallback = fallbackField(source)
  if (fallback !== null && !options.includes(fallback)) {
    options.push(fallback)
  }
  return options
}

// Same column where the target has it free, else the fallback field. A list
// goes into the same list, which is added to by default. Nothing is
// overwritten unless the user picks it.
function defaultMapping(columns: GridColumn[], source: GridRow, target: GridRow): Record<string, string> {
  const mapping: Record<string, string> = {}
  for (const column of movableColumns(columns, source)) {
    const options = targetOptions(columns, target, column)
    const free = (id: string) => options.includes(id) && !hasValue(target.cells[id])
    const fallback = fallbackField(column)
    if (free(column.id) || (column.multi && options.includes(column.id))) {
      mapping[column.id] = column.id
    } else if (fallback !== null && free(fallback)) {
      mapping[column.id] = fallback
    } else {
      mapping[column.id] = DONT_MOVE
    }
  }
  return mapping
}

interface MergeItemsDialogProps extends NormalizeDialogProps {
  row: GridRow
  onApply: (operations: Operation[]) => void
}

export function MergeItemsDialog({ row, rows, columns, onClose, onApply }: MergeItemsDialogProps) {
  const [target, setTarget] = useState<GridRow | null>(null)
  // Source column to target column, DONT_MOVE for leaves that stay behind.
  const [mapping, setMapping] = useState<Record<string, string>>({})
  // Source list columns whose target list is replaced instead of added to.
  const [replaced, setReplaced] = useState<Set<string>>(new Set())
  const byId = new Map(columns.map((column) => [column.id, column]))
  const sources = movableColumns(columns, row)
  const others = normalizable(rows).filter((candidate) => candidate.id !== row.id)
  const chosen = Object.entries(mapping).filter(([, to]) => to !== DONT_MOVE)
  const targetCount = new Map<string, number>()
  for (const [, to] of chosen) {
    targetCount.set(to, (targetCount.get(to) ?? 0) + 1)
  }
  const duplicate = [...targetCount.values()].some((count) => count > 1)

  // A list moving into a list the target already has values in: add or replace.
  function joinsList(column: GridColumn, to: string): boolean {
    return target !== null && column.multi && to !== DONT_MOVE && hasValue(target.cells[to])
  }

  function pick(next: GridRow | null) {
    setTarget(next)
    setMapping(next ? defaultMapping(columns, row, next) : {})
    setReplaced(new Set())
  }

  function setListMode(columnId: string, replace: boolean) {
    setReplaced(toggled(replaced, columnId, replace))
  }

  function submit() {
    if (target) {
      const append = sources
        .filter((column) => !replaced.has(column.id) && joinsList(column, mapping[column.id] ?? DONT_MOVE))
        .map((column) => mapping[column.id])
      onApply([mergeItems(row, target, Object.fromEntries(chosen.map(([from, to]) => [to, from])), append)])
    }
  }

  return (
    <FormDialog
      title={`Merge ${rowName(row)} into another item`}
      maxWidth="md"
      submitLabel="Merge"
      canSubmit={target !== null && chosen.length > 0 && !duplicate}
      onClose={onClose}
      onSubmit={submit}
    >
      <Autocomplete
        options={others}
        value={target}
        onChange={(_, next) => pick(next)}
        getOptionLabel={rowName}
        isOptionEqualToValue={(option, value) => option.id === value.id}
        renderOption={(props, option) => {
          const { key, ...rest } = props
          const type = typeof option.cells.type === 'string' ? (TYPE_LABELS[option.cells.type] ?? option.cells.type) : ''
          const folder = typeof option.cells.folder === 'string' ? `, ${option.cells.folder}` : ''
          return (
            <li key={key} {...rest}>
              {rowName(option)}
              <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                {type}
                {folder}
              </Typography>
            </li>
          )
        }}
        renderInput={(params) => (
          <TextField
            {...params}
            autoFocus
            label="Target item"
            slotProps={{ ...params.slotProps, htmlInput: { ...params.slotProps.htmlInput, ...NO_PASSWORD_MANAGER } }}
          />
        )}
      />
      {target && sources.length === 0 && <Alert severity="info">{rowName(row)} has no values to move.</Alert>}
      {target && sources.length > 0 && (
        <Table size="small" aria-label="Mapping">
          <TableHead>
            <TableRow>
              <TableCell>From {rowName(row)}</TableCell>
              <TableCell>Value</TableCell>
              <TableCell>Into {rowName(target)}</TableCell>
              <TableCell>{rowName(target)} has</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {sources.map((column) => {
              const to = mapping[column.id] ?? DONT_MOVE
              const toColumn = byId.get(to)
              const list = joinsList(column, to)
              const adds = list && !replaced.has(column.id)
              const overwrites = to !== DONT_MOVE && hasValue(target.cells[to]) && !adds
              const clash = to !== DONT_MOVE && (targetCount.get(to) ?? 0) > 1
              const secretIntoText = column.secret && toColumn !== undefined && !toColumn.secret
              const label = `Target for ${column.id}`
              return (
                <TableRow key={column.id}>
                  <TableCell>{column.id}</TableCell>
                  <TableCell sx={{ maxWidth: 200 }}>
                    <ValuePreview row={row} column={column} />
                  </TableCell>
                  <TableCell sx={{ minWidth: 200 }}>
                    <FormControl size="small" fullWidth error={clash}>
                      <Select
                        inputProps={{ ...NO_PASSWORD_MANAGER, 'aria-label': label }}
                        value={to}
                        displayEmpty
                        renderValue={(value) => (value === DONT_MOVE ? "Don't move" : value)}
                        onChange={(event) => setMapping({ ...mapping, [column.id]: event.target.value })}
                      >
                        <MenuItem value={DONT_MOVE}>Don't move</MenuItem>
                        {targetOptions(columns, target, column).map((id) => (
                          <MenuItem key={id} value={id}>
                            {id}
                          </MenuItem>
                        ))}
                      </Select>
                      {list && (
                        <RadioGroup
                          row
                          aria-label={`List ${column.id}`}
                          value={adds ? 'add' : 'replace'}
                          onChange={(event) => setListMode(column.id, event.target.value === 'replace')}
                        >
                          <FormControlLabel value="add" control={<Radio size="small" />} label="Add to the list" />
                          <FormControlLabel value="replace" control={<Radio size="small" />} label="Replace" />
                        </RadioGroup>
                      )}
                      {clash && <FormHelperText>Two values go into {to}.</FormHelperText>}
                      {!clash && overwrites && <FormHelperText>Replaces the value {rowName(target)} has.</FormHelperText>}
                      {secretIntoText && <FormHelperText error>A hidden value cannot go into a column that is not secret.</FormHelperText>}
                    </FormControl>
                  </TableCell>
                  <TableCell sx={{ maxWidth: 200 }}>{to !== DONT_MOVE && <ValuePreview row={target} column={toColumn} />}</TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
      {target && (
        <ProseAlert severity="info">
          Values you don't move stay in {rowName(row)}, which goes to the trash afterwards (and is in the backup).
        </ProseAlert>
      )}
    </FormDialog>
  )
}
