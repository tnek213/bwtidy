import { CircularProgress, Stack, Typography } from '@mui/material'

// Under the unlock and the Bitwarden login while the account's worker starts for the click.
export default function Starting() {
  return (
    <Stack role="status" direction="row" sx={{ alignItems: 'center', justifyContent: 'center', gap: 1, mt: 2 }}>
      <CircularProgress size={20} aria-hidden />
      <Typography>Starting</Typography>
    </Stack>
  )
}
