import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  FormControl,
  FormControlLabel,
  FormLabel,
  Link,
  Menu,
  MenuItem,
  Radio,
  RadioGroup,
  Snackbar,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { ApiError } from '../api/api.ts'
import { useFormatTime } from '../shared/DateFormat.tsx'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import { copyFeedback, useCopy } from '../shared/useCopy.ts'
import { MCP_SWITCH_SX } from './mcpStyle.ts'
import {
  createMcpToken,
  type McpTokenInfo,
  mcpTokenQueryKey,
  type NewMcpToken,
  removeMcpToken,
  type Session,
  sessionQueryKey,
  setMcpEnabled,
  useMcpTokenInfo,
  useSession,
} from '../api/session.ts'
import { expiryWarning } from './mcpToken.ts'

const NEEDS_TOKEN = 'Create an MCP token first: MCP token menu, Create.'
const TOKEN_EXPIRED = 'The MCP token has expired. Replace it first: MCP token menu, Replace.'
const NO_EXPIRY_WARNING = 'A token without an expiry works until you delete it.'
const EXPIRY_CHOICES: { value: string; label: string; days: number | null }[] = [
  { value: '7', label: '7 days', days: 7 },
  { value: '30', label: '30 days', days: 30 },
  { value: '90', label: '90 days', days: 90 },
  { value: '365', label: '365 days', days: 365 },
  { value: 'never', label: 'No expiry', days: null },
]
const DEFAULT_EXPIRY = '90'

const MCP_DOCS_PATH = '/docs/how-to/mcp/'

function CopyField({ label, value, hidden = false }: { label: string; value: string; hidden?: boolean }) {
  const { state, copy } = useCopy()

  return (
    <Stack direction="row" spacing={1} sx={{ alignItems: 'flex-start' }}>
      <TextField
        label={label}
        value={value}
        fullWidth
        multiline
        size="small"
        slotProps={{
          htmlInput: {
            ...NO_PASSWORD_MANAGER,
            readOnly: true,
            spellCheck: false,
            sx: { fontFamily: 'monospace' },
            // the docs screenshot hides the token's text (backend/tests/support/docs_shots.py)
            ...(hidden ? { 'data-shot-hide': true } : {}),
          },
        }}
        helperText={copyFeedback(state)}
      />
      <Button onClick={() => void copy(value)} aria-label={`Copy ${label.toLowerCase()}`} sx={{ mt: 0.5 }}>
        Copy
      </Button>
    </Stack>
  )
}

function TokenLifetime({ info }: { info: McpTokenInfo }) {
  const formatTime = useFormatTime()
  const rows: [string, string][] = [
    ['Created', formatTime(info.createdAt)],
    ['Expires', info.expiresAt === null ? 'Never' : formatTime(info.expiresAt)],
    ['Last used', info.lastUsedAt === null ? 'Never' : formatTime(info.lastUsedAt)],
  ]
  return (
    <Box component="li" role="group" sx={{ px: 2, py: 1 }} aria-label="MCP token times">
      {rows.map(([label, value]) => (
        <Typography key={label} variant="body2" color="text.secondary">
          {label}: {value}
        </Typography>
      ))}
    </Box>
  )
}

