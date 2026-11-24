import { Alert, Box, Button, Container, Paper, Typography } from '@mui/material'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { ApiError } from '../api/api.ts'
import { refreshAccount } from '../api/account.ts'
import { login, sessionQueryKey } from '../api/session.ts'
import { unlockNoteQueryKey } from '../shared/session.ts'
import { withWorker, workerGone } from '../shared/account.ts'
import { noteWorkerRunning, workerRunning } from './account.ts'
import MasterPasswordField from './MasterPasswordField.tsx'
import Starting from './Starting.tsx'

function useUnlockNote(): string | null {
  return (
    useQuery({ queryKey: unlockNoteQueryKey, queryFn: () => null as string | null, staleTime: Infinity }).data ?? null
  )
}

function wrongPassword(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401 && !workerGone(error)
}

function errorText(error: unknown): string {
  if (wrongPassword(error)) {
    return 'Wrong master password'
  }
  // The backend waits longer after each wrong password against a running session and answers
  // 409 for a tool note outside the personal vault, and the gateway answers 429 at a cap and 503
  // when the worker did not start, each with its reason.
  if (error instanceof ApiError && [409, 429, 503].includes(error.status) && error.detail) {
    return error.detail
  }
  if (error instanceof ApiError && error.status === 429) {
    return 'Too many wrong passwords, try again later'
  }
  return 'Login failed, try again'
}

export default function LoginPage() {
  const queryClient = useQueryClient()
  const note = useUnlockNote()
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  // The unlock waits for the account's worker to start.
  const [starting, setStarting] = useState(false)
  // Remounting the field after a failed login resets MUI's filled state
  // (a plain value reset on an uncontrolled input does not) and focuses it.
  const [fieldKey, setFieldKey] = useState(0)
  // The password is hidden again when the form is sent.
  const [shown, setShown] = useState(false)

  // The password is read from the uncontrolled field on submit and the field
  // is cleared right after, so it never lives in React state or a cache.
  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const field = form.elements.namedItem('password')
    if (!(field instanceof HTMLInputElement) || field.value === '') {
      return
    }
    const password = field.value
    field.value = ''
    setShown(false)
    setPending(true)
    setStarting(!workerRunning(queryClient))
    setError(null)
    try {
      const session = await withWorker(queryClient, () => login(password))
      noteWorkerRunning(queryClient)
      queryClient.removeQueries({ queryKey: unlockNoteQueryKey })
      queryClient.setQueryData(sessionQueryKey, session)
    } catch (caught) {
      setError(errorText(caught))
      setPending(false)
      setStarting(false)
      setFieldKey((key) => key + 1)
      // The click may have started the worker, and an unlock that failed for another reason
      // than the password may mean the CLI is logged out: the Bitwarden login shows then.
      await refreshAccount(queryClient)
      if (!wrongPassword(caught)) {
        void queryClient.invalidateQueries({ queryKey: sessionQueryKey })
      }
    }
  }

  return (
    <Container maxWidth="xs">
      <Paper sx={{ mt: 12, p: 4 }}>
        <Typography variant="h4" component="h1" gutterBottom>
          bwtidy
        </Typography>
        {note !== null && (
          <Alert severity="info" role="status">
            {note}
          </Alert>
        )}
        {/* method="post" keeps the password out of the URL if a submit ever
            happens before React has attached its handler. */}
        <Box component="form" method="post" noValidate onSubmit={handleSubmit}>
          <MasterPasswordField
            key={fieldKey}
            shown={shown}
            onToggle={() => setShown((value) => !value)}
            disabled={pending}
          />
          {error && (
            <Alert severity="error" sx={{ mt: 1 }}>
              {error}
            </Alert>
          )}
          <Button type="submit" variant="contained" fullWidth disabled={pending} sx={{ mt: 2 }}>
            Unlock
          </Button>
          {starting && <Starting />}
        </Box>
      </Paper>
    </Container>
  )
}
