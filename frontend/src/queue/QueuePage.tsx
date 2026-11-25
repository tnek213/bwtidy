import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControlLabel,
  Paper,
  Stack,
  Tab,
  Table,
  TextField,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  Typography,
} from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useId, useState } from 'react'
import { ApiError } from '../api/api.ts'
import { isMasked, MASK } from '../shared/values.ts'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import { toggled } from '../shared/sets.ts'
import { sessionQueryKey } from '../api/session.ts'
import { useFormatTime } from '../shared/DateFormat.tsx'
import {
  approveEntries,
  type ApproveOutcome,
  type DiffRow,
  type DiffValue,
  dismissEntries,
  QUEUE_POLL_MS,
  type QueueEntry,
  queueKey,
  type QueueStatus,
  rejectEntries,
  retryEntries,
  type ShownUri,
  useQueue,
} from '../api/queue.ts'
import {
  AFTER_SECONDS_RANGE,
  type AutoCommitSettings,
  autoCommitQueryKey,
  MAX_ENTRIES_RANGE,
  saveAutoCommit,
  useAutoCommit,
  useAutoCommitStatus,
} from '../commit/autoCommit.ts'
import { type QueueTab, useUnreadCounts } from '../commit/unread.ts'
import { failureReason } from '../shared/queueText.ts'
import { conflictLines, noRetryNote, retryKind } from './failures.ts'
import Prose from '../theme/Prose.tsx'

function DiffCell({ value }: { value: DiffValue }) {
  if (value === null) {
    return (
      <Box component="span" sx={{ color: 'text.secondary', fontStyle: 'italic' }}>
        none
      </Box>
    )
  }
  if (isMasked(value)) {
    return (
      <Box component="span" data-testid="masked-value" title="Secret value">
        {MASK}
      </Box>
    )
  }
  if (Array.isArray(value)) {
    return <>{value.join(', ')}</>
  }
  return <Box component="span" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'break-word' }}>{String(value)}</Box>
}

// One line per URI with its match word, as a login's list before or after a setUris.
function UriList({ uris }: { uris: ShownUri[] }) {
  if (uris.length === 0) {
    return <DiffCell value={null} />
  }
  return (
    <Box component="ul" sx={{ m: 0, p: 0, listStyle: 'none' }}>
      {uris.map((entry, index) => (
        <Box component="li" key={`${index}:${entry.uri}`} sx={{ overflowWrap: 'break-word' }}>
          {entry.uri}{' '}
          <Box component="span" sx={{ color: 'text.secondary' }}>
            ({entry.match ?? 'default'})
          </Box>
        </Box>
      ))}
    </Box>
  )
}

// The same default share per column in every table, so the columns line up across
// entries. In auto layout these are preferred widths: text wraps inside them, and a
// column grows only for a token that cannot wrap. That is why the cells use
// overflow-wrap break-word: word-break break-word would also shrink the minimum
// width of a long token, so no column could ever grow for it.
const DIFF_COLUMN_WIDTHS = ['22%', '18%', '30%', '30%']

