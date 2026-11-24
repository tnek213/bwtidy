import { Button, CircularProgress, Stack, Tooltip } from '@mui/material'
import { phaseText, useAutoCommit } from '../commit/autoCommit.ts'

// A way to write the UI edits now, and a spinner while a commit runs.
export default function UnsavedChanges() {
  const { unsaved, committing, status, commitNow } = useAutoCommit()
  if (unsaved === 0 && !committing) {
    return null
  }
  return (
    <Stack direction="row" sx={{ alignItems: 'center', gap: 1, ml: 2 }}>
      {committing && (
        <Tooltip title={phaseText(status)}>
          <CircularProgress size={18} color="inherit" aria-label={phaseText(status)} />
        </Tooltip>
      )}
      {unsaved > 0 && (
        <Button color="inherit" size="small" variant="outlined" onClick={() => void commitNow()}>
          Commit now
        </Button>
      )}
    </Stack>
  )
}
