import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  FormControl,
  FormControlLabel,
  FormHelperText,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Typography,
} from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { DATE_FORMATS, formatDate } from '../shared/format.ts'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import { queueQueryKey } from '../api/queue.ts'
import DeleteDataSection from './DeleteDataSection.tsx'
import { type DateFormat, type Display, displayQueryKey, saveDisplay, useDisplay } from '../api/settings.ts'
import { type GeneratorOptions, generatorQueryKey, saveDefault, useGenerator } from '../api/generator.ts'
import GeneratorForm from '../shared/GeneratorForm.tsx'
import { type Layout, useLayout } from '../theme/layout.tsx'
import { optionsProblem, refusalOf } from '../shared/generatorRules.ts'
import Prose from '../theme/Prose.tsx'

const DEFAULT_DISPLAY: Display = { dateFormat: 'system', hideToolNote: false, rareFieldsMax: 0 }

const SECTION_SX = { maxWidth: 560, alignItems: 'flex-start' } as const

// How dates and times are shown, kept by the backend for the account and saved as soon as it is picked.
function DisplaySection() {
  const queryClient = useQueryClient()
  const display = useDisplay()
  const labelId = useId()
  const layoutLabelId = useId()
  const { layout, setLayout } = useLayout()
  const [failure, setFailure] = useState<string | null>(null)
  const now = new Date()

  // Every page follows a change at once, the answer then confirms it. Only the changed field is sent.
  async function change(changed: Partial<Display>) {
    setFailure(null)
    queryClient.setQueryData<Display>(displayQueryKey, (current) => ({ ...DEFAULT_DISPLAY, ...current, ...changed }))
    try {
      queryClient.setQueryData(displayQueryKey, await saveDisplay(changed))
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error))
      await queryClient.invalidateQueries({ queryKey: displayQueryKey })
    }
  }

  return (
    <Stack component="section" aria-labelledby="display-heading" spacing={2} sx={SECTION_SX}>
      <Typography id="display-heading" variant="h6" component="h3">
        Display
      </Typography>
      {display.isError && <Alert severity="warning">Could not load the display settings: {display.error.message}</Alert>}
      <FormControl sx={{ minWidth: 320 }} disabled={display.isPending}>
        <InputLabel id={labelId}>Date and time format</InputLabel>
        <Select
          labelId={labelId}
          label="Date and time format"
          value={display.data?.dateFormat ?? DEFAULT_DISPLAY.dateFormat}
          inputProps={NO_PASSWORD_MANAGER}
          onChange={(event) => void change({ dateFormat: event.target.value as DateFormat })}
        >
          {DATE_FORMATS.map(({ value, label }) => (
            <MenuItem key={value} value={value}>
              {label} ({formatDate(now, value)})
            </MenuItem>
          ))}
        </Select>
      </FormControl>
      <FormControl sx={{ minWidth: 320 }}>
        <InputLabel id={layoutLabelId}>Layout</InputLabel>
        <Select
          labelId={layoutLabelId}
          label="Layout"
          value={layout}
          inputProps={NO_PASSWORD_MANAGER}
          onChange={(event) => setLayout(event.target.value as Layout)}
        >
          <MenuItem value="normal">Normal</MenuItem>
          <MenuItem value="compact">Compact</MenuItem>
        </Select>
        <FormHelperText>Saved in this browser only.</FormHelperText>
      </FormControl>
      <FormControl disabled={display.isPending}>
        <FormControlLabel
          label="Hide bwtidy's own note"
          control={
            <Checkbox
              checked={display.data?.hideToolNote ?? false}
              onChange={(event) => void change({ hideToolNote: event.target.checked })}
            />
          }
        />
        <FormHelperText sx={{ ml: 4 }}>The note keeps your templates and settings. Hidden only in the Vault grid.</FormHelperText>
      </FormControl>
      {failure !== null && <Alert severity="error">Nothing was saved: {failure}</Alert>}
    </Stack>
  )
}

// The generator's default, which bwtidy keeps in its own note.
function GeneratorSection() {
  const queryClient = useQueryClient()
  const doc = useGenerator()
  // What the user changed since the stored default loaded.
  const [draft, setDraft] = useState<GeneratorOptions | null>(null)
  const [sending, setSending] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  async function save(options: GeneratorOptions) {
    setSending(true)
    setRefusal(null)
    try {
      await saveDefault(options)
      setDraft(null)
    } catch (error) {
      setRefusal(refusalOf(error))
    }
    setSending(false)
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: generatorQueryKey }),
      queryClient.invalidateQueries({ queryKey: queueQueryKey }),
    ])
  }

  if (doc.isPending) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}>
        <CircularProgress aria-label="Loading the settings" />
      </Box>
    )
  }
  if (doc.isError) {
    return <Alert severity="error">Could not load the settings: {doc.error.message}</Alert>
  }

  const options = draft ?? doc.data.default
  return (
    <Stack component="section" aria-labelledby="generator-heading" spacing={2} sx={SECTION_SX}>
      <Stack direction="row" sx={{ alignItems: 'center', gap: 1 }}>
        <Typography id="generator-heading" variant="h6" component="h3">
          Password generator
        </Typography>
        {doc.data.saving && <Chip size="small" color="warning" label="Saving" />}
      </Stack>
      <Prose>
        The default for generated passwords. An item can have its own setting, from the cell menu of its password in the grid.
      </Prose>
      {doc.data.error !== undefined && (
        <Alert severity="warning">The stored settings are not valid and the next save replaces them: {doc.data.error}</Alert>
      )}
      <GeneratorForm value={options} onChange={setDraft} />
      <Button
        variant="contained"
        disabled={draft === null || sending || optionsProblem(options) !== null}
        onClick={() => void save(options)}
      >
        Save
      </Button>
      {refusal !== null && <Alert severity="error">Nothing was saved: {refusal}</Alert>}
    </Stack>
  )
}

// The Settings page (docs/spec.md, Settings): the display, then the password generator.
export default function SettingsPage() {
  return (
    <Box sx={{ p: 2, overflow: 'auto' }}>
      <Typography variant="h5" component="h2" gutterBottom>
        Settings
      </Typography>
      <Stack spacing={4}>
        <DisplaySection />
        <GeneratorSection />
        <DeleteDataSection />
      </Stack>
    </Box>
  )
}
