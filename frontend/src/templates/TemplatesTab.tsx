import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import { useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useId, useRef, useState } from 'react'
import { ApiError } from '../api/api.ts'
import { useGridData, type VaultField } from '../api/grid.ts'
import { queueQueryKey } from '../api/queue.ts'
import {
  saveTemplates,
  type Template,
  type TemplateChange,
  type TemplateCoordinate,
  type TemplateMatches,
  templatesQueryKey,
  useTemplateMatches,
  useTemplates,
} from '../api/templates.ts'
import CoordinateRow, { type Row } from './CoordinateRow.tsx'
import { type Datatype, useDatatypes } from '../api/datatypes.ts'
import { TYPE_GUIDANCE } from './typeGuidance.ts'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import { BUILT_IN_SLOTS, coordinateColumn, ITEM_TYPES, typeLabel } from '../shared/templateRules.ts'
import {
  coordinateKey,
  draftDuplicateWarnings,
  draftProblem,
  EMPTY_FIELD,
  templateChanges,
  UNKNOWN_FIELD,
  unknownField,
} from './drafts.ts'
import Prose from '../theme/Prose.tsx'
import ProseAlert from '../theme/ProseAlert.tsx'
import { VISUALLY_HIDDEN } from '../shared/visuallyHidden.ts'

function ExtensionHint() {
  return (
    <ProseAlert severity="info">
      Put the most copied values in <strong>login.username</strong>, <strong>login.password</strong> and{' '}
      <strong>login.totp</strong>: the browser extension copies those straight from the item list. Custom fields
      need the item opened, and autofill matches them by name against the name or id of a form input.
    </ProseAlert>
  )
}

function GuidanceList({ title, lines }: { title: string; lines: string[] }) {
  return (
    <Box>
      <Typography variant="body2" sx={{ fontWeight: 'bold' }}>
        {title}
      </Typography>
      <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
        {lines.map((line) => (
          <Prose key={line} component="li" color="inherit">
            {line}
          </Prose>
        ))}
      </Box>
    </Box>
  )
}

function TypeGuidanceBox({ type }: { type: number }) {
  const guidance = TYPE_GUIDANCE[type]
  if (!guidance) {
    return null
  }
  return (
    <Box
      component="section"
      aria-label="Type guidance"
      sx={{ bgcolor: 'action.hover', borderRadius: 1, px: 2, py: 1, color: 'text.secondary' }}
    >
      <Stack spacing={0.5}>
        <GuidanceList title={`Use ${typeLabel(type)} for`} lines={guidance.useFor} />
        <GuidanceList title="Not for" lines={guidance.notFor} />
      </Stack>
    </Box>
  )
}

function saveErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 422) {
    return `Nothing was saved: ${error.detail ?? 'the templates are not valid.'}`
  }
  const message = error instanceof Error ? error.message : String(error)
  return `Could not save: ${message}`
}

// A pattern is kept without surrounding spaces, which matching ignores anyway. An empty
// description is left out, so the template is stored without the key.
function cleaned(draft: Template): Template {
  const { description, ...rest } = draft
  const coordinates = draft.coordinates.map((coordinate) =>
    coordinate.kind === 'fieldPattern' ? { ...coordinate, pattern: coordinate.pattern.trim() } : coordinate,
  )
  return {
    ...rest,
    name: draft.name.trim(),
    coordinates,
    ...(description?.trim() ? { description: description.trim() } : {}),
  }
}

const DESCRIPTION_HELP = 'Shown in the list and to an agent connected through MCP.'

interface EditorProps {
  initial: Template
  title: string
  others: Template[]
  // Where the template is listed among the others, the end for a new one.
  position: number
  // The vault's custom fields, undefined while the grid loads.
  vaultFields: readonly VaultField[] | undefined
  datatypes: readonly Datatype[]
  error: string | null
  onClose: () => void
  onSave: (template: Template) => void
}