function DiffTable({ entry }: { entry: QueueEntry }) {
  if (entry.diff.length === 0) {
    return entry.diffError ? null : (
      <Typography variant="body2" color="text.secondary">
        No cell changes.
      </Typography>
    )
  }
  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small" aria-label={`Changes of entry ${entry.id}`} sx={{ width: '100%' }}>
        <colgroup>
          {DIFF_COLUMN_WIDTHS.map((width, index) => (
            <col key={index} style={{ width }} />
          ))}
        </colgroup>
        <TableHead>
          <TableRow>
            <TableCell>Item</TableCell>
            <TableCell>Column</TableCell>
            <TableCell>Before</TableCell>
            <TableCell>After</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {entry.diff.map((row: DiffRow, index) => (
            <TableRow key={`${row.itemId}:${row.column}:${index}`}>
              <TableCell sx={{ overflowWrap: 'break-word' }}>{row.itemName ?? row.itemId ?? ''}</TableCell>
              <TableCell sx={{ fontFamily: 'monospace', overflowWrap: 'break-word' }}>{row.column}</TableCell>
              <TableCell>
                {row.uris ? <UriList uris={row.uris.before} /> : <DiffCell value={row.before} />}
              </TableCell>
              <TableCell>
                {row.uris ? <UriList uris={row.uris.after} /> : <DiffCell value={row.after} />}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function EntryMeta({ entry }: { entry: QueueEntry }) {
  const formatTime = useFormatTime()
  const parts = [`#${entry.id}`, `from ${entry.source}`, formatTime(entry.createdAt)]
  if (entry.operation.kind) {
    parts.push(entry.operation.kind)
  }
  if (entry.status !== 'pending' && entry.decidedAt) {
    parts.push(`${entry.status} ${formatTime(entry.decidedAt)}`)
  }
  return (
    <Typography variant="body2" color="text.secondary">
      {parts.join(' · ')}
    </Typography>
  )
}

function EntryCard({
  entry,
  selected,
  onToggle,
  onDismiss,
  onRetry,
  busy,
}: {
  entry: QueueEntry
  selected?: boolean
  onToggle?: (id: number) => void
  onDismiss?: (id: number) => void
  onRetry?: (entry: QueueEntry) => void
  // A dismiss or retry of the entry runs.
  busy?: boolean
}) {
  const note = noRetryNote(entry)
  return (
    <Paper variant="outlined" sx={{ p: 2 }} data-testid={`queue-entry-${entry.id}`}>
      <Stack spacing={1}>
        {onToggle ? (
          <FormControlLabel
            control={<Checkbox checked={selected ?? false} onChange={() => onToggle(entry.id)} />}
            label={<Typography sx={{ fontWeight: 'medium' }}>{entry.summary}</Typography>}
          />
        ) : (
          <Typography sx={{ fontWeight: 'medium' }}>{entry.summary}</Typography>
        )}
        <EntryMeta entry={entry} />
        {entry.diffError && <Alert severity="warning">This change no longer fits the vault: {entry.diffError}</Alert>}
        {entry.status === 'failed' && (
          <Alert
            severity="error"
            action={
              <Stack direction="row" spacing={1}>
                {onRetry && retryKind(entry) && (
                  <Button color="inherit" size="small" disabled={busy} onClick={() => onRetry(entry)}>
                    Retry
                  </Button>
                )}
                {onDismiss && (
                  <Button color="inherit" size="small" disabled={busy} onClick={() => onDismiss(entry.id)}>
                    Dismiss
                  </Button>
                )}
              </Stack>
            }
          >
            Failed: {failureReason(entry)}
            {note && (
              <Box component="span" sx={{ display: 'block' }}>
                {note}
              </Box>
            )}
          </Alert>
        )}
        {entry.status === 'rejected' && entry.result?.detail && (
          <Alert severity="warning">Refused: {entry.result.detail}</Alert>
        )}
        {entry.status === 'applied' && entry.result?.backup && (
          <Typography variant="body2" color="text.secondary">
            Backup <Box component="span" sx={{ fontFamily: 'monospace' }}>{entry.result.backup}</Box>
          </Typography>
        )}
        <DiffTable entry={entry} />
      </Stack>
    </Paper>
  )
}

interface Decided {
  outcome: ApproveOutcome
  // Summaries of the approved entries, taken before the queue reloads.
  summaries: Map<number, string>
}

function ApproveResult({ decided }: { decided: Decided }) {
  const failedEntries = useQueue('failed')
  const { outcome, summaries } = decided
  const reasons = new Map((failedEntries.data ?? []).map((entry) => [entry.id, failureReason(entry)]))
  const name = (id: number) => summaries.get(id) ?? `Entry ${id}`
  return (
    <Stack spacing={1} sx={{ mb: 2 }} data-testid="approve-result">
      {outcome.applied.length > 0 && (
        <Alert severity="success">
          Applied {outcome.applied.length} {outcome.applied.length === 1 ? 'entry' : 'entries'}
          {outcome.backup ? ` (backup ${outcome.backup})` : ''}:
          <Box component="ul" sx={{ m: 0, pl: 3 }}>
            {outcome.applied.map((id) => (
              <li key={id}>{name(id)}</li>
            ))}
          </Box>
        </Alert>
      )}
      {outcome.failed.length > 0 && (
        <Alert severity="error">
          Failed {outcome.failed.length} {outcome.failed.length === 1 ? 'entry' : 'entries'}:
          <Box component="ul" sx={{ m: 0, pl: 3 }}>
            {outcome.failed.map((id) => (
              <li key={id}>
                {name(id)}: {reasons.get(id) ?? (failedEntries.isPending ? 'loading the reason' : 'no reason given')}
              </li>
            ))}
          </Box>
        </Alert>
      )}
      {outcome.pending.length > 0 && (
        <Alert severity="info">
          {outcome.pending.length} {outcome.pending.length === 1 ? 'entry is' : 'entries are'} pending again,
          because a write before {outcome.pending.length === 1 ? 'it' : 'them'} failed.
        </Alert>
      )}
    </Stack>
  )
}

function PendingTab() {
  const queryClient = useQueryClient()
  const pending = useQueue('pending')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [decided, setDecided] = useState<Decided | null>(null)
  const [rejected, setRejected] = useState<number | null>(null)

  const entries = pending.data ?? []
  const pendingIds = entries.map((entry) => entry.id)
  const chosen = pendingIds.filter((id) => selected.has(id))

  function toggle(id: number) {
    setSelected((old) => toggled(old, id, !old.has(id)))
  }

  // The vault, the change log and every queue list may have changed.
  async function reload() {
    await queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== sessionQueryKey[0] })
  }

  async function approve(ids: number[]) {
    setBusy(true)
    setError(null)
    setRejected(null)
    setDecided(null)
    const summaries = new Map(entries.map((entry) => [entry.id, entry.summary]))
    try {
      const approving = approveEntries(ids)
      // The app bar's spinner follows the status, which says committing while the approve runs.
      void queryClient.invalidateQueries({ queryKey: autoCommitQueryKey })
      const outcome = await approving
      setDecided({ outcome, summaries })
      setSelected(new Set())
    } catch (failure) {
      setError(`Could not approve: ${failure instanceof Error ? failure.message : String(failure)}`)
    } finally {
      setBusy(false)
      await reload()
    }
  }

  async function reject(ids: number[]) {
    setBusy(true)
    setError(null)
    setDecided(null)
    setRejected(null)
    try {
      const answer = await rejectEntries(ids)
      setRejected(answer.rejected.length)
      setSelected(new Set())
    } catch (failure) {
      setError(`Could not reject: ${failure instanceof Error ? failure.message : String(failure)}`)
    } finally {
      setBusy(false)
      await reload()
    }
  }

  const allChosen = entries.length > 0 && chosen.length === entries.length
  return (
    <Box>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {decided && <ApproveResult decided={decided} />}
      {rejected !== null && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Rejected {rejected} {rejected === 1 ? 'entry' : 'entries'}.
        </Alert>
      )}
      {pending.isPending ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', mt: 6 }}>
          <CircularProgress aria-label="Loading the queue" />
        </Box>
      ) : pending.isError ? (
        <Alert severity="error">Could not load the queue: {pending.error.message}</Alert>
      ) : entries.length === 0 ? (
        <Typography>No changes waiting for review.</Typography>
      ) : (
        <Stack spacing={2}>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
            <FormControlLabel
              control={
                <Checkbox
                  checked={allChosen}
                  indeterminate={chosen.length > 0 && !allChosen}
                  onChange={() => setSelected(new Set(allChosen ? [] : pendingIds))}
                />
              }
              label="Select all"
            />
            <Button variant="contained" disabled={busy || chosen.length === 0} onClick={() => approve(chosen)}>
              Approve selected
            </Button>
            <Button variant="outlined" color="error" disabled={busy || chosen.length === 0} onClick={() => reject(chosen)}>
              Reject selected
            </Button>
            <Button variant="outlined" disabled={busy} onClick={() => approve(pendingIds)}>
              Approve all
            </Button>
            {busy && <CircularProgress size={24} aria-label="Working" />}
          </Stack>
          {entries.map((entry) => (
            <EntryCard key={entry.id} entry={entry} selected={selected.has(entry.id)} onToggle={toggle} />
          ))}
        </Stack>
      )}
    </Box>
  )
}

