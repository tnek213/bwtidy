import {
  Alert,
  Box,
  Checkbox,
  CircularProgress,
  FormControl,
  FormControlLabel,
  FormGroup,
  FormLabel,
  IconButton,
  Radio,
  RadioGroup,
  Stack,
  SvgIcon,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { useId, useState } from 'react'
import { NO_PASSWORD_MANAGER } from './noPasswordManager.ts'
import { useDebouncedRequest } from './useDebouncedRequest.ts'
import { fetchExample, type GeneratorOptions, type PassphraseOptions, type PasswordOptions } from '../api/generator.ts'
import {
  DEFAULT_PASSPHRASE,
  DEFAULT_PASSWORD,
  LENGTH_LIMITS,
  MINIMUM_LIMITS,
  optionsProblem,
  WORD_LIMITS,
} from './generatorRules.ts'

// The form waits for a pause in the typing before it asks bw for an example.
const EXAMPLE_DELAY_MS = 300

function RefreshIcon() {
  return (
    <SvgIcon fontSize="inherit">
      <path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4z" />
    </SvgIcon>
  )
}

interface NumberInputProps {
  label: string
  value: number
  limits: { min: number; max: number }
  disabled?: boolean
  onChange: (value: number) => void
}

// An emptied input is NaN, which optionsProblem refuses until a number is back.
function NumberInput({ label, value, limits, disabled, onChange }: NumberInputProps) {
  return (
    <TextField
      label={label}
      type="number"
      size="small"
      disabled={disabled}
      value={Number.isNaN(value) ? '' : value}
      onChange={(event) => onChange(event.target.value === '' ? Number.NaN : Number(event.target.value))}
      slotProps={{ htmlInput: { ...NO_PASSWORD_MANAGER, ...limits } }}
      sx={{ width: 160 }}
    />
  )
}

interface BoxProps {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}

function OptionBox({ label, checked, onChange }: BoxProps) {
  return (
    <FormControlLabel
      label={label}
      control={<Checkbox checked={checked} onChange={(event) => onChange(event.target.checked)} />}
    />
  )
}

function PasswordInputs({ value, onChange }: { value: PasswordOptions; onChange: (value: PasswordOptions) => void }) {
  return (
    <>
      <NumberInput
        label="Length"
        value={value.length}
        limits={LENGTH_LIMITS}
        onChange={(length) => onChange({ ...value, length })}
      />
      <FormGroup row>
        <OptionBox label="A-Z" checked={value.uppercase} onChange={(uppercase) => onChange({ ...value, uppercase })} />
        <OptionBox label="a-z" checked={value.lowercase} onChange={(lowercase) => onChange({ ...value, lowercase })} />
        <OptionBox label="0-9" checked={value.number} onChange={(number) => onChange({ ...value, number })} />
        <OptionBox label="Special" checked={value.special} onChange={(special) => onChange({ ...value, special })} />
      </FormGroup>
      <Stack direction="row" sx={{ gap: 2 }}>
        <NumberInput
          label="Minimum numbers"
          value={value.minNumber}
          limits={MINIMUM_LIMITS}
          disabled={!value.number}
          onChange={(minNumber) => onChange({ ...value, minNumber })}
        />
        <NumberInput
          label="Minimum special"
          value={value.minSpecial}
          limits={MINIMUM_LIMITS}
          disabled={!value.special}
          onChange={(minSpecial) => onChange({ ...value, minSpecial })}
        />
      </Stack>
      <OptionBox
        label="Avoid ambiguous characters"
        checked={value.ambiguous}
        onChange={(ambiguous) => onChange({ ...value, ambiguous })}
      />
    </>
  )
}

function PassphraseInputs({ value, onChange }: { value: PassphraseOptions; onChange: (value: PassphraseOptions) => void }) {
  return (
    <>
      <NumberInput
        label="Number of words"
        value={value.words}
        limits={WORD_LIMITS}
        onChange={(words) => onChange({ ...value, words })}
      />
      <TextField
        label="Word separator"
        size="small"
        helperText="One character, or empty for none."
        value={value.separator}
        onChange={(event) => onChange({ ...value, separator: event.target.value })}
        slotProps={{ htmlInput: { ...NO_PASSWORD_MANAGER, spellCheck: false } }}
        sx={{ width: 240 }}
      />
      <FormGroup row>
        <OptionBox label="Capitalize" checked={value.capitalize} onChange={(capitalize) => onChange({ ...value, capitalize })} />
        <OptionBox
          label="Include number"
          checked={value.includeNumber}
          onChange={(includeNumber) => onChange({ ...value, includeNumber })}
        />
      </FormGroup>
    </>
  )
}

// One example from bw, for options the backend would take. A newer request replaces an older one.
// The last example (or failure) stays while the next one is on its way.
function Example({ options }: { options: GeneratorOptions }) {
  const [refresh, setRefresh] = useState(0)
  const example = useDebouncedRequest(JSON.stringify([options, refresh]), () => fetchExample(options), EXAMPLE_DELAY_MS)
  const failed = 'error' in example
  const value = 'value' in example ? example.value : null

  return (
    <Stack direction="row" sx={{ alignItems: 'center', gap: 1, minHeight: 40 }}>
      <Typography variant="body2" color="text.secondary">
        Example
      </Typography>
      <Box
        component="span"
        data-testid="generator-example"
        sx={{ fontFamily: 'monospace', overflowWrap: 'anywhere', color: failed ? 'error.main' : undefined }}
      >
        {failed ? 'Could not get an example' : value}
      </Box>
      {!failed && value === null && <CircularProgress size={16} aria-label="Loading the example" />}
      <Tooltip title="Another example">
        <IconButton size="small" aria-label="Another example" onClick={() => setRefresh(refresh + 1)}>
          <RefreshIcon />
        </IconButton>
      </Tooltip>
    </Stack>
  )
}

// The options as Bitwarden's generator has them. Switching the type keeps what was set for the other one.
export default function GeneratorForm({ value, onChange }: { value: GeneratorOptions; onChange: (value: GeneratorOptions) => void }) {
  const typeLabelId = useId()
  const [other, setOther] = useState<GeneratorOptions>(value.type === 'password' ? DEFAULT_PASSPHRASE : DEFAULT_PASSWORD)
  const problem = optionsProblem(value)

  function switchType(type: string) {
    if (type !== value.type) {
      setOther(value)
      onChange(other)
    }
  }

  return (
    <Stack spacing={2} sx={{ alignItems: 'flex-start' }}>
      <FormControl>
        <FormLabel id={typeLabelId}>Type</FormLabel>
        <RadioGroup row aria-labelledby={typeLabelId} value={value.type} onChange={(event) => switchType(event.target.value)}>
          <FormControlLabel value="password" control={<Radio />} label="Password" />
          <FormControlLabel value="passphrase" control={<Radio />} label="Passphrase" />
        </RadioGroup>
      </FormControl>
      {value.type === 'password' ? (
        <PasswordInputs value={value} onChange={onChange} />
      ) : (
        <PassphraseInputs value={value} onChange={onChange} />
      )}
      {problem !== null ? <Alert severity="warning">{problem}</Alert> : <Example options={value} />}
    </Stack>
  )
}
