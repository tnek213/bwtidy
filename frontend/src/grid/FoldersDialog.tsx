import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  List,
  ListItem,
  ListItemText,
  Stack,
  TextField,
  Tooltip,
} from '@mui/material'
import { type FormEvent, useId, useState } from 'react'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import type { QueueEntry } from '../api/queue.ts'
import type { VaultFolder } from '../api/grid.ts'
import { deleteFolder, type Operation, renameFolder } from '../api/operations.ts'
import { caseFold } from '../shared/caseFold.ts'

function count(n: number): string {
  return n === 1 ? '1 item' : `${n} items`
}

function countText(folder: VaultFolder): string {
  return folder.trashedItems > 0 ? `${count(folder.items)}, ${folder.trashedItems} in the trash` : count(folder.items)
}

// Why Delete is off for a folder, null when it may go: only an empty one is deleted.
function deleteBlocker(folder: VaultFolder): string | null {
  if (folder.items > 0) {
    return `Move its ${count(folder.items)} out first`
  }
  if (folder.trashedItems > 0) {
    return `Its ${count(folder.trashedItems)} in the trash are still in it`
  }
  return null
}

// What a pending queue entry is about to do with the folder, null when none is queued.
function queuedChange(folder: VaultFolder, pending: readonly QueueEntry[]): string | null {
  for (const entry of pending) {
    const { kind, folderId, newName } = entry.operation
    if (folderId !== folder.id) {
      continue
    }
    if (kind === 'renameFolder') {
      return `Rename to "${String(newName)}" is queued`
    }
    if (kind === 'deleteFolder') {
      return 'Delete is queued'
    }
  }
  return null
}

// Why a new name cannot be saved, null when it can. Another folder's name counts ignoring case,
// like the backend checks it (str.casefold).
function nameProblem(folder: VaultFolder, name: string, folders: readonly VaultFolder[]): string | null {
  if (name.trim() === '') {
    return 'The name is empty'
  }
  if (name === folder.name) {
    return 'The folder has this name already'
  }
  const wanted = caseFold(name)
  const other = folders.find((candidate) => candidate.id !== folder.id && caseFold(candidate.name) === wanted)
  return other ? `Another folder is named ${other.name}` : null
}

interface RenameFormProps {
  folder: VaultFolder
  folders: readonly VaultFolder[]
  onCancel: () => void
  onSave: (name: string) => void
}

function RenameForm({ folder, folders, onCancel, onSave }: RenameFormProps) {
  const [name, setName] = useState(folder.name)
  const problem = nameProblem(folder, name, folders)
  // The unchanged name is no mistake to point out, the field just starts with it.
  const shown = name === folder.name ? null : problem
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
        label={`New name for ${folder.name}`}
        value={name}
        onChange={(event) => setName(event.target.value)}
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

interface FoldersDialogProps {
  folders: readonly VaultFolder[]
  // The pending queue entries, so a folder with a change on its way is not changed again.
  pending: readonly QueueEntry[]
  onClose: () => void
  // Closes the dialog and queues the operation.
  onApply: (operations: Operation[]) => void
}

// Manage folders: every folder with its item count, a rename in place and the delete of an
// empty one. Each action queues one UI entry, like the grid's other edits.
export default function FoldersDialog({ folders, pending, onClose, onApply }: FoldersDialogProps) {
  const titleId = useId()
  const [renaming, setRenaming] = useState<string | null>(null)
  return (
    <Dialog open onClose={onClose} aria-labelledby={titleId} fullWidth maxWidth="sm">
      <DialogTitle id={titleId}>Folders</DialogTitle>
      <DialogContent>
        {folders.length === 0 ? (
          <DialogContentText>The vault has no folders.</DialogContentText>
        ) : (
          <List dense aria-label="Folders">
            {folders.map((folder) => {
              const queued = queuedChange(folder, pending)
              const blocker = deleteBlocker(folder)
              if (renaming === folder.id) {
                return (
                  <ListItem key={folder.id} disableGutters>
                    <RenameForm
                      folder={folder}
                      folders={folders}
                      onCancel={() => setRenaming(null)}
                      onSave={(name) => onApply([renameFolder(folder, name)])}
                    />
                  </ListItem>
                )
              }
              return (
                <ListItem
                  key={folder.id}
                  disableGutters
                  aria-label={folder.name}
                  secondaryAction={
                    <Stack direction="row" spacing={1}>
                      <Button size="small" disabled={queued !== null} onClick={() => setRenaming(folder.id)}>
                        Rename
                      </Button>
                      <Tooltip title={queued === null ? (blocker ?? '') : ''}>
                        <span>
                          <Button
                            size="small"
                            color="error"
                            disabled={queued !== null || blocker !== null}
                            onClick={() => onApply([deleteFolder(folder)])}
                          >
                            Delete
                          </Button>
                        </span>
                      </Tooltip>
                    </Stack>
                  }
                >
                  <ListItemText primary={folder.name} secondary={queued ?? countText(folder)} />
                </ListItem>
              )
            })}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}
