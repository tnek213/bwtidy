import {
  Alert,
  Box,
  Button,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Paper,
  TextField,
  Typography,
} from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useId, useState } from 'react'
import { ApiError } from '../api/api.ts'
import { type Account, openAccount, openErrorText } from '../api/account.ts'
import { showAccount } from '../shared/account.ts'
import { formValues, saveFormValues } from './account.ts'

interface AccountPageProps {
  // Why the app could not open the remembered account by itself.
  error?: string
}

// Asks which Bitwarden account this browser works with, before the unlock. A new account is
// offered to be created, the gateway keeps it only once its Bitwarden login succeeded.
export default function AccountPage({ error: initialError }: AccountPageProps) {
  const queryClient = useQueryClient()
  const titleId = useId()
  const [initial] = useState(formValues)
  const [server, setServer] = useState(initial.server)
  const [email, setEmail] = useState(initial.email)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(initialError ?? null)
  // The account the create dialog offers, null while it is closed.
  const [offer, setOffer] = useState<Account | null>(null)
  const filled = server.trim() !== '' && email.trim() !== ''

  async function open(account: Account, create: boolean) {
    setBusy(true)
    setError(null)
    try {
      showAccount(queryClient, await openAccount(account, create))
    } catch (caught) {
      setBusy(false)
      if (!create && caught instanceof ApiError && caught.status === 404) {
        setOffer(account)
        return
      }
      setOffer(null)
      setError(openErrorText(caught))
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!filled || busy) {
      return
    }
    const account = { server: server.trim(), email: email.trim() }
    saveFormValues(account)
    void open(account, false)
  }

  return (
    <Container maxWidth="xs">
      <Paper sx={{ mt: 12, p: 4 }}>
        <Typography variant="h4" component="h1" gutterBottom>
          bwtidy
        </Typography>
        <Typography color="text.secondary">Which Bitwarden account?</Typography>
        <Box component="form" noValidate onSubmit={handleSubmit}>
          <TextField
            label="Server"
            type="url"
            value={server}
            onChange={(event) => setServer(event.target.value)}
            disabled={busy}
            fullWidth
            required
            margin="normal"
          />
          <TextField
            label="Email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={busy}
            autoFocus
            fullWidth
            required
            margin="normal"
          />
          {error && (
            <Alert severity="error" sx={{ mt: 1 }}>
              {error}
            </Alert>
          )}
          <Button type="submit" variant="contained" fullWidth disabled={busy || !filled} sx={{ mt: 2 }}>
            Continue
          </Button>
        </Box>
      </Paper>
      <Dialog open={offer !== null} onClose={() => !busy && setOffer(null)} aria-labelledby={titleId}>
        <DialogTitle id={titleId}>Create account</DialogTitle>
        <DialogContent>
          <DialogContentText>
            There is no bwtidy account for {offer?.email} on {offer?.server} yet. Create it? It is kept only if the
            Bitwarden login succeeds.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button disabled={busy} onClick={() => setOffer(null)}>
            Cancel
          </Button>
          <Button variant="contained" disabled={busy} onClick={() => offer !== null && void open(offer, true)}>
            Create
          </Button>
        </DialogActions>
      </Dialog>
    </Container>
  )
}