// Asks before a conflict is retried, as that writes over the change made elsewhere.
function OverwriteDialog({
  entry,
  onCancel,
  onRetry,
}: {
  entry: QueueEntry
  onCancel: () => void
  onRetry: () => void
}) {
  const titleId = useId()
  return (
    <Dialog open onClose={onCancel} aria-labelledby={titleId} fullWidth maxWidth="sm">
      <DialogTitle id={titleId}>Write over the other change?</DialogTitle>
      <DialogContent>
        {conflictLines(entry).map((line) => (
          <DialogContentText key={line}>{line}</DialogContentText>
        ))}
        <DialogContentText sx={{ mt: 1 }}>
          Retry writes your value over that change. A backup is taken first, so the other value can be restored from
          the Change log.
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="contained" onClick={onRetry}>
          Retry
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function DecidedTab({ status }: { status: Exclude<QueueStatus, 'pending' | 'approved'> }) {
  const list = useQueue(status)
  const queryClient = useQueryClient()
  const { forgetFailed } = useAutoCommit()
  const [busy, setBusy] = useState<ReadonlySet<number>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const [decided, setDecided] = useState<Decided | null>(null)
  const [confirming, setConfirming] = useState<QueueEntry | null>(null)

  // A dismissed entry is deleted, and the grid drops the marks it left there.
  async function dismiss(id: number) {
    setBusy((old) => toggled(old, id, true))
    setError(null)
    setDecided(null)
    try {
      await dismissEntries([id])
      forgetFailed([id])
    } catch (failure) {
      setError(`Could not dismiss: ${failure instanceof Error ? failure.message : String(failure)}`)
    } finally {
      await queryClient.invalidateQueries({ queryKey: queueKey(status) })
      setBusy((old) => toggled(old, id, false))
    }
  }

  // The entry is applied at once like an approval, its old failure leaves the grid.
  async function retry(entry: QueueEntry, overwrite: boolean) {
    setConfirming(null)
    setBusy((old) => toggled(old, entry.id, true))
    setError(null)
    setDecided(null)
    try {
      const outcome = await retryEntries([entry.id], overwrite)
      forgetFailed([entry.id])
      setDecided({ outcome, summaries: new Map([[entry.id, entry.summary]]) })
    } catch (failure) {
      const text = failure instanceof ApiError ? (failure.detail ?? failure.message) : String(failure)
      setError(`Could not retry: ${text}`)
    } finally {
      // The vault, the change log and every queue list may have changed.
      await queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== sessionQueryKey[0] })
      setBusy((old) => toggled(old, entry.id, false))
    }
  }

  function askRetry(entry: QueueEntry) {
    if (retryKind(entry) === 'overwrite') {
      setConfirming(entry)
    } else {
      void retry(entry, false)
    }
  }

  if (list.isPending) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', mt: 6 }}>
        <CircularProgress aria-label={`Loading the ${status} entries`} />
      </Box>
    )
  }
  if (list.isError) {
    return <Alert severity="error">Could not load the {status} entries: {list.error.message}</Alert>
  }
  const alert = (
    <>
      {error && <Alert severity="error">{error}</Alert>}
      {decided && <ApproveResult decided={decided} />}
    </>
  )
  if (list.data.length === 0) {
    return (
      <Stack spacing={2}>
        {alert}
        <Typography>No {status} entries.</Typography>
      </Stack>
    )
  }
  // Newest decision first, which is what an audit view is read for.
  const entries = [...list.data].reverse()
  const failed = status === 'failed'
  return (
    <Stack spacing={2}>
      {alert}
      {entries.map((entry) => (
        <EntryCard
          key={entry.id}
          entry={entry}
          onDismiss={failed ? dismiss : undefined}
          onRetry={failed ? askRetry : undefined}
          busy={busy.has(entry.id)}
        />
      ))}
      {confirming && (
        <OverwriteDialog
          entry={confirming}
          onCancel={() => setConfirming(null)}
          onRetry={() => void retry(confirming, true)}
        />
      )}
    </Stack>
  )
}

