import {
  Alert,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControlLabel,
  Stack,
  Typography,
} from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { ApiError } from '../api/api.ts'
import { useAutoCommit } from '../commit/autoCommit.ts'
import { announceLogout, reopenAccount, withWorker, workerGone } from '../shared/account.ts'
import { bwLogout, logout, sessionQueryKey } from '../api/session.ts'
import { showLocked, unlockNoteQueryKey } from '../shared/session.ts'
import Prose from '../theme/Prose.tsx'

const UNLOCK_TO_LOG_OUT = 'Unlock first to log out of Bitwarden'

// The backend commits the unsaved edits before it ends the session, with the box checked or not.
export default function LogoutButton() {
  const queryClient = useQueryClient()
  const { unsaved } = useAutoCommit()
  const titleId = useId()
  const [open, setOpen] = useState(false)
  const [alsoBitwarden, setAlsoBitwarden] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleOpen() {
    setAlsoBitwarden(false)
    setError(null)
    setOpen(true)
  }

  async function handleConfirm() {
    setBusy(true)
    setSaving(unsaved > 0)
    setError(null)
    if (!alsoBitwarden) {
      try {
        await logout()
      } catch {
        // Locked locally either way. Without heartbeats the backend ends the
        // session within 120 s.
      }
      // The worker exits with its session, the unlock starts the account's next one.
      await reopenAccount(queryClient)
      showLocked(queryClient)
      announceLogout()
      return
    }
    try {
      await withWorker(queryClient, bwLogout)
      await reopenAccount(queryClient)
      showLocked(queryClient, { loggedIn: false })
      announceLogout()
    } catch (caught) {
      // The session ended behind the page, and only a session may log the CLI out. Its worker
      // may be gone or on its way out too: nothing starts one for a log out.
      if ((caught instanceof ApiError && caught.status === 401) || workerGone(caught)) {
        showLocked(queryClient)
        queryClient.setQueryData(unlockNoteQueryKey, UNLOCK_TO_LOG_OUT)
        return
      }
      setBusy(false)
      setSaving(false)
      setError('Could not log out of Bitwarden')
      // The session may have ended without the CLI logging out, so ask the
      // backend what is true now.
      void queryClient.invalidateQueries({ queryKey: sessionQueryKey })
    }
  }

  return (
    <>
      <Button color="inherit" onClick={handleOpen}>
        Log out
      </Button>
      <Dialog open={open} onClose={() => !busy && setOpen(false)} aria-labelledby={titleId}>
        <DialogTitle id={titleId}>Log out</DialogTitle>
        <DialogContent>
          <DialogContentText>Log out of bwtidy. Unsaved changes are written first.</DialogContentText>
          <FormControlLabel
            sx={{ mt: 1 }}
            control={
              <Checkbox
                checked={alsoBitwarden}
                disabled={busy}
                onChange={(event) => setAlsoBitwarden(event.target.checked)}
              />
            }
            label="Also log out of Bitwarden (the next login asks for the new device code again)"
          />
          {alsoBitwarden && (
            <Prose>
              The next login runs the Bitwarden login again with the master password, and bitwarden.com may
              email a one-time code.
            </Prose>
          )}
          {saving && (
            <Stack direction="row" sx={{ alignItems: 'center', gap: 2, mt: 2 }}>
              <CircularProgress size={20} />
              <Typography>Saving your changes</Typography>
            </Stack>
          )}
          {error !== null && (
            <Alert severity="error" sx={{ mt: 2 }}>
              {error}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button disabled={busy} onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            color={alsoBitwarden ? 'error' : 'primary'}
            variant="contained"
            disabled={busy}
            onClick={() => void handleConfirm()}
          >
            Log out
          </Button>
        </DialogActions>
      </Dialog>
    </>
  )
}
