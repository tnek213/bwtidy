import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  List,
  ListItem,
  ListItemText,
  TextField,
} from '@mui/material'
import { type FormEvent, useId, useState } from 'react'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import type { QueueEntry } from '../api/queue.ts'
import type { GridAttachment, GridRow } from '../api/grid.ts'
import { type Operation, renameAttachment } from '../api/operations.ts'
import { rowName } from './labels.ts'
import { caseFold } from '../shared/caseFold.ts'

// The rename of the attachment a pending queue entry is about to write, null when none is queued.
function queuedRename(attachment: GridAttachment, pending: readonly QueueEntry[]): string | null {
  for (const entry of pending) {
    const { kind, attachmentId, fileName } = entry.operation
    if (kind === 'renameAttachment' && attachmentId === attachment.id) {
      return `Rename to "${String(fileName)}" is queued`
    }
  }
  return null
}

// Why a new file name cannot be saved, null when it can. The rules the backend applies
// (check_attachment_rename), another attachment's name counted ignoring case (str.casefold).
function fileNameProblem(attachment: GridAttachment, name: string, attachments: readonly GridAttachment[]): string | null {
  if (name.trim() === '') {
    return 'The name is empty'
  }
  if (name.includes('/') || name.includes('\\')) {
    return 'A file name may not hold a slash or a backslash'
  }
  if (name === attachment.fileName) {
    return 'The attachment has this name already'
  }
  const wanted = caseFold(name)
  const other = attachments.find((candidate) => candidate.id !== attachment.id && caseFold(candidate.fileName) === wanted)
  return other ? `Another attachment is named ${other.fileName}` : null
}

interface RenameFormProps {
  attachment: GridAttachment
  attachments: readonly GridAttachment[]
  onCancel: () => void
  onSave: (name: string) => void
}

function RenameForm({ attachment, attachments, onCancel, onSave }: RenameFormProps) {
  const [name, setName] = useState(attachment.fileName)
  const [edited, setEdited] = useState(false)
  const problem = fileNameProblem(attachment, name, attachments)
  // The field starts with the name it has now, which is no mistake to point out until the user types.
  const shown = edited ? problem : null
  function submit(event: FormEvent) {
    event.preventDefault()
    if (problem === null) {
      onSave(name)
    }
  }
  return (
    <Box component="form" onSubmit={submit} noValidate sx={{ display: 'flex', gap: 1, alignItems: 'flex-start', width: '100%' }}>
      <TextField
        autoFocus
        size="small"
        label={`New name for ${attachment.fileName}`}
        value={name}
        onChange={(event) => {
          setName(event.target.value)
          setEdited(true)
        }}
        error={shown !== null}
        helperText={shown ?? (name !== name.trim() ? 'The name starts or ends with a space.' : undefined)}
        sx={{ flexGrow: 1 }}
        slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
      />
      <Button type="submit" variant="contained" disabled={problem !== null}>
        Save
      </Button>
      <Button onClick={onCancel}>Cancel</Button>
    </Box>
  )
}

interface AttachmentsDialogProps {
  row: GridRow
  // The pending queue entries, so an attachment with a rename on its way is not renamed again.
  pending: readonly QueueEntry[]
  onClose: () => void
  // Closes the dialog and queues the operation.
  onApply: (operations: Operation[]) => void
}

// The attachments of one item by file name, each renamed in place. A save queues one UI
// renameAttachment. The dialog knows only the ids and names, never a url, size or contents.
export default function AttachmentsDialog({ row, pending, onClose, onApply }: AttachmentsDialogProps) {
  const titleId = useId()
  const [renaming, setRenaming] = useState<string | null>(null)
  const attachments = row.attachments ?? []
  return (
    <Dialog open onClose={onClose} aria-labelledby={titleId} fullWidth maxWidth="sm">
      <DialogTitle id={titleId}>Attachments of {rowName(row)}</DialogTitle>
      <DialogContent>
        <List dense aria-label="Attachments">
          {attachments.map((attachment) => {
            if (renaming === attachment.id) {
              return (
                <ListItem key={attachment.id} disableGutters>
                  <RenameForm
                    attachment={attachment}
                    attachments={attachments}
                    onCancel={() => setRenaming(null)}
                    onSave={(name) => onApply([renameAttachment(row, attachment, name)])}
                  />
                </ListItem>
              )
            }
            const queued = queuedRename(attachment, pending)
            return (
              <ListItem
                key={attachment.id}
                disableGutters
                aria-label={attachment.fileName}
                secondaryAction={
                  <Button size="small" disabled={queued !== null} onClick={() => setRenaming(attachment.id)}>
                    Rename
                  </Button>
                }
              >
                <ListItemText primary={attachment.fileName} secondary={queued} />
              </ListItem>
            )
          })}
        </List>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}