function inRange(text: string, [low, high]: readonly [number, number]): boolean {
  const value = Number(text)
  return /^[0-9]+$/.test(text.trim()) && value >= low && value <= high
}

function AutoCommitForm({ settings }: { settings: AutoCommitSettings }) {
  const queryClient = useQueryClient()
  const [afterSeconds, setAfterSeconds] = useState(String(settings.afterSeconds))
  const [maxEntries, setMaxEntries] = useState(String(settings.maxEntries))
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ severity: 'success' | 'error'; text: string } | null>(null)
  const afterOk = inRange(afterSeconds, AFTER_SECONDS_RANGE)
  const maxOk = inRange(maxEntries, MAX_ENTRIES_RANGE)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!afterOk || !maxOk) {
      return
    }
    setSaving(true)
    setMessage(null)
    try {
      await saveAutoCommit({ afterSeconds: Number(afterSeconds), maxEntries: Number(maxEntries) })
      setMessage({ severity: 'success', text: 'Saved.' })
    } catch (error) {
      const text = error instanceof ApiError ? (error.detail ?? error.message) : String(error)
      setMessage({ severity: 'error', text: `Could not save: ${text}` })
    } finally {
      setSaving(false)
      void queryClient.invalidateQueries({ queryKey: autoCommitQueryKey })
    }
  }

  return (
    <Paper variant="outlined" component="form" onSubmit={submit} noValidate sx={{ p: 2, mb: 2 }} aria-label="Auto-commit">
      <Typography variant="subtitle1" component="h3" gutterBottom>
        Auto-commit
      </Typography>
      <Prose sx={{ mb: 2 }}>
        Your edits in the grid are written together, when the oldest has waited this long or when this many wait.
      </Prose>
      <Stack direction="row" useFlexGap sx={{ flexWrap: 'wrap', alignItems: 'flex-start', gap: 2 }}>
        <TextField
          size="small"
          label="After seconds"
          value={afterSeconds}
          onChange={(event) => setAfterSeconds(event.target.value)}
          error={!afterOk}
          helperText={`${AFTER_SECONDS_RANGE[0]} to ${AFTER_SECONDS_RANGE[1]}`}
          slotProps={{ htmlInput: { ...NO_PASSWORD_MANAGER, inputMode: 'numeric' } }}
        />
        <TextField
          size="small"
          label="Or at changes"
          value={maxEntries}
          onChange={(event) => setMaxEntries(event.target.value)}
          error={!maxOk}
          helperText={`${MAX_ENTRIES_RANGE[0]} to ${MAX_ENTRIES_RANGE[1]}`}
          slotProps={{ htmlInput: { ...NO_PASSWORD_MANAGER, inputMode: 'numeric' } }}
        />
        <Button type="submit" variant="contained" disabled={saving || !afterOk || !maxOk}>
          Save
        </Button>
      </Stack>
      {message && (
        <Alert severity={message.severity} onClose={() => setMessage(null)} sx={{ mt: 2 }}>
          {message.text}
        </Alert>
      )}
    </Paper>
  )
}

