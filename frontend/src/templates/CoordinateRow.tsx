import {
  Autocomplete,
  Box,
  Button,
  Checkbox,
  Chip,
  createFilterOptions,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import { useId, useRef } from 'react'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import { fieldColumn } from '../shared/coordinates.ts'
import type { VaultField } from '../api/grid.ts'
import type { FieldType } from '../api/operations.ts'
import type { Datatype } from '../api/datatypes.ts'
import type { TemplateCoordinate } from '../api/templates.ts'
import { coordinateColumn, FIELD_TYPE_LABELS, isSecretCoordinate } from '../shared/templateRules.ts'
import {
  canTakeDatatype,
  coordinateKey,
  EMPTY_FIELD,
  nearFields,
  PATTERN_HINT,
  UNKNOWN_FIELD,
  unknownField,
  withDatatype,
  withOptional,
} from './drafts.ts'

// What the coordinate input offers: a built-in leaf, a custom field, the
// step to a field the vault does not have yet, or the one to a field name pattern.
type Step = { kind: 'new' } | { kind: 'newPattern' }
type Choice = TemplateCoordinate | Step

const NEW_FIELD_LABEL = 'New field…'
const PATTERN_LABEL = 'Field name pattern…'

const NEW_FIELD: Choice = { kind: 'new' }
const NEW_PATTERN: Choice = { kind: 'newPattern' }
const STEPS = [NEW_FIELD, NEW_PATTERN]

function isStep(choice: Choice): choice is Step {
  return choice.kind === 'new' || choice.kind === 'newPattern'
}

function choiceLabel(choice: Choice): string {
  if (choice.kind === 'new') {
    return NEW_FIELD_LABEL
  }
  return choice.kind === 'newPattern' ? PATTERN_LABEL : coordinateColumn(choice)
}

function choiceKey(choice: Choice): string {
  return isStep(choice) ? `\u0000${choice.kind}` : coordinateKey(choice)
}

const filterChoices = createFilterOptions<Choice>({ stringify: choiceLabel })

const OPTIONAL_HINT = 'Not needed for a match. A value still has to fit its datatype.'

// The select's value for no datatype. An id is 8 hex characters, so never this.
const NO_DATATYPE = 'none'

export interface Row {
  key: number
  coordinate: TemplateCoordinate
  // A new field's name, typed as free text after New field.
  typed: boolean
}

interface Props {
  index: number
  row: Row
  // The built-in leaves and the vault's fields no other row uses.
  choices: TemplateCoordinate[]
  // undefined until the grid is loaded, then no field is marked unknown.
  vaultFields: readonly VaultField[] | undefined
  datatypes: readonly Datatype[]
  autoFocus: boolean
  onChange: (coordinate: TemplateCoordinate, typed: boolean) => void
  onRemove: () => void
}

export default function CoordinateRow({ index, row, choices, vaultFields, datatypes, autoFocus, onChange, onRemove }: Props) {
  const { coordinate, typed } = row
  const typeLabelId = useId()
  const datatypeLabelId = useId()
  // What was typed into the picker last, so New field starts from it.
  const typedText = useRef('')
  const unknown = unknownField(coordinate, vaultFields)
  const value = coordinate.kind === 'field' && coordinate.name === '' ? null : coordinate
  const options: Choice[] =
    value === null || choices.some((choice) => choiceKey(choice) === choiceKey(value)) ? choices : [value, ...choices]
  const near = typed && coordinate.kind === 'field' ? nearFields(coordinate.name, vaultFields ?? []) : []

  // Another pick in the row keeps its optional flag and its datatype, unless the pick cannot take one.
  function picked(next: TemplateCoordinate): TemplateCoordinate {
    const kept = withOptional(next, coordinate.optional === true)
    return canTakeDatatype(kept) ? withDatatype(kept, coordinate.datatype) : withDatatype(kept, undefined)
  }

  function choose(choice: Choice | null) {
    if (choice === null) {
      onChange(EMPTY_FIELD, false)
    } else if (choice.kind === 'new') {
      onChange(picked({ kind: 'field', name: typedText.current.trim(), fieldType: 0 }), true)
    } else if (choice.kind === 'newPattern') {
      onChange(picked({ kind: 'fieldPattern', pattern: typedText.current.trim(), fieldType: 0 }), true)
    } else {
      onChange(picked(choice), false)
    }
  }

  function typeText(choice: Choice): string {
    if (isStep(choice)) {
      return ''
    }
    if (choice.kind === 'path') {
      return isSecretCoordinate(choice) ? 'Built-in, hidden' : 'Built-in'
    }
    return unknownField(choice, vaultFields) ? 'Not in the vault' : FIELD_TYPE_LABELS[choice.fieldType]
  }

  return (
    <Box
      role="group"
      aria-label={`Coordinate row ${index + 1}`}
      title={unknown ? UNKNOWN_FIELD : undefined}
      data-unknown-field={unknown || undefined}
      sx={{
        borderRadius: 1,
        p: 0.5,
        bgcolor: unknown ? (theme) => alpha(theme.palette.warning.main, 0.15) : undefined,
      }}
    >
      <Stack direction="row" spacing={1} sx={{ alignItems: 'flex-start' }}>
        {coordinate.kind === 'fieldPattern' ? (
          <Stack direction="row" spacing={1} sx={{ alignItems: 'flex-start', flexGrow: 1 }}>
            <Chip size="small" label="pattern" color="info" variant="outlined" sx={{ mt: 1 }} />
            <TextField
              size="small"
              autoFocus={typed}
              label={`Field name pattern ${index + 1}`}
              value={coordinate.pattern}
              onChange={(event) => onChange({ ...coordinate, pattern: event.target.value }, typed)}
              helperText={PATTERN_HINT}
              sx={{ flexGrow: 1 }}
              slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
            />
          </Stack>
        ) : typed && coordinate.kind === 'field' ? (
          <TextField
            size="small"
            autoFocus
            label={`New field name ${index + 1}`}
            value={coordinate.name}
            onChange={(event) => onChange({ ...coordinate, name: event.target.value }, true)}
            helperText={unknown && near.length === 0 ? 'Not in the vault yet' : undefined}
            sx={{ flexGrow: 1 }}
            slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
          />
        ) : (
          <Autocomplete<Choice>
            size="small"
            openOnFocus
            sx={{ flexGrow: 1 }}
            options={[...options, ...STEPS]}
            value={value}
            onChange={(_, next) => choose(next)}
            onInputChange={(_, text, reason) => {
              if (reason === 'input') {
                typedText.current = text
              }
            }}
            getOptionLabel={choiceLabel}
            isOptionEqualToValue={(option, wanted) => choiceKey(option) === choiceKey(wanted)}
            // New field and Field name pattern stay the last options whatever is typed.
            filterOptions={(all, state) => [...filterChoices(all.filter((choice) => !isStep(choice)), state), ...STEPS]}
            renderOption={(props, choice) => {
              const { key, ...rest } = props
              const kind = typeText(choice)
              return (
                <li key={key} {...rest} aria-label={kind === '' ? choiceLabel(choice) : `${choiceLabel(choice)}, ${kind}`}>
                  {isStep(choice) ? <em>{choiceLabel(choice)}</em> : choiceLabel(choice)}
                  <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 'auto', pl: 2 }}>
                    {kind}
                  </Typography>
                </li>
              )
            }}
            renderInput={(params) => (
              <TextField
                {...params}
                autoFocus={autoFocus}
                label={`Coordinate ${index + 1}`}
                slotProps={{ ...params.slotProps, htmlInput: { ...params.slotProps.htmlInput, ...NO_PASSWORD_MANAGER } }}
              />
            )}
          />
        )}
        {coordinate.kind !== 'path' ? (
          <FormControl size="small" sx={{ minWidth: 120 }}>
            <InputLabel id={typeLabelId}>Field type</InputLabel>
            <Select
              labelId={typeLabelId}
              label="Field type"
              value={coordinate.fieldType}
              inputProps={NO_PASSWORD_MANAGER}
              onChange={(event) => onChange(picked({ ...coordinate, fieldType: Number(event.target.value) as FieldType }), typed)}
            >
              <MenuItem value={0}>{FIELD_TYPE_LABELS[0]}</MenuItem>
              <MenuItem value={1}>{FIELD_TYPE_LABELS[1]}</MenuItem>
              {coordinate.fieldType === 2 && <MenuItem value={2}>{FIELD_TYPE_LABELS[2]}</MenuItem>}
            </Select>
          </FormControl>
        ) : (
          <Box sx={{ minWidth: 120 }} />
        )}
        {canTakeDatatype(coordinate) ? (
          <FormControl size="small" sx={{ width: 160 }}>
            <InputLabel id={datatypeLabelId}>Datatype</InputLabel>
            <Select
              labelId={datatypeLabelId}
              label="Datatype"
              value={coordinate.datatype ?? NO_DATATYPE}
              inputProps={NO_PASSWORD_MANAGER}
              onChange={(event) =>
                onChange(withDatatype(coordinate, event.target.value === NO_DATATYPE ? undefined : event.target.value), typed)
              }
            >
              <MenuItem value={NO_DATATYPE}>None</MenuItem>
              {datatypes
                .filter((datatype) => datatype.id !== '')
                .map((datatype) => (
                  <MenuItem
                    key={datatype.id}
                    value={datatype.id}
                    disabled={datatype.broken !== null && datatype.id !== coordinate.datatype}
                  >
                    {datatype.broken === null ? datatype.name : `${datatype.name} (broken)`}
                  </MenuItem>
                ))}
              {coordinate.datatype !== undefined && !datatypes.some((datatype) => datatype.id === coordinate.datatype) && (
                <MenuItem value={coordinate.datatype}>Unknown datatype</MenuItem>
              )}
            </Select>
          </FormControl>
        ) : (
          <Box sx={{ width: 160 }} />
        )}
        <Tooltip title={OPTIONAL_HINT} describeChild>
          <FormControlLabel
            sx={{ mr: 0 }}
            control={
              <Checkbox
                checked={coordinate.optional === true}
                onChange={(event) => onChange(withOptional(coordinate, event.target.checked), typed)}
              />
            }
            label="Optional"
          />
        </Tooltip>
        <IconButton aria-label={`Remove coordinate ${index + 1}`} onClick={onRemove}>
          ✕
        </IconButton>
      </Stack>
      {near.map((field) => (
        <Stack key={field.name} direction="row" spacing={1} sx={{ alignItems: 'center', ml: 1 }}>
          <Typography variant="body2">
            The vault has {fieldColumn(field.name)} ({FIELD_TYPE_LABELS[field.fieldType]}).
          </Typography>
          <Button size="small" onClick={() => onChange(picked({ kind: 'field', ...field }), false)}>
            Use {fieldColumn(field.name)}
          </Button>
        </Stack>
      ))}
    </Box>
  )
}
