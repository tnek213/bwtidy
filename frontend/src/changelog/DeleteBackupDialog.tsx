import { Alert, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { ApiError } from '../api/api.ts'
import { changeLogQueryKey, deleteBackup } from '../api/changeLog.ts'

// Asks before a backup is deleted for good. A refusal (409: the newest backup, the one a running
// write selected, or one a queue entry still needs) stays in the dialog and nothing is deleted.
export default function DeleteBackupDialog({ name, onClose }: { name: string; onClose: () => void }) {
  const titleId = useId()
  const queryClient = useQueryClient()
  const [sending, setSending] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  async function remove() {
    setSending(true)
    setRefusal(null)
    try {
      await deleteBackup(name)
    } catch (error) {
      setRefusal(error instanceof ApiError && error.detail ? error.detail : String(error))
      setSending(false)
      return
    }
    // The backups and the change log rows that name them.
    void queryClient.invalidateQueries({ queryKey: changeLogQueryKey })
    onClose()
  }

  return (
    <Dialog open onClose={onClose} aria-labelledby={titleId} fullWidth maxWidth="sm">
      <DialogTitle id={titleId}>Delete backup</DialogTitle>
      <DialogContent>
        <DialogContentText>
          Delete backup {name} for good? Changes made with it can no longer be restored.
        </DialogContentText>
        {refusal !== null && (
          <Alert severity="error" sx={{ mt: 2 }}>
            Nothing was deleted: {refusal}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" color="error" disabled={sending} onClick={() => void remove()}>
          Delete
        </Button>
      </DialogActions>
    </Dialog>
  )
}
