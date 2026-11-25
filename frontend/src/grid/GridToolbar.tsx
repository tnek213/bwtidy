import {
  Button,
  Chip,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import ColumnChooser from './ColumnChooser.tsx'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import type { GridColumn } from '../api/grid.ts'
import { FORMAT_FILTER_LABELS, FORMAT_OFF, FORMAT_PARTIAL, NEAR_PREFIX } from './matches.ts'
import { TYPE_LABELS } from '../shared/templateRules.ts'
import { type FilterOptions, type Filters, NONE } from './filters.ts'

interface FilterSelectProps {
  label: string
  value: string
  options: string[]
  noneLabel: string
  optionLabel?: (value: string) => string
  onChange: (value: string) => void
}

function FilterSelect({ label, value, options, noneLabel, optionLabel, onChange }: FilterSelectProps) {
  const id = `grid-filter-${label.toLowerCase()}`
  return (
    <FormControl size="small" sx={{ minWidth: 150 }}>
      <InputLabel id={`${id}-label`} shrink>
        {label}
      </InputLabel>
      <Select
        labelId={`${id}-label`}
        id={id}
        label={label}
        value={value}
        notched
        displayEmpty
        inputProps={NO_PASSWORD_MANAGER}
        renderValue={(selected) =>
          selected === '' ? 'All' : selected === NONE ? noneLabel : (optionLabel?.(selected) ?? selected)
        }
        onChange={(event) => onChange(event.target.value)}
      >
        <MenuItem value="">All</MenuItem>
        {options.map((option) => (
          <MenuItem key={option} value={option}>
            {optionLabel?.(option) ?? option}
          </MenuItem>
        ))}
        {noneLabel && <MenuItem value={NONE}>{noneLabel}</MenuItem>}
      </Select>
    </FormControl>
  )
}

export interface ToolbarProps {
  filters: Filters
  options: FilterOptions
  onFilters: (filters: Filters) => void
  hideEmpty: boolean
  onHideEmpty: (hideEmpty: boolean) => void
  hideRare: boolean
  onHideRare: (hideRare: boolean) => void
  // Rare fields is off (N is 0), so there is nothing to hide.
  rareOff: boolean
  showQueued: boolean
  onShowQueued: (showQueued: boolean) => void
  columns: GridColumn[]
  hidden: ReadonlySet<string>
  onHidden: (hidden: Set<string>) => void
  selected: ReadonlySet<string>
  onSelected: (selected: Set<string>) => void
  onMergeSelected: () => void
  shown: number
  total: number
  templateFilter: string
  templateNames: string[]
  onTemplateFilter: (value: string) => void
  formatFilter: string
  onFormatFilter: (value: string) => void
  onNewItem: () => void
  toFillCount: number
  toFillOnly: boolean
  onToFillOnly: (toFillOnly: boolean) => void
  ownGeneratorCount: number
  ownGeneratorOnly: boolean
  onOwnGeneratorOnly: (ownGeneratorOnly: boolean) => void
}

function templateFilterLabel(value: string): string {
  return value.startsWith(NEAR_PREFIX) ? `Near misses of ${value.slice(NEAR_PREFIX.length)}` : value
}

export default function GridToolbar(props: ToolbarProps) {
  const { filters, options, onFilters } = props
  const set = (key: keyof Filters) => (value: string) => onFilters({ ...filters, [key]: value })
  return (
    <Stack direction="row" useFlexGap sx={{ flexWrap: 'wrap', alignItems: 'center', gap: 1.5, p: 1.5 }}>
      <Button variant="outlined" onClick={props.onNewItem}>
        New item
      </Button>
      {props.toFillCount > 0 && (
        <Chip
          label={`To fill in (${props.toFillCount})`}
          color="warning"
          variant={props.toFillOnly ? 'filled' : 'outlined'}
          aria-pressed={props.toFillOnly}
          onClick={() => props.onToFillOnly(!props.toFillOnly)}
          onDelete={props.toFillOnly ? () => props.onToFillOnly(false) : undefined}
        />
      )}
      {props.ownGeneratorCount > 0 && (
        <Chip
          label={`Own generator settings (${props.ownGeneratorCount})`}
          color="primary"
          variant={props.ownGeneratorOnly ? 'filled' : 'outlined'}
          aria-pressed={props.ownGeneratorOnly}
          onClick={() => props.onOwnGeneratorOnly(!props.ownGeneratorOnly)}
          onDelete={props.ownGeneratorOnly ? () => props.onOwnGeneratorOnly(false) : undefined}
        />
      )}
      <TextField
        size="small"
        label="Search"
        type="search"
        value={filters.text}
        onChange={(event) => set('text')(event.target.value)}
        sx={{ minWidth: 220 }}
        slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
      />
      <FilterSelect
        label="Type"
        value={filters.type}
        options={options.type}
        noneLabel=""
        optionLabel={(value) => TYPE_LABELS[value] ?? value}
        onChange={set('type')}
      />
      <FilterSelect label="Folder" value={filters.folder} options={options.folder} noneLabel="No folder" onChange={set('folder')} />
      <FilterSelect
        label="Organization"
        value={filters.organization}
        options={options.organization}
        noneLabel="Personal"
        onChange={set('organization')}
      />
      <FilterSelect
        label="Collection"
        value={filters.collection}
        options={options.collection}
        noneLabel="No collection"
        onChange={set('collection')}
      />
      <FilterSelect
        label="Template"
        value={props.templateFilter}
        options={props.templateNames.flatMap((name) => [name, `${NEAR_PREFIX}${name}`])}
        noneLabel="No template"
        optionLabel={templateFilterLabel}
        onChange={props.onTemplateFilter}
      />
      <FilterSelect
        label="Format"
        value={props.formatFilter}
        options={[FORMAT_OFF, FORMAT_PARTIAL]}
        noneLabel=""
        optionLabel={(value) => FORMAT_FILTER_LABELS[value] ?? value}
        onChange={props.onFormatFilter}
      />
      <FormControlLabel
        control={<Switch checked={props.hideEmpty} onChange={(event) => props.onHideEmpty(event.target.checked)} />}
        label="Hide empty columns"
      />
      <FormControlLabel
        disabled={props.rareOff}
        control={<Switch checked={props.hideRare} onChange={(event) => props.onHideRare(event.target.checked)} />}
        label="Hide rare field columns"
      />
      <FormControlLabel
        control={<Switch checked={props.showQueued} onChange={(event) => props.onShowQueued(event.target.checked)} />}
        label="Show queued changes"
      />
      <ColumnChooser
        columns={props.columns}
        hidden={props.hidden}
        onChange={props.onHidden}
        selected={props.selected}
        onSelected={props.onSelected}
        onMergeSelected={props.onMergeSelected}
      />
      <Typography variant="body2" color="text.secondary" sx={{ ml: 'auto' }}>
        {props.shown === props.total ? `${props.total} items` : `${props.shown} of ${props.total} items`}
      </Typography>
    </Stack>
  )
}
