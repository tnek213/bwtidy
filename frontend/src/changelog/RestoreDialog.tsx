import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  List,
  ListItem,
  ListItemText,
} from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { ApiError } from '../api/api.ts'
import { queueOperations, queueQueryKey } from '../api/queue.ts'
import { type ChangeLogEntry, type RestorePlan, useRestorePlan } from '../api/changeLog.ts'

function reasonOf(error: unknown, folder: boolean): string {
  if (error instanceof ApiError && error.status === 409) {
    const what = folder ? 'The folder of this change was' : 'Items of this change were'
    return `${what} changed elsewhere since the vault was loaded. Open the dialog again to restore what the vault holds now.`
  }
  if (error instanceof ApiError && error.detail) {
    return error.detail
  }
  return error instanceof Error ? error.message : String(error)
}

// What the restore does as a whole. An attachment rename's restore renames the copy back and leaves
// the rest of the item as it is.
function intro(plan: RestorePlan): string {
  if (plan.operation.kind === 'renameAttachment') {
    return `Give the attachment back the name it had before this change, from backup ${plan.backup}. The rest of the item stays as it is.`
  }
  const what = plan.folder ? 'Put this folder back as it was' : 'Put these items back as they were'
  return `${what} before this change, from backup ${plan.backup}.`
}

// Shows what restoring a change log row does per item, or with its folder (GET
// /api/restore-plan), and queues the restoreItems or restoreFolder like every UI edit. A refusal
// stays in the dialog and nothing is queued.
export default function RestoreDialog({ entry, onClose }: { entry: ChangeLogEntry; onClose: () => void }) {
  const titleId = useId()
  const queryClient = useQueryClient()
  const plan = useRestorePlan(entry.id)
  const [sending, setSending] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const folder = entry.folderId != null

  async function restore() {
    if (!plan.data) {
      return
    }
    setSending(true)
    setRefusal(null)
    try {
      await queueOperations([plan.data.operation])
    } catch (error) {
      setRefusal(reasonOf(error, folder))
      setSending(false)
      return
    }
    // The pending list and the auto-commit status, so the app bar counts the restore at once.
    void queryClient.invalidateQueries({ queryKey: queueQueryKey })
    onClose()
  }

  return (
    <Dialog open onClose={onClose} aria-labelledby={titleId} fullWidth maxWidth="sm">
      <DialogTitle id={titleId}>Restore</DialogTitle>
      <DialogContent>
        {plan.isPending && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress aria-label="Loading the restore plan" />
          </Box>
        )}
        {plan.isError && (
          <Alert severity="error">
            This change cannot be restored: {reasonOf(plan.error, folder)}
          </Alert>
        )}
        {plan.data && (
          <>
            <DialogContentText>{intro(plan.data)}</DialogContentText>
            <List dense aria-label="What the restore does">
              {plan.data.folder && (
                <ListItem disableGutters>
                  <ListItemText primary={`Folder ${plan.data.folder.name}`} secondary={plan.data.folder.text} />
                </ListItem>
              )}
              {plan.data.items.map((item) => (
                <ListItem key={item.itemId} disableGutters>
                  <ListItemText primary={item.itemName || item.itemId} secondary={item.text} />
                </ListItem>
              ))}
            </List>
            <DialogContentText>
              bwtidy takes a new backup first, so this restore can be restored too.
            </DialogContentText>
          </>
        )}
        {refusal !== null && (
          <Alert severity="error" sx={{ mt: 2 }}>
            Nothing was queued: {refusal}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!plan.data || sending} onClick={() => void restore()}>
          Restore
        </Button>
      </DialogActions>
    </Dialog>
  )
}