function TemplateEditor({ initial, title, others, position, vaultFields, datatypes, error, onClose, onSave }: EditorProps) {
  const [name, setName] = useState(initial.name)
  const [description, setDescription] = useState(initial.description ?? '')
  const [type, setType] = useState(initial.type)
  const [rows, setRows] = useState<Row[]>(() =>
    initial.coordinates.map((coordinate, key) => ({ key, coordinate, typed: false })),
  )
  const nextKey = useRef(initial.coordinates.length)
  const [added, setAdded] = useState<number | null>(null)
  const [touched, setTouched] = useState(false)
  const titleId = useId()
  const typeLabelId = useId()
  const draft: Template = { name, description, type, coordinates: rows.map((row) => row.coordinate) }
  const problem = draftProblem(draft, others)
  const duplicates = draftDuplicateWarnings(draft, others, position, datatypes)
  const slots = BUILT_IN_SLOTS[type] ?? []

  // The built-in leaves and vault fields no row but this one uses.
  function choicesFor(row: Row): TemplateCoordinate[] {
    const used = new Set(rows.filter((other) => other.key !== row.key).map((other) => coordinateKey(other.coordinate)))
    const leaves: TemplateCoordinate[] = slots.map((path) => ({ kind: 'path', path }))
    const fields: TemplateCoordinate[] = (vaultFields ?? []).map((field) => ({ kind: 'field', ...field }))
    return [...leaves, ...fields].filter((choice) => !used.has(coordinateKey(choice)))
  }

  function changeType(next: number) {
    // Built-in slots belong to one type, custom fields fit every type.
    const allowed = new Set(BUILT_IN_SLOTS[next] ?? [])
    setType(next)
    setRows(rows.filter(({ coordinate }) => coordinate.kind !== 'path' || allowed.has(coordinate.path)))
  }

  function updateRow(key: number, coordinate: TemplateCoordinate, typed: boolean) {
    setRows(rows.map((row) => (row.key === key ? { ...row, coordinate, typed } : row)))
  }

  function addRow() {
    const key = nextKey.current++
    setRows([...rows, { key, coordinate: EMPTY_FIELD, typed: false }])
    setAdded(key)
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    setTouched(true)
    if (problem === null) {
      onSave(cleaned(draft))
    }
  }

  return (
    <Dialog open onClose={onClose} aria-labelledby={titleId} fullWidth maxWidth="md">
      <form onSubmit={submit} noValidate>
        <DialogTitle id={titleId}>{title}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <ExtensionHint />
            <Stack direction="row" spacing={2}>
              <TextField
                autoFocus
                fullWidth
                label="Name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                helperText={duplicates.length > 0 ? duplicates.join(' ') : undefined}
                slotProps={{ formHelperText: { sx: { color: 'warning.main' } }, htmlInput: NO_PASSWORD_MANAGER }}
              />
              <FormControl sx={{ minWidth: 180 }}>
                <InputLabel id={typeLabelId}>Type</InputLabel>
                <Select
                  labelId={typeLabelId}
                  label="Type"
                  value={type}
                  inputProps={NO_PASSWORD_MANAGER}
                  onChange={(event) => changeType(Number(event.target.value))}
                >
                  {Object.keys(ITEM_TYPES).map((option) => (
                    <MenuItem key={option} value={Number(option)}>
                      {typeLabel(Number(option))}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Stack>
            <TextField
              fullWidth
              multiline
              minRows={2}
              maxRows={2}
              label="Description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              helperText={DESCRIPTION_HELP}
              slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
            />
            <TypeGuidanceBox type={draft.type} />
            <Typography variant="subtitle1" component="h3">
              Coordinates
            </Typography>
            {rows.length === 0 && (
              <Prose>
                No coordinates yet. A coordinate is a place in an item where the template expects a value: a built-in
                slot of the item type (such as login.username or notes) or a custom field, picked by its name. An item
                matches the template when it has this type and a value in every required coordinate. Add a coordinate
                below, then make it optional or give it a datatype if you like. A field name pattern such as user_*
                covers several custom fields at once.
              </Prose>
            )}
            {rows.map((row, index) => (
              <CoordinateRow
                key={row.key}
                index={index}
                row={row}
                choices={choicesFor(row)}
                vaultFields={vaultFields}
                datatypes={datatypes}
                autoFocus={row.key === added}
                onChange={(coordinate, typed) => updateRow(row.key, coordinate, typed)}
                onRemove={() => setRows(rows.filter((other) => other.key !== row.key))}
              />
            ))}
            <Box>
              <Button onClick={addRow}>Add coordinate</Button>
            </Box>
            {touched && problem !== null && <Alert severity="error">{problem}</Alert>}
            {error !== null && <Alert severity="error">{error}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained">
            Save
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  )
}

function CoordinateChip({
  coordinate,
  vaultFields,
  datatypes,
}: {
  coordinate: TemplateCoordinate
  vaultFields: readonly VaultField[] | undefined
  datatypes: readonly Datatype[]
}) {
  const unknown = unknownField(coordinate, vaultFields)
  const datatype = datatypes.find((candidate) => candidate.id === coordinate.datatype)
  const marks = [coordinate.kind === 'fieldPattern' && 'pattern', coordinate.optional && 'optional'].filter(Boolean)
  return (
    <Chip
      size="small"
      label={`${datatype ? `${coordinateColumn(coordinate)}: ${datatype.name}` : coordinateColumn(coordinate)}${marks.length > 0 ? ` (${marks.join(', ')})` : ''}`}
      title={unknown ? UNKNOWN_FIELD : undefined}
      data-unknown-field={unknown || undefined}
      sx={unknown ? { bgcolor: (theme) => alpha(theme.palette.warning.main, 0.25) } : undefined}
    />
  )
}

function matchCount(matches: TemplateMatches | undefined, name: string): number {
  return Object.values(matches ?? {}).filter((match) => match?.match === name).length
}

// storedName is the name the template has in the list, null for a new one.
type Editing = { index: number | null; storedName: string | null; template: Template }

const NEW_TEMPLATE: Template = { name: '', type: 2, coordinates: [] }

export function TemplatesTab() {
  const queryClient = useQueryClient()
  const doc = useTemplates()
  const grid = useGridData()
  const matches = useTemplateMatches()
  const datatypes = useDatatypes().data?.datatypes ?? []
  const [editing, setEditing] = useState<Editing | null>(null)
  const [deleting, setDeleting] = useState<number | null>(null)
  // A refused delete, shown on the page since its dialog closed already.
  const [error, setError] = useState<string | null>(null)
  const deleteTitleId = useId()

  const templates = doc.data?.templates ?? []

  // The editor and the delete dialog close at once: the backend only queues
  // the save, so its answer comes quickly and the auto-commit writes it. A
  // refused save opens the editor again with the draft (onRefused).
  async function save(changes: TemplateChange[], onRefused: (message: string) => void) {
    try {
      await saveTemplates(changes)
    } catch (caught) {
      onRefused(saveErrorMessage(caught))
    }
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: templatesQueryKey }),
      queryClient.invalidateQueries({ queryKey: queueQueryKey }),
    ])
  }

  function saveEdit(template: Template) {
    if (!editing) {
      return
    }
    const changes = templateChanges(editing.storedName, template)
    const reopen: Editing = { ...editing, template }
    setEditing(null)
    void save(changes, (message) => {
      setError(message)
      setEditing(reopen)
    })
  }

  function confirmDelete() {
    if (deleting === null) {
      return
    }
    const { name } = templates[deleting]
    setDeleting(null)
    void save([{ op: 'removeTemplate', name }], setError)
  }

  function openEditor(next: Editing) {
    setError(null)
    setEditing(next)
  }

  if (doc.isPending) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', mt: 12 }}>
        <CircularProgress aria-label="Loading templates" />
      </Box>
    )
  }
  if (doc.isError) {
    return (
      <Alert severity="error" sx={{ m: 2 }}>
        Could not load the templates: {doc.error.message}
      </Alert>
    )
  }

  return (
    <Box>
      <Stack direction="row" sx={{ alignItems: 'center', mb: 1 }}>
        <Typography variant="h5" component="h2">
          Templates
        </Typography>
        <Box sx={{ flexGrow: 1, ml: 2 }}>
          {doc.data.saving && <Chip size="small" color="warning" label="Saving" />}
        </Box>
        <Button variant="contained" onClick={() => openEditor({ index: null, storedName: null, template: NEW_TEMPLATE })}>
          New template
        </Button>
      </Stack>
      <Prose sx={{ mb: 2 }}>
        An item matches a template when it has the template's type and a value in every coordinate. The grid shows
        the match of each item, or the closest template with how many of its coordinates are filled.
      </Prose>
      <Box sx={{ mb: 2 }}>
        <ExtensionHint />
      </Box>
      {error !== null && editing === null && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {doc.data.warnings?.map((warning) => (
        <Alert key={warning} severity="warning" sx={{ mb: 2 }}>
          {warning}
        </Alert>
      ))}
      {templates.length === 0 ? (
        <Typography>No templates yet.</Typography>
      ) : (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small" aria-label="Templates">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Description</TableCell>
                <TableCell>Type</TableCell>
                <TableCell>Coordinates</TableCell>
                <TableCell align="right">Matching items</TableCell>
                <TableCell>
                  <Box component="span" sx={VISUALLY_HIDDEN}>
                    Actions
                  </Box>
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {templates.map((template, index) => (
                <TableRow key={template.name}>
                  <TableCell>{template.name}</TableCell>
                  <TableCell sx={{ color: 'text.secondary', whiteSpace: 'normal', wordBreak: 'break-word' }}>
                    {template.description}
                  </TableCell>
                  <TableCell>{typeLabel(template.type)}</TableCell>
                  <TableCell>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                      {template.coordinates.map((coordinate) => (
                        <CoordinateChip
                          key={coordinateKey(coordinate)}
                          coordinate={coordinate}
                          vaultFields={grid.data?.fields}
                          datatypes={datatypes}
                        />
                      ))}
                    </Box>
                  </TableCell>
                  <TableCell align="right">{matches.data ? matchCount(matches.data, template.name) : ''}</TableCell>
                  <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                    <Button size="small" aria-label={`Edit ${template.name}`} onClick={() => openEditor({ index, storedName: template.name, template })}>
                      Edit
                    </Button>
                    <Button
                      size="small"
                      color="error"
                      aria-label={`Delete ${template.name}`}
                      onClick={() => {
                        setError(null)
                        setDeleting(index)
                      }}
                    >
                      Delete
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {editing && (
        <TemplateEditor
          initial={editing.template}
          title={editing.index === null ? 'New template' : `Edit ${editing.template.name}`}
          others={templates.filter((_, index) => index !== editing.index)}
          position={editing.index ?? templates.length}
          vaultFields={grid.data?.fields}
          datatypes={datatypes}
          error={error}
          onClose={() => {
            setError(null)
            setEditing(null)
          }}
          onSave={saveEdit}
        />
      )}
      {deleting !== null && templates[deleting] && (
        <Dialog open onClose={() => setDeleting(null)} aria-labelledby={deleteTitleId}>
          <DialogTitle id={deleteTitleId}>Delete template</DialogTitle>
          <DialogContent>
            <DialogContentText>
              Delete the template “{templates[deleting].name}”? Items keep their values, only the template goes.
            </DialogContentText>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setDeleting(null)}>Cancel</Button>
            <Button color="error" variant="contained" onClick={confirmDelete}>
              Delete
            </Button>
          </DialogActions>
        </Dialog>
      )}
    </Box>
  )
}
