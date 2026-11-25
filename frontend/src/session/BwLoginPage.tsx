import { Alert, Box, Button, Container, Paper, Stack, TextField, Typography } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/api.ts'
import { refreshAccount } from '../api/account.ts'
import {
  type BwLogin,
  type BwLoginInput,
  bwLoginOutput,
  cancelBwLogin,
  finishBwLogin,
  login,
  sendBwLoginInput,
  sessionQueryKey,
  startBwLogin,
} from '../api/session.ts'
import { withWorker, workerGone } from '../shared/account.ts'
import { browserAccount, noteWorkerRunning, saveFormValues, workerRunning } from './account.ts'
import MasterPasswordField from './MasterPasswordField.tsx'
import Starting from './Starting.tsx'
import Prose from '../theme/Prose.tsx'

const POLL_INTERVAL_MS = 500

type Phase = 'form' | 'starting' | 'running' | 'finishing' | 'failed'

function unlockErrorText(error: unknown): string {
  if (error instanceof ApiError && error.status === 401 && !workerGone(error)) {
    return 'Wrong master password'
  }
  return 'Login failed, try again'
}

function startErrorText(error: unknown): string {
  // 422: a worker refuses another account than its own, or the gateway the server's address.
  if (error instanceof ApiError && (error.status === 409 || error.status === 422)) {
    return error.detail ?? 'A Bitwarden login is already running'
  }
  // The gateway's reason: one of its caps is reached, or the worker did not start.
  if (error instanceof ApiError && (error.status === 429 || error.status === 503) && error.detail) {
    return error.detail
  }
  return 'Could not start the Bitwarden login, try again'
}

interface BwLoginPageProps {
  bwLogin: BwLogin
  // The notice for a first start (about/FirstRunNotice.tsx), above the form.
  notice?: ReactNode
}

