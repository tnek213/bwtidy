import {
  Alert,
  Checkbox,
  DialogContentText,
  FormControl,
  FormControlLabel,
  FormGroup,
  FormHelperText,
  FormLabel,
  InputLabel,
  ListSubheader,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { Fragment, useCallback, useId, useLayoutEffect, useState } from 'react'
import { ApiError } from '../api/api.ts'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import { queueOperations, queueQueryKey } from '../api/queue.ts'
import { useDatatypes } from '../api/datatypes.ts'
import { toggled } from '../shared/sets.ts'
import { useGenerator } from '../api/generator.ts'
import type { Template, TemplateCoordinate } from '../api/templates.ts'
import { fieldColumn } from '../shared/coordinates.ts'
import { organizationOption } from './dialogs/collectionChoices.tsx'
import { FolderPicker, folderNames, NEW_FOLDER } from './dialogs/FolderPicker.tsx'
import { FormDialog } from '../shared/FormDialog.tsx'
import { organizationCollections } from './editing.ts'
import { type GridRow, type VaultOrganization } from '../api/grid.ts'
import { liveFormatText, useFormatVerdict } from './formats.ts'
import { FIRST_OPERATION, type LeafValue, type Operation } from '../api/operations.ts'
import { BUILT_IN_SLOTS, coordinateColumn, foldedName, isSecretCoordinate, typeLabel } from '../shared/templateRules.ts'
import { fitsPattern, patternPrefix } from './matches.ts'
import { describeOptions } from './ownGenerator.ts'

const LOGIN = 1
const GENERATED_COLUMN = 'login.password'

// Bitwarden's item types in the order its own clients offer them.
const NEW_ITEM_TYPES = [1, 2, 3, 4, 5]

const TYPE_PREFIX = 'type:'
const TEMPLATE_PREFIX = 'template:'

export const SECRETS_LEFT_EMPTY = 'Secret fields left empty go on your To fill in list.'
export const GENERATED_PASSWORD = 'The password is generated and shown only when you reveal it in the grid.'

interface NewItemField {
  column: string
  // 0 text, 2 boolean. A secret field is always text.
  fieldType: 0 | 2
  // The template's datatype id for the field, null without one and for a raw type.
  datatype: string | null
  // A template's text field cannot be left blank, unless its datatype matches blank or it is optional.
  required: boolean
  // An optional field of the template, which its label says.
  optional: boolean
  // Typed hidden and sent in secretValues, never in values.
  secret: boolean
  // A field name pattern of the template: the user names the field, a name that fits it.
  pattern: string | null
}

// The fields a new item of a raw type or a template is offered with, secret ones included. A
// template keeps its order, a raw type has its built-in slots and then the notes. The name has an
// input of its own.
export function newItemFields(type: number, template: Template | null): NewItemField[] {
  const coordinates: TemplateCoordinate[] = template
    ? template.coordinates
    : [...(BUILT_IN_SLOTS[type] ?? []).map((path) => ({ kind: 'path' as const, path })), { kind: 'path', path: 'notes' }]
  return coordinates
    .filter((coordinate) => coordinateColumn(coordinate) !== 'name')
    .map((coordinate) => {
      const boolean = coordinate.kind === 'field' && coordinate.fieldType === 2
      const optional = template !== null && coordinate.optional === true
      return {
        column: coordinateColumn(coordinate),
        fieldType: boolean ? 2 : 0,
        datatype: boolean ? null : (coordinate.datatype ?? null),
        required: template !== null && !boolean && !optional,
        optional,
        secret: isSecretCoordinate(coordinate),
        pattern: coordinate.kind === 'fieldPattern' ? coordinate.pattern : null,
      }
    })
}

function fieldLabel(field: NewItemField): string {
  return field.optional ? `${field.column} (optional)` : field.column
}

// Why the name typed for a pattern's field cannot be sent, or null. It must fit the pattern and
// be no field the template names otherwise (others: the folded names of the other fields).
function patternNameProblem(pattern: string, name: string, others: ReadonlySet<string>): string | null {
  if (!fitsPattern(pattern, name)) {
    return `The name must fit ${pattern.trim()}`
  }
  if (others.has(foldedName(name))) {
    return `${fieldColumn(name.trim())} is another field of the template`
  }
  return null
}

const NAME_SUFFIX = ':name'

interface PatternNameFieldProps {
  field: NewItemField & { pattern: string }
  name: string
  onChange: (name: string) => void
  // The value is left out, so the name does not matter.
  unused: boolean
  others: ReadonlySet<string>
  onBlocked: (key: string, blocked: boolean) => void
}

function PatternNameField({ field, name, onChange, unused, others, onBlocked }: PatternNameFieldProps) {
  const problem = unused ? null : patternNameProblem(field.pattern, name, others)
  const blockKey = `${field.column}${NAME_SUFFIX}`
  useLayoutEffect(() => {
    onBlocked(blockKey, problem !== null)
    return () => onBlocked(blockKey, false)
  }, [problem, blockKey, onBlocked])
  return (
    <TextField
      label={`Field name for ${field.column}`}
      value={name}
      error={problem !== null}
      helperText={problem ?? `A name that fits ${field.pattern.trim()}`}
      onChange={(event) => onChange(event.target.value)}
      slotProps={{ htmlInput: { ...NO_PASSWORD_MANAGER, spellCheck: false } }}
    />
  )
}

// The datatype of the template's name coordinate, which the Name input checks.
function nameDatatype(template: Template | null): string | null {
  return template?.coordinates.find((coordinate) => coordinateColumn(coordinate) === 'name')?.datatype ?? null
}

function splitUris(input: string): string[] {
  return input
    .split('\n')
    .map((uri) => uri.trim())
    .filter(Boolean)
}

// What a text input sends, one value for a text and one for each URI of login.uris. No value
// is a blank input. The name is sent trimmed.
function checkedValues(column: string, input: string): string[] {
  if (column === 'login.uris') {
    return splitUris(input)
  }
  if (column === 'name') {
    return input.trim() === '' ? [] : [input.trim()]
  }
  return input.trim() === '' ? [] : [input]
}

const REQUIRED_BY_TEMPLATE = 'Required by the template'

interface CheckedTextFieldProps {
  label: string
  column: string
  value: string
  onChange: (value: string) => void
  datatypeId: string | null
  // The template needs a value, unless blankAllowed.
  required: boolean
  // The datatype matches blank.
  blankAllowed: boolean
  // Name is required for every start but is blocked by the dialog itself when blank.
  noBlankBlock?: boolean
  multiline?: boolean
  minRows?: number
  // Typed hidden, shown as typed when shown.
  secret?: boolean
  shown?: boolean
  baseHelper?: string
  // Called with blockKey and whether this input stops Create, and with false when it goes away.
  blockKey: string
  onBlocked: (key: string, blocked: boolean) => void
}

function CheckedTextField({
  label,
  column,
  value,
  onChange,
  datatypeId,
  required,
  blankAllowed,
  noBlankBlock,
  multiline,
  minRows,
  secret,
  shown,
  baseHelper,
  blockKey,
  onBlocked,
}: CheckedTextFieldProps) {
  const values = checkedValues(column, value)
  const blank = values.length === 0
  const verdict = useFormatVerdict(datatypeId, values)
  const needed = required && !blankAllowed
  const blocked = blank ? needed && !noBlankBlock : verdict.state === 'pending' || verdict.state === 'off'
  useLayoutEffect(() => {
    onBlocked(blockKey, blocked)
    return () => onBlocked(blockKey, false)
  }, [blocked, blockKey, onBlocked])
  let extra: string | undefined
  if (verdict.state === 'off' && verdict.off) {
    extra = liveFormatText(verdict.off)
  } else if (verdict.state === 'matches') {
    extra = liveFormatText({ score: 1, firstEdit: null, expected: null, tooLong: false })
  } else if (blank && needed && !noBlankBlock) {
    extra = REQUIRED_BY_TEMPLATE
  }
  const helper = [baseHelper, extra].filter(Boolean).join(' ')
  return (
    <TextField
      required={noBlankBlock || needed}
      label={label}
      type="text"
      multiline={multiline}
      minRows={minRows}
      error={verdict.state === 'off'}
      helperText={helper === '' ? undefined : helper}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      slotProps={{
        htmlInput: secret
          ? {
              ...NO_PASSWORD_MANAGER,
              spellCheck: false,
              autoCapitalize: 'off',
              autoCorrect: 'off',
              style: shown ? undefined : { WebkitTextSecurity: 'disc' },
            }
          : NO_PASSWORD_MANAGER,
      }}
    />
  )
}

function valueOf(field: NewItemField, input: string | boolean | undefined): LeafValue | undefined {
  if (field.fieldType === 2) {
    return input === true ? true : undefined
  }
  if (typeof input !== 'string') {
    return undefined
  }
  if (field.column === 'login.uris') {
    const uris = splitUris(input)
    return uris.length > 0 ? uris : undefined
  }
  return input.trim() === '' ? undefined : input
}

function reasonOf(error: unknown): string {
  if (error instanceof ApiError && error.detail) {
    return error.detail.replace(FIRST_OPERATION, '')
  }
  return error instanceof Error ? error.message : String(error)
}

interface NewItemDialogProps {
  rows: GridRow[]
  organizations?: readonly VaultOrganization[]
  templates: readonly Template[]
  onClose: () => void
  // After the backend queued the item, before the dialog closes.
  onQueued?: () => void
}

// Queues a createItem like every UI edit. A refusal stays in the dialog and nothing is queued.
export default function NewItemDialog({ rows, organizations: vaultOrganizations, templates, onClose, onQueued }: NewItemDialogProps) {
  const queryClient = useQueryClient()
  const startLabelId = useId()
  const organizationLabelId = useId()
  const [start, setStart] = useState('')
  const [name, setName] = useState('')
  const [folder, setFolder] = useState('')
  const [newFolder, setNewFolder] = useState('')
  const [organization, setOrganization] = useState('')
  const [chosen, setChosen] = useState<Set<string>>(() => new Set())
  const [inputs, setInputs] = useState<Record<string, string | boolean>>({})
  // The field names typed for the template's patterns, by the pattern's column.
  const [names, setNames] = useState<Record<string, string>>({})
  const [generate, setGenerate] = useState(true)
  const [shown, setShown] = useState(false)
  const [sending, setSending] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const template = start.startsWith(TEMPLATE_PREFIX)
    ? (templates.find((candidate) => candidate.name === start.slice(TEMPLATE_PREFIX.length)) ?? null)
    : null
  const type = template ? template.type : start.startsWith(TYPE_PREFIX) ? Number(start.slice(TYPE_PREFIX.length)) : null
  const isLogin = type === LOGIN
  // A generated password has no input.
  const fields = (type === null ? [] : newItemFields(type, template)).filter(
    (field) => !(isLogin && generate && field.column === GENERATED_COLUMN),
  )
  const hasSecretInput = fields.some((field) => field.secret)
  // A raw type's secret fields may stay blank and go on the list, a template's are required or optional.
  const leftEmpty = template === null && hasSecretInput
  const generated = isLogin && generate
  const folderName = folder === NEW_FOLDER ? newFolder.trim() : folder
  const byOrganization = organizationCollections(rows, vaultOrganizations)
  const organizations = [...byOrganization.keys()]
  const collections = byOrganization.get(organization) ?? []
  const generator = useGenerator(isLogin)
  const [blockedBy, setBlockedBy] = useState<ReadonlySet<string>>(() => new Set())
  const reportBlocked = useCallback((key: string, blocked: boolean) => {
    setBlockedBy((current) => {
      if (current.has(key) === blocked) {
        return current
      }
      return toggled(current, key, blocked)
    })
  }, [])
  const wantsDatatypes = template !== null && (nameDatatype(template) !== null || fields.some((field) => field.datatype !== null))
  const datatypes = useDatatypes(wantsDatatypes)
  const listed = new Map((datatypes.data?.datatypes ?? []).map((datatype) => [datatype.id, datatype]))
  const loading = wantsDatatypes && datatypes.isPending
  // A datatype the list does not name counts as none. Until it is read the check may go on.
  function datatypeOf(id: string | null): string | null {
    return id !== null && (loading || listed.has(id)) ? id : null
  }
  function blankAllowed(id: string | null): boolean {
    return id !== null && listed.get(id)?.shortest === 0
  }
  const canSubmit =
    !sending &&
    type !== null &&
    name.trim() !== '' &&
    (folder !== NEW_FOLDER || folderName !== '') &&
    (organization === '' || chosen.size > 0) &&
    blockedBy.size === 0

  function nameFor(field: NewItemField): string {
    return names[field.column] ?? patternPrefix(field.pattern ?? '')
  }

  // The column a field's value goes to: its own, or the one named for a pattern.
  function targetOf(field: NewItemField): string {
    return field.pattern === null ? field.column : fieldColumn(nameFor(field).trim())
  }

  // The folded names of the template's fields other than the one of this pattern.
  function otherNames(field: NewItemField): Set<string> {
    const named = (template?.coordinates ?? []).flatMap((coordinate) =>
      coordinate.kind === 'field' ? [foldedName(coordinate.name)] : [],
    )
    const typed = fields
      .filter((other) => other.pattern !== null && other.column !== field.column)
      .map((other) => foldedName(nameFor(other)))
    return new Set([...named, ...typed])
  }

  function toggle(collection: string, on: boolean) {
    setChosen(toggled(chosen, collection, on))
  }

  function operation(): Operation {
    const values: Record<string, LeafValue> = {}
    const secretValues: Record<string, LeafValue> = {}
    for (const field of fields) {
      const value = valueOf(field, inputs[field.column])
      if (value !== undefined) {
        const target = field.secret ? secretValues : values
        target[targetOf(field)] = value
      }
    }
    return {
      kind: 'createItem',
      name: name.trim(),
      ...(template ? { template: template.name } : { type: type ?? undefined }),
      ...(folderName !== '' && { folderName }),
      ...(organization !== '' && {
        organizationName: organization,
        collectionNames: collections.filter((collection) => chosen.has(collection)),
      }),
      ...(Object.keys(values).length > 0 && { values }),
      ...(Object.keys(secretValues).length > 0 && { secretValues }),
      ...(isLogin && generate && { generatePassword: true }),
    }
  }

  async function create() {
    setSending(true)
    setRefusal(null)
    try {
      await queueOperations([operation()])
    } catch (error) {
      setRefusal(reasonOf(error))
      setSending(false)
      return
    }
    onQueued?.()
    // The pending list and the auto-commit status, so the app bar counts the new item at once.
    void queryClient.invalidateQueries({ queryKey: queueQueryKey })
    onClose()
  }

  return (
    <FormDialog title="New item" submitLabel="Create" canSubmit={canSubmit} onClose={onClose} onSubmit={() => void create()}>
      <FormControl fullWidth required>
        <InputLabel id={startLabelId}>Start from</InputLabel>
        <Select
          labelId={startLabelId}
          label="Start from"
          value={start}
          inputProps={NO_PASSWORD_MANAGER}
          onChange={(event) => {
            setStart(event.target.value)
            setNames({})
            setRefusal(null)
          }}
        >
          <ListSubheader>Templates</ListSubheader>
          {templates.length === 0 && <MenuItem disabled>No templates yet</MenuItem>}
          {templates.map((candidate) => (
            <MenuItem key={`${TEMPLATE_PREFIX}${candidate.name}`} value={`${TEMPLATE_PREFIX}${candidate.name}`}>
              {`${candidate.name} (${typeLabel(candidate.type)})`}
            </MenuItem>
          ))}
          <ListSubheader>Types</ListSubheader>
          {NEW_ITEM_TYPES.map((candidate) => (
            <MenuItem key={`${TYPE_PREFIX}${candidate}`} value={`${TYPE_PREFIX}${candidate}`}>
              {typeLabel(candidate)}
            </MenuItem>
          ))}
        </Select>
        {template?.description && <FormHelperText>{template.description}</FormHelperText>}
      </FormControl>
      <CheckedTextField
        key={`${start}:name`}
        label="Name"
        column="name"
        value={name}
        onChange={setName}
        datatypeId={datatypeOf(nameDatatype(template))}
        required
        blankAllowed={false}
        noBlankBlock
        blockKey="name"
        onBlocked={reportBlocked}
      />
      <FolderPicker folders={folderNames(rows)} value={folder} onChange={setFolder} />
      {folder === NEW_FOLDER && (
        <TextField
          label="New folder name"
          value={newFolder}
          onChange={(event) => setNewFolder(event.target.value)}
          slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
        />
      )}
      {organizations.length > 0 && (
        <FormControl fullWidth>
          <InputLabel id={organizationLabelId} shrink>
            Organization
          </InputLabel>
          <Select
            labelId={organizationLabelId}
            label="Organization"
            value={organization}
            notched
            displayEmpty
            inputProps={NO_PASSWORD_MANAGER}
            renderValue={(value) => (value === '' ? 'Personal vault' : value)}
            onChange={(event) => {
              setOrganization(event.target.value)
              setChosen(new Set())
            }}
          >
            <MenuItem value="">Personal vault</MenuItem>
            {organizations.map((candidate) => organizationOption(candidate, byOrganization.get(candidate) ?? []))}
          </Select>
        </FormControl>
      )}
      {organization !== '' && (
        <FormControl component="fieldset">
          <FormLabel component="legend">Collections</FormLabel>
          <FormGroup>
            {collections.map((collection) => (
              <FormControlLabel
                key={collection}
                label={collection}
                control={
                  <Checkbox checked={chosen.has(collection)} onChange={(event) => toggle(collection, event.target.checked)} />
                }
              />
            ))}
          </FormGroup>
          {chosen.size === 0 && (
            <FormHelperText>Pick at least one collection. Bitwarden needs one for an item in an organization.</FormHelperText>
          )}
        </FormControl>
      )}
      {fields.map((field) =>
        field.fieldType === 2 ? (
          <FormControlLabel
            key={field.column}
            label={fieldLabel(field)}
            control={
              <Checkbox
                checked={inputs[field.column] === true}
                onChange={(event) => setInputs({ ...inputs, [field.column]: event.target.checked })}
              />
            }
          />
        ) : (
          <Fragment key={`${start}:${field.column}`}>
            {field.pattern !== null && (
              <PatternNameField
                field={{ ...field, pattern: field.pattern }}
                name={nameFor(field)}
                onChange={(name) => setNames((current) => ({ ...current, [field.column]: name }))}
                unused={!field.required && valueOf(field, inputs[field.column]) === undefined}
                others={otherNames(field)}
                onBlocked={reportBlocked}
              />
            )}
            <CheckedTextField
              label={fieldLabel(field)}
              column={field.column}
              multiline={field.column === 'notes' || field.column === 'login.uris' || field.column === 'sshKey.privateKey'}
              minRows={field.column === 'notes' ? 3 : field.column === 'sshKey.privateKey' ? 4 : undefined}
              secret={field.secret}
              shown={shown}
              baseHelper={field.column === 'login.uris' ? 'One URI per line.' : undefined}
              value={typeof inputs[field.column] === 'string' ? String(inputs[field.column]) : ''}
              onChange={(value) => setInputs((current) => ({ ...current, [field.column]: value }))}
              datatypeId={datatypeOf(field.datatype)}
              required={field.required}
              blankAllowed={blankAllowed(field.datatype)}
              blockKey={field.column}
              onBlocked={reportBlocked}
            />
          </Fragment>
        ),
      )}
      {hasSecretInput && (
        <FormControlLabel
          control={<Checkbox checked={shown} onChange={(event) => setShown(event.target.checked)} />}
          label="Show what I type"
        />
      )}
      {isLogin && (
        <Stack direction="row" sx={{ alignItems: 'center' }}>
          <FormControlLabel
            control={<Checkbox checked={generate} onChange={(event) => setGenerate(event.target.checked)} />}
            label="Generate a password"
          />
          {generator.data && (
            <Typography variant="body2" color="text.secondary" data-testid="generator-default">
              {describeOptions(generator.data.default)}
            </Typography>
          )}
        </Stack>
      )}
      {(leftEmpty || generated) && (
        <DialogContentText>{[leftEmpty && SECRETS_LEFT_EMPTY, generated && GENERATED_PASSWORD].filter(Boolean).join(' ')}</DialogContentText>
      )}
      {refusal !== null && <Alert severity="error">Nothing was queued: {refusal}</Alert>}
    </FormDialog>
  )
}
