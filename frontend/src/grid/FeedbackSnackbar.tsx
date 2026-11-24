import { Alert, Box, Snackbar } from '@mui/material'
import type { Feedback } from './writer.ts'

export default function FeedbackSnackbar({ feedback, onClose }: { feedback: Feedback | null; onClose: () => void }) {
  return (
    <Snackbar
      open={feedback !== null}
      onClose={(_, reason) => reason !== 'clickaway' && onClose()}
      autoHideDuration={feedback?.severity === 'success' ? 6000 : null}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
    >
      {feedback ? (
        <Alert severity={feedback.severity} onClose={onClose} variant="filled" sx={{ maxWidth: 640 }}>
          {feedback.message}
          {feedback.severity !== 'success' && feedback.items && feedback.items.length > 0 && (
            <Box component="ul" sx={{ m: 0, mt: 0.5, pl: 2.5 }}>
              {feedback.items.map((name, index) => (
                <li key={`${index}:${name}`}>{name}</li>
              ))}
            </Box>
          )}
        </Alert>
      ) : undefined}
    </Snackbar>
  )
}
