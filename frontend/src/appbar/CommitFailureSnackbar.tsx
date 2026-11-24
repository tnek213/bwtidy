import { Alert, Box, Snackbar } from '@mui/material'
import { useAutoCommit } from '../commit/autoCommit.ts'

// A commit that could not write some UI edits, on every page.
export default function CommitFailureSnackbar() {
  const { failure, closeFailure } = useAutoCommit()
  return (
    <Snackbar
      open={failure !== null}
      onClose={(_, reason) => reason !== 'clickaway' && closeFailure()}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
    >
      {failure ? (
        <Alert severity="error" variant="filled" onClose={closeFailure} sx={{ maxWidth: 640 }}>
          {failure.message}
          {failure.lines.length > 0 && (
            <Box component="ul" sx={{ m: 0, mt: 0.5, pl: 2.5 }}>
              {failure.lines.map((line, index) => (
                <li key={`${index}:${line}`}>{line}</li>
              ))}
            </Box>
          )}
        </Alert>
      ) : undefined}
    </Snackbar>
  )
}
