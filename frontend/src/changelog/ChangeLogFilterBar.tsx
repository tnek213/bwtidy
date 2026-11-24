import { Autocomplete, Button, FormControl, InputLabel, MenuItem, Select, Stack, TextField, Tooltip } from '@mui/material'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import { operationLabel } from '../shared/operationLabels.ts'
import DateField from '../shared/DateField.tsx'
import {
  type ChangeLogFilters,
  FILTER_KINDS,
  hasFilters,
  ITEM_LIMIT,
  noChangeLogFilters,
} from './filters.ts'
import { RESULT_LABELS, SOURCE_LABELS } from './rows.ts'

function kindLabel(kind: string): string {
  return operationLabel(kind)?.label ?? kind
}

interface MultiSelectProps {
  label: string
  value: string[]
  options: string[]
  optionLabel: (value: string) => string
  onChange: (value: string[]) => void
}

function MultiSelect({ label, value, options, optionLabel, onChange }: MultiSelectProps) {
  const id = `change-log-filter-${label.toLowerCase()}`
  return (
    <FormControl size="small" sx={{ minWidth: 150, maxWidth: 320 }}>
      <InputLabel id={`${id}-label`} shrink>
        {label}
      </InputLabel>
      <Select<string[]>
        labelId={`${id}-label`}
        id={id}
        label={label}
        multiple
        notched
        displayEmpty
        inputProps={NO_PASSWORD_MANAGER}
        value={value}
        renderValue={(selected) => (selected.length === 0 ? 'All' : selected.map(optionLabel).join(', '))}
        onChange={(event) => {
          const next = event.target.value
          onChange(typeof next === 'string' ? next.split(',') : next)
        }}
      >
        {options.map((option) => (
          <MenuItem key={option} value={option}>
            {optionLabel(option)}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  )
}

// What the Items field says under it: how many items its text names, when that is not a plain match.
function itemsHelp(text: string, matched: number | undefined): string {
  if (text.trim() === '' || matched === undefined) {
    return ''
  }
  if (matched === 0) {
    return 'No item has that in its name'
  }
  if (matched > ITEM_LIMIT) {
    return `${matched} items match, type more of the name`
  }
  return matched === 1 ? '1 item' : `${matched} items`
}

interface ChangeLogFilterBarProps {
  filters: ChangeLogFilters
  onFilters: (filters: ChangeLogFilters) => void
  fields: string[]
  // How many items the Items text matches, undefined while the grid loads.
  matchedItems: number | undefined
  // Opens the diff report, which has nothing to list when no change row is shown.
  onDiffReport: () => void
  canDiffReport: boolean
}

const DIFF_REPORT_HINT = 'Lists what every shown change did. Filter the log first to narrow it down.'

export default function ChangeLogFilterBar({
  filters,
  onFilters,
  fields,
  matchedItems,
  onDiffReport,
  canDiffReport,
}: ChangeLogFilterBarProps) {
  const set =
    <K extends keyof ChangeLogFilters>(key: K) =>
    (value: ChangeLogFilters[K]) =>
      onFilters({ ...filters, [key]: value })
  const help = itemsHelp(filters.items, matchedItems)
  return (
    <Stack
      direction="row"
      useFlexGap
      role="search"
      aria-label="Change log filters"
      sx={{ flexWrap: 'wrap', alignItems: 'flex-start', gap: 1.5, mb: 2 }}
    >
      <DateField label="From" value={filters.from} onChange={set('from')} />
      <DateField label="To" value={filters.to} onChange={set('to')} />
      <MultiSelect
        label="Source"
        value={filters.sources}
        options={Object.keys(SOURCE_LABELS)}
        optionLabel={(source) => SOURCE_LABELS[source] ?? source}
        onChange={set('sources')}
      />
      <MultiSelect
        label="Operation"
        value={filters.kinds}
        options={FILTER_KINDS}
        optionLabel={kindLabel}
        onChange={set('kinds')}
      />
      <Autocomplete<string, true, false, true>
        multiple
        freeSolo
        size="small"
        sx={{ minWidth: 220, maxWidth: 420 }}
        options={fields}
        value={filters.columns}
        onChange={(_, next) => set('columns')(next)}
        renderInput={(params) => (
          <TextField
            {...params}
            label="Field"
            placeholder={filters.columns.length === 0 ? 'All' : undefined}
            slotProps={{
              ...params.slotProps,
              inputLabel: { ...params.slotProps.inputLabel, shrink: true },
              htmlInput: { ...params.slotProps.htmlInput, ...NO_PASSWORD_MANAGER },
            }}
          />
        )}
      />
      <TextField
        size="small"
        label="Items"
        placeholder="Name"
        value={filters.items}
        helperText={help || undefined}
        onChange={(event) => set('items')(event.target.value)}
        slotProps={{ inputLabel: { shrink: true }, htmlInput: NO_PASSWORD_MANAGER }}
      />
      <FormControl size="small" sx={{ minWidth: 110 }}>
        <InputLabel id="change-log-filter-result-label" shrink>
          Result
        </InputLabel>
        <Select
          labelId="change-log-filter-result-label"
          id="change-log-filter-result"
          label="Result"
          notched
          displayEmpty
          inputProps={NO_PASSWORD_MANAGER}
          value={filters.result}
          renderValue={(selected: string) => RESULT_LABELS[selected] ?? 'All'}
          onChange={(event) => set('result')(event.target.value as ChangeLogFilters['result'])}
        >
          <MenuItem value="">All</MenuItem>
          {Object.entries(RESULT_LABELS).map(([result, label]) => (
            <MenuItem key={result} value={result}>
              {label}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
      <Button onClick={() => onFilters(noChangeLogFilters)} disabled={!hasFilters(filters)} sx={{ mt: 0.25 }}>
        Clear filters
      </Button>
      {/* A disabled button gives no hover events, so the span carries the tooltip. */}
      <Tooltip title={DIFF_REPORT_HINT} describeChild>
        <span>
          <Button onClick={onDiffReport} disabled={!canDiffReport} sx={{ mt: 0.25 }}>
            Diff report
          </Button>
        </span>
      </Tooltip>
    </Stack>
  )
}