// Waits for the stored settings, so the form starts from them.
function AutoCommitBox() {
  const status = useAutoCommitStatus().data
  if (!status) {
    return null
  }
  return <AutoCommitForm settings={status} />
}

const TABS: { value: QueueTab; label: string; help: string }[] = [
  {
    value: 'pending',
    label: 'Pending',
    help: 'Waiting for your approval (agent proposals) or for the next auto-commit (your grid edits).',
  },
  { value: 'applied', label: 'Applied', help: 'Written to the vault.' },
  {
    value: 'rejected',
    label: 'Rejected',
    help: 'Not written: you rejected them, or bwtidy refused a proposal that would put a secret value into a plain column.',
  },
  {
    value: 'failed',
    label: 'Failed',
    help: 'Approved, but not written. The reason says why. After a cut-off, the write may still have happened.',
  },
]

function ids(entries: QueueEntry[] | undefined): number[] | undefined {
  return entries?.map((entry) => entry.id)
}

export default function QueuePage() {
  const [tab, setTab] = useState<QueueTab>('pending')
  const pending = useQueue('pending')
  const applied = useQueue('applied', QUEUE_POLL_MS)
  const rejected = useQueue('rejected', QUEUE_POLL_MS)
  const failed = useQueue('failed', QUEUE_POLL_MS)
  const unread = useUnreadCounts(tab, {
    pending: ids(pending.data),
    applied: ids(applied.data),
    rejected: ids(rejected.data),
    failed: ids(failed.data),
  })
  return (
    <Box sx={{ p: 2, overflow: 'auto' }}>
      <Typography variant="h5" component="h2" gutterBottom>
        Queue
      </Typography>
      <Prose sx={{ mb: 1 }}>
        Your edits in the grid wait here until the auto-commit writes them. Changes the agent proposed through MCP
        are written only when you approve them. An approved batch is one operation with one backup. Secret values
        are masked.
      </Prose>
      <AutoCommitBox />
      <Tabs value={tab} onChange={(_, value: QueueTab) => setTab(value)} sx={{ mb: 2 }} aria-label="Queue entries">
        {TABS.map(({ value, label }) => (
          <Tab key={value} value={value} label={unread[value] > 0 ? `${label} (${unread[value]})` : label} />
        ))}
      </Tabs>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {TABS.find(({ value }) => value === tab)?.help}
      </Typography>
      {tab === 'pending' ? <PendingTab /> : <DecidedTab status={tab} />}
    </Box>
  )
}
