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
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { ApiError } from '../api/api.ts'
import { queueQueryKey } from '../api/queue.ts'
import DatatypeEditor from './DatatypeEditor.tsx'
import { type DatatypeChange, type DatatypeDraft, datatypesQueryKey, saveDatatypes, useDatatypes } from '../api/datatypes.ts'
import { templatesQueryKey, useTemplates } from '../api/templates.ts'
import { coordinatesUsing, datatypeChange, templatesUsing } from './drafts.ts'
import Prose from '../theme/Prose.tsx'
import { VISUALLY_HIDDEN } from '../shared/visuallyHidden.ts'

function saveErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 422) {
    return `Nothing was saved: ${error.detail ?? 'the datatypes do not hold.'}`
  }
  const message = error instanceof Error ? error.message : String(error)
  return `Could not save: ${message}`
}

type Editing = { index: number | null; datatype: DatatypeDraft }

const NEW_DATATYPE: DatatypeDraft = {
  name: '',
  pattern: '',
  description: '',
  ignoreCase: false,
  wholeValue: true,
  examples: [],
  counterExamples: [],
}

export default function DatatypesTab({ onStatistics }: { onStatistics: () => void }) {
  const queryClient = useQueryClient()
  const doc = useDatatypes()
  const templatesDoc = useTemplates()
  const [editing, setEditing] = useState<Editing | null>(null)
  const [deleting, setDeleting] = useState<number | null>(null)
  // A refused delete, shown on the page since its dialog closed already.
  const [error, setError] = useState<string | null>(null)
  const deleteTitleId = useId()

  const datatypes = doc.data?.datatypes ?? []
  const templates = templatesDoc.data?.templates ?? []

  // Like the templates save: the dialogs close at once, as the backend only
  // queues the save and the auto-commit writes it. A refused save opens the
  // editor again with the draft (onRefused).
  async function save(changes: DatatypeChange[], onRefused: (message: string) => void) {
    try {
      await saveDatatypes(changes)
    } catch (caught) {
      onRefused(saveErrorMessage(caught))
    }
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: datatypesQueryKey }),
      queryClient.invalidateQueries({ queryKey: templatesQueryKey }),
      queryClient.invalidateQueries({ queryKey: queueQueryKey }),
    ])
  }

  function saveEdit(datatype: DatatypeDraft) {
    if (!editing) {
      return
    }
    const reopen: Editing = { ...editing, datatype }
    setEditing(null)
    void save([datatypeChange(datatype)], (message) => {
      setError(message)
      setEditing(reopen)
    })
  }

  function askDelete(index: number) {
    const datatype = datatypes[index]
    const users = templatesUsing(templates, datatype.id)
    if (users.length > 0) {
      const named = `${users.length === 1 ? 'the template' : 'the templates'} ${users.join(', ')}`
      setError(`${datatype.name} cannot be deleted: it is used by ${named}. Take it off first.`)
      return
    }
    setError(null)
    setDeleting(index)
  }

  function confirmDelete() {
    if (deleting === null) {
      return
    }
    const { id } = datatypes[deleting]
    setDeleting(null)
    void save([{ op: 'removeDatatype', id }], setError)
  }

  function openEditor(next: Editing) {
    setError(null)
    setEditing(next)
  }

  if (doc.isPending) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', mt: 12 }}>
        <CircularProgress aria-label="Loading datatypes" />
      </Box>
    )
  }
  if (doc.isError) {
    return (
      <Alert severity="error" sx={{ m: 2 }}>
        Could not load the datatypes: {doc.error.message}
      </Alert>
    )
  }

  return (
    <Box>
      <Stack direction="row" sx={{ alignItems: 'center', mb: 1 }}>
        <Typography variant="h5" component="h2">
          Datatypes
        </Typography>
        <Box sx={{ flexGrow: 1, ml: 2 }}>{doc.data.saving && <Chip size="small" color="warning" label="Saving" />}</Box>
        <Button variant="contained" onClick={() => openEditor({ index: null, datatype: NEW_DATATYPE })}>
          New datatype
        </Button>
      </Stack>
      <Prose sx={{ mb: 2 }}>
        A datatype is a named pattern a template coordinate can use. It says how well a value matches format, never
        whether the value is right. bwtidy writes nothing about datatypes into items.
      </Prose>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 2 }}>
        <Prose>How close values and patterns come to the checks' limits:</Prose>
        <Button variant="text" size="small" onClick={onStatistics}>
          Pattern limits and statistics
        </Button>
      </Stack>
      {doc.data.error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Could not read the datatypes: {doc.data.error}
        </Alert>
      )}
      {datatypes.some((datatype) => datatype.broken !== null) && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          A broken datatype counts as no datatype, and no save goes through until it is fixed or deleted.
        </Alert>
      )}
      {error !== null && editing === null && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {datatypes.length === 0 ? (
        <Typography>No datatypes yet.</Typography>
      ) : (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small" aria-label="Datatypes">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Pattern</TableCell>
                <TableCell>Description</TableCell>
                <TableCell align="right">Coordinates using it</TableCell>
                <TableCell>
                  <Box component="span" sx={VISUALLY_HIDDEN}>
                    Actions
                  </Box>
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {datatypes.map((datatype, index) => (
                <TableRow key={`${index}:${datatype.id}`}>
                  <TableCell>
                    {datatype.name}
                    {datatype.broken !== null && (
                      <>
                        <Chip size="small" color="error" label="Broken" sx={{ ml: 1 }} />
                        <Typography variant="body2" color="error">
                          {datatype.broken}
                        </Typography>
                      </>
                    )}
                  </TableCell>
                  <TableCell sx={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{datatype.pattern}</TableCell>
                  <TableCell>{datatype.description}</TableCell>
                  <TableCell align="right">{coordinatesUsing(templates, datatype.id)}</TableCell>
                  <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                    <Button size="small" aria-label={`Edit ${datatype.name}`} onClick={() => openEditor({ index, datatype })}>
                      Edit
                    </Button>
                    <Button size="small" color="error" aria-label={`Delete ${datatype.name}`} onClick={() => askDelete(index)}>
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
        <DatatypeEditor
          initial={editing.datatype}
          title={editing.index === null ? 'New datatype' : `Edit ${editing.datatype.name}`}
          otherNames={datatypes.filter((_, index) => index !== editing.index).map((datatype) => datatype.name)}
          error={error}
          onClose={() => {
            setError(null)
            setEditing(null)
          }}
          onSave={saveEdit}
        />
      )}
      {deleting !== null && datatypes[deleting] && (
        <Dialog open onClose={() => setDeleting(null)} aria-labelledby={deleteTitleId}>
          <DialogTitle id={deleteTitleId}>Delete datatype</DialogTitle>
          <DialogContent>
            <DialogContentText>
              Delete the datatype “{datatypes[deleting].name}”? Items keep their values, only the datatype goes.
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