// Asks how long the new token lives, 90 days unless the user picks another.
function ExpiryDialog({
  open,
  replaces,
  onCancel,
  onCreate,
}: {
  open: boolean
  replaces: boolean
  onCancel: () => void
  onCreate: (days: number | null) => void
}) {
  const titleId = useId()
  const labelId = useId()
  const [choice, setChoice] = useState(DEFAULT_EXPIRY)
  const picked = EXPIRY_CHOICES.find((c) => c.value === choice) ?? EXPIRY_CHOICES[2]

  return (
    <Dialog
      open={open}
      onClose={onCancel}
      aria-labelledby={titleId}
      slotProps={{ transition: { onExited: () => setChoice(DEFAULT_EXPIRY) } }}
    >
      <DialogTitle id={titleId}>New MCP token</DialogTitle>
      <DialogContent>
        {replaces && (
          <DialogContentText sx={{ mb: 2 }}>The current token stops working once the new one is made.</DialogContentText>
        )}
        <FormControl>
          <FormLabel id={labelId}>Expires after</FormLabel>
          <RadioGroup aria-labelledby={labelId} value={choice} onChange={(event) => setChoice(event.target.value)}>
            {EXPIRY_CHOICES.map((c) => (
              <FormControlLabel key={c.value} value={c.value} control={<Radio />} label={c.label} />
            ))}
          </RadioGroup>
        </FormControl>
        {picked.days === null && (
          <Alert severity="warning" sx={{ mt: 1 }}>
            {NO_EXPIRY_WARNING}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="contained" onClick={() => onCreate(picked.days)}>
          Create
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// Create (or Replace) and Remove for the MCP bearer token. A new token is
// shown once in a dialog and forgotten when the dialog closes.
function McpTokenMenu({ exists, info }: { exists: boolean; info: ReturnType<typeof useMcpTokenInfo> }) {
  const queryClient = useQueryClient()
  const titleId = useId()
  const formatTime = useFormatTime()
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const [asking, setAsking] = useState(false)
  const [created, setCreated] = useState<NewMcpToken | null>(null)
  const shown = exists ? info.data : undefined
  // When the answer came, so the chip is as fresh as the times it shows.
  const warning = shown ? expiryWarning(shown.expiresAt, shown.createdAt, info.dataUpdatedAt) : null

  function setExists(mcpToken: boolean) {
    queryClient.setQueryData<Session>(sessionQueryKey, (old) => (old ? { ...old, mcpToken } : old))
  }

  const create = useMutation({
    mutationFn: createMcpToken,
    onSuccess: (answer) => {
      queryClient.setQueryData<McpTokenInfo>(mcpTokenQueryKey, {
        createdAt: answer.createdAt,
        expiresAt: answer.expiresAt,
        lastUsedAt: null,
      })
      setExists(true)
      setCreated(answer)
    },
  })
  const remove = useMutation({
    mutationFn: removeMcpToken,
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: mcpTokenQueryKey })
      setExists(false)
    },
  })
  const busy = create.isPending || remove.isPending
  const failed = create.isError ? 'create' : remove.isError ? 'remove' : null

  function openMenu(target: HTMLElement) {
    setAnchor(target)
    // The last use changes while the page stays open.
    if (exists) {
      void info.refetch()
    }
  }

  return (
    <>
      {warning !== null && <Chip label={warning} color="warning" size="small" sx={{ mr: 1 }} />}
      <Button color="inherit" sx={{ mr: 2 }} disabled={busy} onClick={(event) => openMenu(event.currentTarget)}>
        MCP token
      </Button>
      <Menu anchorEl={anchor} open={anchor !== null} onClose={() => setAnchor(null)}>
        {shown && <TokenLifetime info={shown} />}
        {shown && <Divider />}
        <MenuItem
          onClick={() => {
            setAnchor(null)
            setAsking(true)
          }}
        >
          {exists ? 'Replace' : 'Create'}
        </MenuItem>
        <MenuItem
          disabled={!exists}
          onClick={() => {
            setAnchor(null)
            remove.mutate()
          }}
        >
          Remove
        </MenuItem>
      </Menu>
      <ExpiryDialog
        open={asking}
        replaces={exists}
        onCancel={() => setAsking(false)}
        onCreate={(days) => {
          setAsking(false)
          create.mutate(days)
        }}
      />
      <Dialog
        open={created !== null}
        onClose={() => setCreated(null)}
        aria-labelledby={titleId}
        fullWidth
        maxWidth="md"
      >
        <DialogTitle id={titleId}>MCP token</DialogTitle>
        <DialogContent>
          <Alert severity="warning" sx={{ mb: 2 }}>
            The token is shown only now. Copy it before you close this dialog.
          </Alert>
          <DialogContentText sx={{ mb: 2 }}>
            An MCP client sends the token as the header Authorization: Bearer TOKEN to the MCP URL. A new token
            replaces this one.{' '}
            {created?.expiresAt
              ? `It expires ${formatTime(created.expiresAt)}.`
              : 'It has no expiry and works until you delete it.'}
          </DialogContentText>
          {created !== null && (
            <Stack spacing={2}>
              <CopyField label="Token" value={created.token} hidden />
              <CopyField label="MCP URL" value={`${window.location.origin}/mcp`} />
              <Link href={MCP_DOCS_PATH} target="_blank" rel="noopener">
                How to connect your MCP client
              </Link>
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreated(null)}>Close</Button>
        </DialogActions>
      </Dialog>
      <Snackbar
        open={failed !== null}
        autoHideDuration={6000}
        onClose={() => (failed === 'create' ? create.reset() : remove.reset())}
        message={`Could not ${failed ?? ''} the MCP token: ${(create.error ?? remove.error)?.message ?? ''}`}
      />
    </>
  )
}

// Why MCP cannot be switched on now, or null when it can: no token, or one that has expired.
function tokenProblem(exists: boolean, info: McpTokenInfo | undefined): string | null {
  if (!exists) {
    return NEEDS_TOKEN
  }
  if (info?.expiresAt && new Date(info.expiresAt).getTime() <= Date.now()) {
    return TOKEN_EXPIRED
  }
  return null
}

// The backend turns MCP off after every login, so the session answer is the
// only source of the switch's state. Switching on needs a token that has not
// expired, switching off always works.
export default function McpToggle() {
  const queryClient = useQueryClient()
  const session = useSession()
  const exists = session.data?.mcpToken ?? false
  const info = useMcpTokenInfo(exists)
  const [problem, setProblem] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: setMcpEnabled,
    onSuccess: ({ mcpEnabled }) => {
      queryClient.setQueryData<Session>(sessionQueryKey, (old) => (old ? { ...old, mcpEnabled } : old))
    },
  })
  const enabled = session.data?.mcpEnabled ?? false
  // The backend refuses too, when the page did not know yet.
  const refused = mutation.error instanceof ApiError && mutation.error.status === 409
  const shownProblem = refused ? (mutation.error?.message ?? NEEDS_TOKEN) : problem

  function change(on: boolean) {
    const missing = on ? tokenProblem(exists, info.data) : null
    setProblem(missing)
    if (missing === null) {
      mutation.mutate(on)
    }
  }

  return (
    <>
      <FormControlLabel
        sx={{ mr: 1 }}
        control={
          <Switch
            color="success"
            sx={MCP_SWITCH_SX}
            checked={enabled}
            disabled={mutation.isPending || session.data === undefined}
            onChange={(event) => change(event.target.checked)}
          />
        }
        label="MCP"
      />
      <McpTokenMenu exists={exists} info={info} />
      <Snackbar
        open={shownProblem !== null}
        autoHideDuration={6000}
        onClose={(_, reason) => {
          if (reason !== 'clickaway') {
            setProblem(null)
            mutation.reset()
          }
        }}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          severity="warning"
          variant="filled"
          sx={{ maxWidth: 640 }}
          onClose={() => {
            setProblem(null)
            mutation.reset()
          }}
        >
          {shownProblem}
        </Alert>
      </Snackbar>
      <Snackbar
        open={mutation.isError && !refused}
        autoHideDuration={6000}
        onClose={() => mutation.reset()}
        message={`Could not switch MCP: ${mutation.error?.message ?? ''}`}
      />
    </>
  )
}
