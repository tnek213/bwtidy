import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import { type FormEvent, useId, useState } from 'react'
import type { GridRow } from '../api/grid.ts'
import type { Operation } from '../api/operations.ts'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import type { Template, TemplateCoordinate, TemplateMatch } from '../api/templates.ts'
import {
  coordinateColumn,
  FIELD_TYPE_LABELS,
  isSecretCoordinate,
  ITEM_TYPES,
  TYPE_LABELS,
  typeLabel,
} from '../shared/templateRules.ts'
import {
  applyTemplate,
  canMapOnto,
  defaultMapping,
  fillLabel,
  type Mapping,
  type SourceColumn,
  sourceColumns,
} from './matches.ts'
import { formatValue } from '../shared/values.ts'
import { rowName } from './labels.ts'

interface ConvertDialogProps {
  row: GridRow
  templates: Template[]
  match: TemplateMatch | undefined
  onClose: () => void
  onApply: (operations: Operation[]) => void
}

function coordinateKind(coordinate: TemplateCoordinate): string {
  if (coordinate.kind === 'path') {
    return 'Built-in'
  }
  const label = FIELD_TYPE_LABELS[coordinate.fieldType]
  return coordinate.kind === 'fieldPattern' ? `${label}, name pattern` : label
}

function sourceLabel(source: SourceColumn, row: GridRow): string {
  const value = formatValue(row.cells[source.column])
  const preview = value.length > 40 ? `${value.slice(0, 40)}…` : value
  return `${source.column}: ${preview.replace(/\s+/g, ' ')}`
}

// Why a mapping row cannot be sent, or null. A field taken by a pattern stays as it is, its type too.
function rowProblem(target: TemplateCoordinate, source: SourceColumn | undefined, mapping: Mapping): string | null {
  if (!source) {
    return null
  }
  if (source.secret && target.kind !== 'fieldPattern' && !isSecretCoordinate(target)) {
    return 'A secret value cannot go into a coordinate that is not secret.'
  }
  const uses = Object.values(mapping).filter((column) => column === source.column).length
  return uses > 1 ? 'This value is mapped to more than one coordinate.' : null
}

export default function ConvertDialog({ row, templates, match, onClose, onApply }: ConvertDialogProps) {
  const sources = sourceColumns(row)
  const [templateName, setTemplateName] = useState('')
  const [mapping, setMapping] = useState<Mapping>({})
  const titleId = useId()
  const templateLabelId = useId()
  const template = templates.find((candidate) => candidate.name === templateName)
  const currentType = typeof row.cells.type === 'string' ? row.cells.type : ''
  const typeChanges = template !== undefined && ITEM_TYPES[template.type] !== currentType
  const sourceByColumn = new Map(sources.map((source) => [source.column, source]))
  const problems = (template?.coordinates ?? []).map((coordinate) =>
    rowProblem(coordinate, sourceByColumn.get(mapping[coordinateColumn(coordinate)] ?? ''), mapping),
  )
  const canSubmit = template !== undefined && problems.every((problem) => problem === null)

  function chooseTemplate(name: string) {
    setTemplateName(name)
    const chosen = templates.find((candidate) => candidate.name === name)
    setMapping(chosen ? defaultMapping(chosen, sources) : {})
  }

  function fill(name: string): string {
    const score = match?.scores.find((candidate) => candidate.template === name)
    return score ? ` (${fillLabel(score)} filled)` : ''
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (template && canSubmit) {
      onApply([applyTemplate(row, template, mapping)])
    }
  }

  return (
    <Dialog open onClose={onClose} aria-labelledby={titleId} fullWidth maxWidth="md">
      <form onSubmit={submit} noValidate>
        <DialogTitle id={titleId}>Convert {rowName(row)} to a template</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {templates.length === 0 ? (
              <Alert severity="info">There are no templates yet. Create one on the Types page.</Alert>
            ) : (
              <FormControl fullWidth>
                <InputLabel id={templateLabelId}>Template</InputLabel>
                <Select
                  labelId={templateLabelId}
                  label="Template"
                  value={templateName}
                  inputProps={NO_PASSWORD_MANAGER}
                  onChange={(event) => chooseTemplate(event.target.value)}
                >
                  {templates.map((candidate) => (
                    <MenuItem key={candidate.name} value={candidate.name}>
                      {candidate.name}, {typeLabel(candidate.type)}
                      {fill(candidate.name)}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
            {template && typeChanges && (
              <Alert severity="warning">
                The template is a {typeLabel(template.type)}, the item is a {TYPE_LABELS[currentType] ?? currentType}.
                A new item of the new type is created with the mapped values, and the old item goes to the trash.
                Passkeys and password history stay with the old item (in the trash and in the backup). Unmapped
                built-in values become custom fields named after their key.
              </Alert>
            )}
            {template && (
              <>
                <DialogContentText>
                  Choose which of the item's values goes into each coordinate of the template. A mapped value
                  moves. Coordinates left empty are created without a value, and values not mapped stay on the item.
                  {template.coordinates.some((coordinate) => coordinate.kind === 'fieldPattern') &&
                    ' A field name pattern takes a field whose name fits it, which keeps its name, and creates no field when left empty.'}
                </DialogContentText>
                <Table size="small" aria-label="Mapping">
                  <TableHead>
                    <TableRow>
                      <TableCell>Template coordinate</TableCell>
                      <TableCell>Kind</TableCell>
                      <TableCell>Value from the item</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {template.coordinates.map((coordinate, index) => {
                      const target = coordinateColumn(coordinate)
                      const problem = problems[index]
                      return (
                        <TableRow key={target}>
                          <TableCell sx={{ fontFamily: 'monospace' }}>{target}</TableCell>
                          <TableCell>{coordinateKind(coordinate)}</TableCell>
                          <TableCell>
                            <FormControl size="small" fullWidth error={problem !== null}>
                              <Select
                                inputProps={{ ...NO_PASSWORD_MANAGER, 'aria-label': `Value for ${target}` }}
                                value={mapping[target] ?? ''}
                                displayEmpty
                                renderValue={(value) => {
                                  const source = sourceByColumn.get(value)
                                  return source ? sourceLabel(source, row) : 'Leave empty'
                                }}
                                onChange={(event) => setMapping({ ...mapping, [target]: event.target.value })}
                              >
                                <MenuItem value="">Leave empty</MenuItem>
                                {sources.filter((source) => canMapOnto(coordinate, source.column)).map((source) => (
                                  <MenuItem key={source.column} value={source.column}>
                                    {sourceLabel(source, row)}
                                  </MenuItem>
                                ))}
                              </Select>
                            </FormControl>
                            {problem !== null && (
                              <Typography variant="caption" color="error" role="alert">
                                {problem}
                              </Typography>
                            )}
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={!canSubmit}>
            {typeChanges ? 'Convert' : 'Apply template'}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  )
}