// Shown instead of LoginPage while the Bitwarden CLI in the container is
// logged out. It runs `bw login` in the backend, shows what the CLI prints and
// passes answers back (the emailed one-time code on a new device), then
// unlocks with the same master password.
export default function BwLoginPage({ bwLogin, notice }: BwLoginPageProps) {
  const queryClient = useQueryClient()
  const accountFixed = bwLogin.accountFixed === true
  // A fixed account comes from the backend, else the browser's memory wins over what the CLI
  // is configured for.
  const [initial] = useState(() => (accountFixed ? null : browserAccount()) ?? bwLogin)
  const [server, setServer] = useState(initial.server)
  const [email, setEmail] = useState(initial.email)
  // bw login with an empty email waits at "? Email address:" until Cancel.
  const accountFilled = server.trim() !== '' && email.trim() !== ''
  const [phase, setPhase] = useState<Phase>('form')
  const [loginId, setLoginId] = useState<string | null>(null)
  const [output, setOutput] = useState('')
  const [error, setError] = useState<string | null>(null)
  // After the CLI logged in but the unlock failed, the form only unlocks.
  const [unlockOnly, setUnlockOnly] = useState(false)
  // The click waits for the account's worker to start.
  const [starting, setStarting] = useState(false)
  // Remounting the field resets MUI's filled state and focuses it (see LoginPage).
  const [fieldKey, setFieldKey] = useState(0)
  // The password is hidden again when the form is sent or fails.
  const [shown, setShown] = useState(false)
  const nextOffset = useRef(0)
  const consoleRef = useRef<HTMLPreElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  function backToForm() {
    setShown(false)
    setLoginId(null)
    setPhase('form')
    setFieldKey((key) => key + 1)
  }

  useEffect(() => {
    if (loginId === null || phase !== 'running') {
      return
    }
    const id = loginId
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined

    // Leaving the running phase stops the polling, the unlock goes on.
    async function finish() {
      setPhase('finishing')
      try {
        const session = await finishBwLogin(id)
        queryClient.setQueryData(sessionQueryKey, session)
      } catch (caught) {
        setError(unlockErrorText(caught))
        setUnlockOnly(true)
        setLoginId(null)
        setPhase('form')
        setShown(false)
        setFieldKey((key) => key + 1)
      }
    }

    async function poll() {
      try {
        const answer = await bwLoginOutput(id, nextOffset.current)
        if (stopped) {
          return
        }
        nextOffset.current = answer.next
        if (answer.text !== '') {
          setOutput((text) => text + answer.text)
        }
        if (answer.running) {
          timer = setTimeout(() => void poll(), POLL_INTERVAL_MS)
        } else if (answer.exitCode === 0) {
          void finish()
        } else {
          setLoginId(null)
          setPhase('failed')
          setError(`bw login failed (exit code ${answer.exitCode ?? 'unknown'})`)
        }
      } catch (caught) {
        if (stopped) {
          return
        }
        if (caught instanceof ApiError && caught.status === 404) {
          setLoginId(null)
          setPhase('failed')
          setError('The Bitwarden login timed out, try again')
          return
        }
        timer = setTimeout(() => void poll(), POLL_INTERVAL_MS)
      }
    }

    void poll()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [loginId, phase, queryClient])

  useEffect(() => {
    const element = consoleRef.current
    if (element) {
      element.scrollTop = element.scrollHeight
    }
  }, [output])

  // The password is read from the uncontrolled field on submit and the field
  // is cleared right after, so it never lives in React state or a cache.
  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const field = event.currentTarget.elements.namedItem('password')
    if (!(field instanceof HTMLInputElement) || field.value === '') {
      return
    }
    if (!unlockOnly && !accountFilled) {
      return
    }
    const password = field.value
    field.value = ''
    setShown(false)
    setError(null)
    setPhase('starting')
    setStarting(!workerRunning(queryClient))
    if (unlockOnly) {
      try {
        const session = await login(password)
        noteWorkerRunning(queryClient)
        queryClient.setQueryData(sessionQueryKey, session)
      } catch (caught) {
        setError(unlockErrorText(caught))
        setStarting(false)
        backToForm()
        void refreshAccount(queryClient)
      }
      return
    }
    setOutput('')
    nextOffset.current = 0
    if (!accountFixed) {
      saveFormValues({ server: server.trim(), email: email.trim() })
    }
    try {
      const id = await withWorker(queryClient, () =>
        startBwLogin({ server: server.trim(), email: email.trim(), password }),
      )
      noteWorkerRunning(queryClient)
      setStarting(false)
      setLoginId(id)
      setPhase('running')
    } catch (caught) {
      setError(startErrorText(caught))
      setStarting(false)
      backToForm()
      void refreshAccount(queryClient)
    }
  }

  async function send(input: BwLoginInput) {
    if (loginId === null) {
      return
    }
    try {
      await sendBwLoginInput(loginId, input)
    } catch (caught) {
      // A 409 means the CLI exited meanwhile, the next poll reports that.
      if (!(caught instanceof ApiError && caught.status === 409)) {
        setError('Could not send the input to the CLI')
      }
    }
  }

  function handleSend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const field = inputRef.current
    if (!field) {
      return
    }
    const text = field.value
    field.value = ''
    setError(null)
    void send({ text })
  }

  async function handleCancel() {
    if (loginId !== null) {
      try {
        await cancelBwLogin(loginId)
      } catch {
        // The backend drops the login 10 minutes after the start anyway.
      }
    }
    setError(null)
    backToForm()
  }

  function handleTryAgain() {
    setError(null)
    backToForm()
  }

  const editable = phase === 'form'
  const running = phase === 'running'

  return (
    <Container maxWidth="sm">
      <Paper sx={{ mt: 12, p: 4 }}>
        <Typography variant="h4" component="h1" gutterBottom>
          bwtidy
        </Typography>
        {notice}
        <Prose>
          {unlockOnly
            ? 'The Bitwarden CLI is logged in. Unlock with the master password.'
            : 'The Bitwarden CLI is not logged in. Log it in to unlock the vault.'}
        </Prose>
        {/* method="post" keeps the password out of the URL if a submit ever
            happens before React has attached its handler. */}
        <Box component="form" method="post" noValidate onSubmit={handleSubmit}>
          <TextField
            name="server"
            label="Server"
            value={server}
            onChange={(event) => setServer(event.target.value)}
            fullWidth
            required
            disabled={!editable || unlockOnly}
            margin="normal"
            slotProps={{ htmlInput: { readOnly: accountFixed } }}
          />
          <TextField
            name="email"
            type="email"
            label="Email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            fullWidth
            required
            disabled={!editable || unlockOnly}
            margin="normal"
            slotProps={{ htmlInput: { readOnly: accountFixed } }}
          />
          <MasterPasswordField
            key={fieldKey}
            shown={shown}
            onToggle={() => setShown((value) => !value)}
            disabled={!editable}
          />
          {error && (
            <Alert severity="error" sx={{ mt: 1 }}>
              {error}
            </Alert>
          )}
          {phase === 'failed' ? (
            <Button variant="contained" fullWidth onClick={handleTryAgain} sx={{ mt: 2 }}>
              Try again
            </Button>
          ) : (
            <Button
              type="submit"
              variant="contained"
              fullWidth
              disabled={!editable || (!unlockOnly && !accountFilled)}
              sx={{ mt: 2 }}
            >
              {unlockOnly ? 'Unlock' : 'Log in'}
            </Button>
          )}
          {phase === 'starting' && starting && <Starting />}
        </Box>
        {(output !== '' || running) && (
          <Box
            ref={consoleRef}
            component="pre"
            role="log"
            aria-label="Bitwarden CLI output"
            sx={{
              mt: 3,
              mb: 0,
              p: 1.5,
              height: 240,
              overflow: 'auto',
              fontFamily: 'monospace',
              fontSize: 13,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              bgcolor: 'grey.900',
              color: 'grey.100',
              borderRadius: 1,
            }}
          >
            {output}
          </Box>
        )}
        {running && (
          <Box component="form" noValidate onSubmit={handleSend} sx={{ mt: 1 }}>
            <TextField
              inputRef={inputRef}
              name="cliInput"
              label="Answer to the CLI"
              autoComplete="off"
              fullWidth
              size="small"
              slotProps={{ htmlInput: { spellCheck: false } }}
            />
            <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
              <Button type="submit" variant="contained">
                Send
              </Button>
              <Button onClick={() => void send({ password: true })}>Send password</Button>
              <Box sx={{ flexGrow: 1 }} />
              <Button color="error" onClick={() => void handleCancel()}>
                Cancel
              </Button>
            </Stack>
          </Box>
        )}
      </Paper>
    </Container>
  )
}
