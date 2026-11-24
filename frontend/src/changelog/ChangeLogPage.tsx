import {
  Alert,
  Box,
  Button,
  CircularProgress,
  IconButton,
  Link,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  type Theme,
  Tooltip,
  Typography,
} from '@mui/material'
import { type ReactNode, useEffect, useState } from 'react'
import { useGridData } from '../api/grid.ts'
import { operationLabel } from '../shared/operationLabels.ts'
import { useFormatTime } from '../shared/DateFormat.tsx'
import { type Backup, CHANGE_LOG_LIMIT, type ChangeLogEntry, downloadUrl, type RestoredBy, useBackups } from '../api/changeLog.ts'
import ChangeLogFilterBar from './ChangeLogFilterBar.tsx'
import { shownRows, useFilteredChangeLog } from './filters.ts'
import DiffReportDialog from './DiffReportDialog.tsx'
import DeleteBackupDialog from './DeleteBackupDialog.tsx'
import { RestoredIcon } from './icons.tsx'
import {
  attachmentText,
  backupNames,
  canRestore,
  columnsText,
  folderLabel,
  formatSize,
  mergeRows,
  restoredText,
  resultLabel,
  sharesBackupWithAbove,
  shownItemIds,
  sourceLabel,
} from './rows.ts'
import RestoreDialog from './RestoreDialog.tsx'
import Prose from '../theme/Prose.tsx'

const NEWEST = 'The newest backup cannot be deleted, the next write may reuse it.'

// A deleted backup leaves its name on the rows it was taken for, greyed and struck through.
const GONE = { color: 'text.disabled', textDecoration: 'line-through' } as const

// Mixes a color 43% toward the page background, for the repeated backup of a group.
export function faded(color: string, theme: Theme): string {
  return `color-mix(in srgb, ${color} 57%, ${theme.palette.background.default})`
}

const MONO_GREY = { fontFamily: 'monospace', fontSize: '0.75rem', color: 'text.secondary' } as const

function Columns({ columns }: { columns: string[] }) {
  if (columns.length === 0) {
    return null
  }
  const text = columnsText(columns)
  const line = (
    <Box component="span" sx={{ ...MONO_GREY, display: 'block' }}>
      {text}
    </Box>
  )
  const all = columns.join(', ')
  return all === text ? (
    line
  ) : (
    <Tooltip title={all} describeChild>
      {line}
    </Tooltip>
  )
}

function Operation({ kind, columns, mark }: { kind: string; columns: string[]; mark?: ReactNode }) {
  const known = operationLabel(kind)
  return (
    <Box component="span">
      {known ? (
        <Tooltip title={known.explanation} describeChild>
          <Box component="span">
            <Box component="span" sx={{ mr: 1 }}>
              {known.label}
            </Box>
            <Box component="span" sx={MONO_GREY}>
              {kind}
            </Box>
          </Box>
        </Tooltip>
      ) : (
        kind
      )}
      {mark}
      <Columns columns={columns} />
    </Box>
  )
}

const HIGHLIGHT_MS = 2000

// The mark of a change a later restore undid. It scrolls to the restore's row when that row is on the page.
function RestoredMark({ text, onShow }: { text: string; onShow: (() => void) | undefined }) {
  const icon = <RestoredIcon sx={{ fontSize: '1rem', verticalAlign: 'middle' }} />
  return (
    <Tooltip title={text}>
      {onShow ? (
        <IconButton size="small" aria-label={text} onClick={onShow} sx={{ ml: 0.5, p: 0.25 }}>
          {icon}
        </IconButton>
      ) : (
        <Box component="span" role="img" aria-label={text} sx={{ ml: 0.5, color: 'action.active' }}>
          {icon}
        </Box>
      )}
    </Tooltip>
  )
}

function rowId(id: number): string {
  return `change-log-row-${id}`
}

