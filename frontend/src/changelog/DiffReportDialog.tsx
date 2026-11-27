import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import { useId } from 'react'
import { useFormatTime } from '../shared/DateFormat.tsx'
import { copyFeedback, useCopy } from '../shared/useCopy.ts'
import type { ChangeLogEntry } from '../api/changeLog.ts'
import { DIFF_HEADER, diffLines, diffSummary, diffText } from './diffReport.ts'

// Lists every operation on every field of the shown change rows, never a value, and copies it as
// tab separated text.
export default function DiffReportDialog({
  entries,
  names,
  folderNames,
  onClose,
}: {
  entries: ChangeLogEntry[]
  names: ReadonlyMap<string, string>
  folderNames: ReadonlyMap<string, string>
  onClose: () => void
}) {
  const titleId = useId()
  const formatTime = useFormatTime()
  const { state, copy } = useCopy()
  const lines = diffLines(entries, names, folderNames, formatTime)
  return (
    <Dialog open onClose={onClose} aria-labelledby={titleId} fullWidth maxWidth="lg">
      <DialogTitle id={titleId}>Diff report</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 1 }}>{diffSummary(entries.length, lines.length)}</DialogContentText>
        {/* Focusable, so the keyboard can scroll a long report: it holds no control of its own. */}
        <TableContainer tabIndex={0} role="region" aria-label="Diff report rows" sx={{ maxHeight: '60vh' }}>
          <Table size="small" stickyHeader aria-label="Diff report">
            <TableHead>
              <TableRow>
                {DIFF_HEADER.map((title) => (
                  <TableCell key={title}>{title}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {lines.map((line, index) => (
                <TableRow key={index}>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{line.time}</TableCell>
                  <TableCell>{line.source}</TableCell>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{line.operation}</TableCell>
                  <TableCell>{line.item}</TableCell>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{line.field}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </DialogContent>
      <DialogActions>
        <Typography role="status" variant="body2" color="text.secondary" sx={{ mr: 'auto', pl: 1 }}>
          {copyFeedback(state)}
        </Typography>
        <Button onClick={() => void copy(diffText(lines))}>Copy</Button>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}
