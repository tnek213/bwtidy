import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { changeLogQueryKey } from '../api/changeLog.ts'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import { queueQueryKey } from '../api/queue.ts'
import { announceLogout, switchAccount } from '../shared/account.ts'
import { useSession } from '../api/session.ts'
import Prose from '../theme/Prose.tsx'
import { deleteMyAccount, deleteMyData, type DeleteResult, type PendingChoice, type PendingCounts } from '../api/deleteData.ts'
import { deleteErrorText, pendingCountsOf } from './deleteData.ts'

const SECTION_SX = { maxWidth: 560, alignItems: 'flex-start' } as const

const DATA_DELETED = [
  'every backup, the newest one too',
  'the change log',
  'the backup fingerprints',
  'the decided queue entries (applied, failed, rejected, dismissed)',
]

const ACCOUNT_DELETED = [
  ...DATA_DELETED,
  'the settings and the MCP token',
  'the Bitwarden login of bwtidy (it logs out)',
  'the account itself',
]

type Kind = 'data' | 'account'

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

// How the pending edits and MCP proposals are handled, so the radio choice can say what stays.
function pendingChoiceText(kind: Kind, counts: PendingCounts): { write: string; drop: string } {
  const edits = plural(counts.pending, 'pending edit', 'pending edits')
  const proposals = plural(counts.mcpPending, 'MCP proposal', 'MCP proposals')
  // Delete my account drops the proposals too, since the account goes.
  const writeKeeps =
    counts.mcpPending === 0
      ? ''
      : kind === 'data'
        ? ` Your ${proposals} cannot be written without approval and stay pending.`
        : ` Your ${proposals} are dropped too, since the account goes.`
  const dropped = counts.mcpPending === 0 ? edits : `${edits} and ${proposals}`
  return {
    write: `Write the ${edits} first.${writeKeeps}`,
    drop: `Drop them: ${dropped} are deleted unwritten.`,
  }
}

function resultText(result: DeleteResult): string {
  const parts = [
    plural(result.backups, 'backup', 'backups'),
    plural(result.changeLog, 'change log row', 'change log rows'),
    plural(result.queue, 'queue entry', 'queue entries'),
  ]
  const kept =
    result.keptBackups.length === 0
      ? ''
      : ` ${plural(result.keptBackups.length, 'backup', 'backups')} a pending restore still needs stayed.`
  return `Deleted ${parts.join(', ')}.${kept}`
}

// The dialog behind each button: it lists what will be deleted, confirms by typing the email,
// and, only after a 409 with pending entries, asks whether to write them first or drop them.
function DeleteDialog({
  kind,
  email,
  onClose,
  onData,
}: {
  kind: Kind
  email: string
  onClose: () => void
  onData: (result: DeleteResult) => void
}) {
  const queryClient = useQueryClient()
  const titleId = useId()
  const [typed, setTyped] = useState('')
  const [counts, setCounts] = useState<PendingCounts | null>(null)
  const [choice, setChoice] = useState<Exclude<PendingChoice, null>>('write')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const matches = typed.trim().toLowerCase() === email.toLowerCase()
  const title = kind === 'data' ? 'Delete my data' : 'Delete my account'

  async function confirm() {
    setBusy(true)
    setError(null)
    const pending: PendingChoice = counts === null ? null : choice
    try {
      if (kind === 'data') {
        const result = await deleteMyData(pending)
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: changeLogQueryKey }),
          queryClient.invalidateQueries({ queryKey: queueQueryKey }),
        ])
        onData(result)
      } else {
        await deleteMyAccount(pending)
        announceLogout()
        // Forgets the account, calls DELETE /gateway/account and shows the account form.
        await switchAccount(queryClient, false)
      }
    } catch (caught) {
      const asked = pendingCountsOf(caught)
      if (asked !== null && counts === null) {
        setCounts(asked)
      } else {
        setError(deleteErrorText(caught))
      }
      setBusy(false)
    }
  }

  return (
    <Dialog open onClose={() => !busy && onClose()} aria-labelledby={titleId}>
      <DialogTitle id={titleId}>{title}</DialogTitle>
      <DialogContent>
        <Prose>This deletes, for {email}:</Prose>
        <Box component="ul" sx={{ mt: 0.5, mb: 0, pl: 3 }}>
          {(kind === 'data' ? DATA_DELETED : ACCOUNT_DELETED).map((line) => (
            <Typography component="li" variant="body1" key={line}>
              {line}
            </Typography>
          ))}
        </Box>
        <Prose sx={{ mt: 1 }}>
          {kind === 'data'
            ? 'The settings, the MCP token and the templates stay.'
            : 'The vault stays in Bitwarden, the templates in its bwtidy note too. Afterwards bwtidy shows the account form.'}
        </Prose>
        {counts !== null && (
          <FormControl sx={{ mt: 2 }}>
            <Typography variant="subtitle2" gutterBottom>
              Some edits are still pending.
            </Typography>
            <RadioGroup value={choice} onChange={(event) => setChoice(event.target.value as 'write' | 'drop')}>
              <FormControlLabel value="write" control={<Radio />} label={pendingChoiceText(kind, counts).write} />
              <FormControlLabel value="drop" control={<Radio />} label={pendingChoiceText(kind, counts).drop} />
            </RadioGroup>
          </FormControl>
        )}
        <TextField
          sx={{ mt: 2 }}
          fullWidth
          label={`Type ${email} to confirm`}
          value={typed}
          disabled={busy}
          onChange={(event) => setTyped(event.target.value)}
          slotProps={{ htmlInput: { ...NO_PASSWORD_MANAGER, spellCheck: false } }}
        />
        {error !== null && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button disabled={busy} onClick={onClose}>
          Cancel
        </Button>
        <Button color="error" variant="contained" disabled={!matches || busy} onClick={() => void confirm()}>
          {title}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// The "Delete data" section at the bottom of the Settings page (docs/spec.md, Deleting data).
export default function DeleteDataSection() {
  const session = useSession()
  const email = session.data?.bwLogin.email ?? ''
  const [open, setOpen] = useState<Kind | null>(null)
  const [done, setDone] = useState<string | null>(null)

  return (
    <Stack component="section" aria-labelledby="delete-heading" spacing={2} sx={SECTION_SX}>
      <Typography id="delete-heading" variant="h6" component="h3">
        Delete data
      </Typography>
      <Stack spacing={1}>
        <Button color="error" variant="outlined" disabled={email === ''} onClick={() => setOpen('data')}>
          Delete my data
        </Button>
        <Typography variant="body2" color="text.secondary">
          Removes every backup, the change log and the decided queue entries. The settings, the MCP token and the
          templates stay.
        </Typography>
      </Stack>
      <Stack spacing={1}>
        <Button color="error" variant="outlined" disabled={email === ''} onClick={() => setOpen('account')}>
          Delete my account
        </Button>
        <Typography variant="body2" color="text.secondary">
          Does all of that, deletes the settings and the MCP token, logs out of Bitwarden and removes the account. The
          vault and its templates stay in Bitwarden.
        </Typography>
      </Stack>
      {done !== null && <Alert severity="success">{done}</Alert>}
      {open !== null && (
        <DeleteDialog
          kind={open}
          email={email}
          onClose={() => setOpen(null)}
          onData={(result) => {
            setDone(resultText(result))
            setOpen(null)
          }}
        />
      )}
    </Stack>
  )
}