function RestoredFrom({
  entry,
  at,
  gone,
  onShow,
}: {
  entry: ChangeLogEntry
  // The time of the restored change when its row is on the page.
  at: string | undefined
  gone: boolean
  onShow: (id: number) => void
}) {
  const formatTime = useFormatTime()
  return (
    <Box sx={{ fontSize: '0.75rem', color: 'text.secondary' }}>
      From backup{' '}
      <Box
        component="span"
        sx={{ fontFamily: 'monospace', ...(gone ? GONE : {}) }}
        title={gone ? 'This backup was deleted' : undefined}
      >
        {entry.restoredBackup}
      </Box>
      {entry.restoredChangeId != null && at !== undefined && (
        <>
          {', '}
          <Link
            component="button"
            type="button"
            variant="inherit"
            sx={{ verticalAlign: 'baseline' }}
            onClick={() => onShow(entry.restoredChangeId as number)}
          >
            the change of {formatTime(at)}
          </Link>
        </>
      )}
    </Box>
  )
}

export default function ChangeLogPage() {
  const { filters, setFilters, filtered, matchedItems, fields, log } = useFilteredChangeLog()
  const grid = useGridData()
  const backups = useBackups()
  const formatTime = useFormatTime()
  const [restoring, setRestoring] = useState<ChangeLogEntry | null>(null)
  const [deleting, setDeleting] = useState<Backup | null>(null)
  const [reporting, setReporting] = useState(false)
  const [highlighted, setHighlighted] = useState<number | null>(null)
  useEffect(() => {
    if (highlighted === null) {
      return
    }
    const timer = setTimeout(() => setHighlighted(null), HIGHLIGHT_MS)
    return () => clearTimeout(timer)
  }, [highlighted])
  const names = new Map<string, string>()
  for (const row of grid.data?.rows ?? []) {
    if (typeof row.cells.name === 'string') {
      names.set(row.id, row.cells.name)
    }
  }
  const folderNames = new Map((grid.data?.folders ?? []).map((folder) => [folder.id, folder.name]))
  const existing = backupNames(backups.data)
  const newest = backups.data?.[0]?.name

  if (log.isPending) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', mt: 12 }}>
        <CircularProgress aria-label="Loading the change log" />
      </Box>
    )
  }
  if (log.isError) {
    return (
      <Alert severity="error" sx={{ m: 2 }}>
        Could not load the change log: {log.error.message}
      </Alert>
    )
  }
  const { entries, more } = log.data
  const rows = shownRows(mergeRows(entries, backups.data), entries, filtered)
  const times = new Map(entries.map((entry) => [entry.id, entry.at]))
  const show = (id: number) => {
    document.getElementById(rowId(id))?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setHighlighted(id)
  }
  const shared = sharesBackupWithAbove(rows)
  const markOf = (entry: ChangeLogEntry, restoredBy: RestoredBy) => (
    <RestoredMark
      text={restoredText(entry, restoredBy, names, formatTime)}
      onShow={times.has(restoredBy.id) ? () => show(restoredBy.id) : undefined}
    />
  )

  return (
    <Box sx={{ p: 2, overflow: 'auto' }}>
      <Typography variant="h5" component="h2" gutterBottom>
        Change log
      </Typography>
      <Prose sx={{ mb: 2 }}>
        The latest changes written to the vault and the backups, newest first. Values are never logged. Items that
        are no longer in the vault are shown by id, and so are folders. Restore puts the items or the folder of a change
        back as they were before it, from its backup, and a clock with an arrow back marks a change that a later restore
        undid. Hover an operation for what it did. A backup row downloads the encrypted file.
      </Prose>
      <ChangeLogFilterBar
        filters={filters}
        onFilters={setFilters}
        fields={fields}
        matchedItems={matchedItems}
        onDiffReport={() => setReporting(true)}
        canDiffReport={entries.length > 0}
      />
      {more && (
        <Typography variant="body2" sx={{ mb: 1 }}>
          Showing the newest {CHANGE_LOG_LIMIT} {filtered ? 'matches' : 'changes'}.
        </Typography>
      )}
      {rows.length === 0 ? (
        <Typography>{filtered ? 'No changes match the filters.' : 'No changes yet.'}</Typography>
      ) : (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small" aria-label="Change log">
            <TableHead>
              <TableRow>
                <TableCell>Time</TableCell>
                <TableCell>Source</TableCell>
                <TableCell>Operation</TableCell>
                <TableCell>Items</TableCell>
                <TableCell>Result</TableCell>
                <TableCell>Backup</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row, index) =>
                row.kind === 'change' ? (
                  <TableRow
                    key={`change-${row.entry.id}`}
                    id={rowId(row.entry.id)}
                    sx={{ transition: 'background-color 0.3s', bgcolor: highlighted === row.entry.id ? 'warning.light' : undefined }}
                  >
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatTime(row.entry.at)}</TableCell>
                    <TableCell>{sourceLabel(row.entry.source)}</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                      <Operation
                        kind={row.entry.operationKind}
                        columns={row.entry.columns}
                        mark={row.entry.restoredBy != null && markOf(row.entry, row.entry.restoredBy)}
                      />
                      {row.entry.restoredBackup && (
                        <RestoredFrom
                          entry={row.entry}
                          at={row.entry.restoredChangeId == null ? undefined : times.get(row.entry.restoredChangeId)}
                          gone={existing !== undefined && !existing.has(row.entry.restoredBackup)}
                          onShow={show}
                        />
                      )}
                    </TableCell>
                    <TableCell>
                      {row.entry.folderId
                        ? folderLabel(row.entry.folderId, folderNames)
                        : shownItemIds(row.entry)
                            .map((id) => names.get(id) ?? id)
                            .join(', ')}
                      {row.entry.attachmentIds && (
                        <Typography variant="caption" component="div" sx={{ fontFamily: 'monospace' }}>
                          {attachmentText(row.entry.attachmentIds)}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>{resultLabel(row.entry.result)}</TableCell>
                    <TableCell
                      sx={[
                        { fontFamily: 'monospace' },
                        shared[index] && ((theme: Theme) => ({ color: faded(theme.palette.text.primary, theme) })),
                      ]}
                    >
                      {row.entry.backup !== null && existing !== undefined && !existing.has(row.entry.backup) ? (
                        <Box component="span" sx={GONE} title="This backup was deleted">
                          {row.entry.backup}
                        </Box>
                      ) : (
                        (row.entry.backup ?? '')
                      )}
                    </TableCell>
                    <TableCell>
                      {canRestore(row.entry, existing, grid.data?.toolNoteId) && (
                        <Button
                          size="small"
                          onClick={() => setRestoring(row.entry)}
                          sx={shared[index] ? (theme) => ({ color: faded(theme.palette.primary.main, theme) }) : undefined}
                        >
                          Restore
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ) : (
                  <TableRow key={`backup-${row.backup.name}`} sx={{ bgcolor: 'action.hover' }}>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatTime(row.backup.time)}</TableCell>
                    <TableCell />
                    <TableCell>backup</TableCell>
                    <TableCell />
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatSize(row.backup.size)}</TableCell>
                    <TableCell sx={{ fontFamily: 'monospace' }}>
                      <Link href={downloadUrl(row.backup)} download={row.backup.name}>
                        {row.backup.name}
                      </Link>
                    </TableCell>
                    <TableCell>
                      {row.backup.name === newest ? (
                        <Tooltip title={NEWEST}>
                          <span>
                            <Button size="small" color="error" disabled>
                              Delete
                            </Button>
                          </span>
                        </Tooltip>
                      ) : (
                        <Button size="small" color="error" onClick={() => setDeleting(row.backup)}>
                          Delete
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ),
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      <Prose sx={{ mt: 2 }}>
        A backup is age encrypted with the master password it was taken under. Decrypt it on a tmpfs such as /dev/shm
        (<code>age -d -o backup.zip NAME.zip.age</code>), so no plain copy touches the disk. The steps for restoring a
        lost vault from it are in RESTORE.md in every backup, whose source is docs/restore.md in the bwtidy
        repository.
      </Prose>
      {restoring && <RestoreDialog entry={restoring} onClose={() => setRestoring(null)} />}
      {reporting && <DiffReportDialog entries={entries} names={names} folderNames={folderNames} onClose={() => setReporting(false)} />}
      {deleting && <DeleteBackupDialog name={deleting.name} onClose={() => setDeleting(null)} />}
    </Box>
  )
}
