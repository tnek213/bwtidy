import {
  Alert,
  Box,
  CircularProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Typography,
} from '@mui/material'
import { useMemo, useState } from 'react'
import { type StatisticsLimits, type StatisticsRow, useStatistics } from '../api/statistics.ts'
import Prose from '../theme/Prose.tsx'

type SortValue = string | number | null

interface RowColumn {
  label: string
  numeric?: boolean
  value: (row: StatisticsRow) => SortValue
  show?: (row: StatisticsRow) => string
}

function largestRepeat(row: StatisticsRow): number | null {
  return row.repeatsUsed.length > 0 ? Math.max(...row.repeatsUsed) : null
}

const ROW_COLUMNS: RowColumn[] = [
  { label: 'Item', value: (row) => row.item },
  { label: 'Column', value: (row) => row.column },
  { label: 'Datatype', value: (row) => row.datatype },
  { label: 'Length', numeric: true, value: (row) => row.length },
  { label: 'Distance', numeric: true, value: (row) => row.distance },
  {
    label: 'Largest repeat used',
    numeric: true,
    value: largestRepeat,
    show: (row) => {
      const used = largestRepeat(row)
      return used === null ? '' : `${used} of ${row.maxRepeat}`
    },
  },
  { label: 'Steps', numeric: true, value: (row) => row.steps },
  { label: 'ms', numeric: true, value: (row) => row.ms },
  { label: 'Too long', value: (row) => (row.tooLong ? 'Yes' : '') },
]

const MS_COLUMN = 'ms'

// A missing number sorts below every number, whichever way the column is turned.
function compare(a: SortValue, b: SortValue): number {
  if (a === b) {
    return 0
  }
  if (a === null || b === null) {
    return a === null ? -1 : 1
  }
  if (typeof a === 'number' && typeof b === 'number') {
    return a - b
  }
  return String(a).localeCompare(String(b))
}

function LimitsSection({ limits }: { limits: StatisticsLimits }) {
  const listed = [
    ['Pattern length', limits.maxPatternLen],
    ['Repeat count', limits.maxRepeat],
    ['States', limits.maxStates],
    ['Nesting depth', limits.maxDepth],
    ['Value length', limits.maxValueLen],
  ] as const
  return (
    <Box component="section" aria-labelledby="statistics-limits">
      <Typography id="statistics-limits" variant="h6" component="h3" gutterBottom>
        Limits
      </Typography>
      <TableContainer component={Paper} variant="outlined" sx={{ width: 'fit-content' }}>
        <Table size="small" aria-labelledby="statistics-limits">
          <TableBody>
            {listed.map(([label, limit]) => (
              <TableRow key={label}>
                <TableCell component="th" scope="row">
                  {label}
                </TableCell>
                <TableCell align="right">{limit}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  )
}

function RowsTable({ rows }: { rows: StatisticsRow[] }) {
  const [sortBy, setSortBy] = useState(MS_COLUMN)
  const [ascending, setAscending] = useState(false)
  const column = ROW_COLUMNS.find((candidate) => candidate.label === sortBy) ?? ROW_COLUMNS[0]
  const sorted = useMemo(() => {
    const direction = ascending ? 1 : -1
    return [...rows].sort((a, b) => direction * compare(column.value(a), column.value(b)))
  }, [rows, column, ascending])

  function sortOn(label: string) {
    setAscending(label === sortBy ? !ascending : false)
    setSortBy(label)
  }

  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small" aria-label="Scored values">
        <TableHead>
          <TableRow>
            {ROW_COLUMNS.map(({ label, numeric }) => (
              <TableCell
                key={label}
                align={numeric ? 'right' : 'left'}
                aria-sort={label === sortBy ? (ascending ? 'ascending' : 'descending') : undefined}
              >
                <TableSortLabel
                  active={label === sortBy}
                  direction={label === sortBy && ascending ? 'asc' : 'desc'}
                  onClick={() => sortOn(label)}
                >
                  {label}
                </TableSortLabel>
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {sorted.map((row) => (
            <TableRow key={`${row.itemId}/${row.column}/${row.datatype}`}>
              {ROW_COLUMNS.map(({ label, numeric, value, show }) => (
                <TableCell key={label} align={numeric ? 'right' : 'left'}>
                  {show ? show(row) : value(row)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

// How hard the datatype checks work, so the engine's limits can be tuned from real use.
export default function StatisticsPage() {
  const statistics = useStatistics()

  if (statistics.isPending) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', mt: 12 }}>
        <CircularProgress aria-label="Loading the statistics" />
      </Box>
    )
  }
  if (statistics.isError) {
    return (
      <Alert severity="error" sx={{ m: 2 }}>
        Could not load the statistics: {statistics.error.message}
      </Alert>
    )
  }

  const { limits, datatypes, rows, rowCount, reloads } = statistics.data
  return (
    <Box sx={{ p: 2, overflow: 'auto' }}>
      <Typography variant="h5" component="h2" gutterBottom>
        Statistics
      </Typography>
      <Prose sx={{ mb: 2 }}>
        How close values and patterns come to the limits of the datatype checks. The numbers are kept in memory for this
        session only. Vault reloads this session: {reloads}.
      </Prose>
      <Stack spacing={3}>
        <LimitsSection limits={limits} />
        <Box component="section" aria-labelledby="statistics-datatypes">
          <Typography id="statistics-datatypes" variant="h6" component="h3" gutterBottom>
            Datatypes
          </Typography>
          {datatypes.length === 0 ? (
            <Typography>No datatypes yet.</Typography>
          ) : (
            <TableContainer component={Paper} variant="outlined">
              <Table size="small" aria-labelledby="statistics-datatypes">
                <TableHead>
                  <TableRow>
                    <TableCell>Datatype</TableCell>
                    <TableCell align="right">States</TableCell>
                    <TableCell align="right">Largest repeat</TableCell>
                    <TableCell align="right">Shortest match</TableCell>
                    <TableCell align="right">Values scored</TableCell>
                    <TableCell align="right">Too long</TableCell>
                    <TableCell align="right">Slowest value, ms</TableCell>
                    <TableCell align="right">Last reload, ms</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {datatypes.map((datatype) => (
                    <TableRow key={datatype.id}>
                      <TableCell>{datatype.name}</TableCell>
                      <TableCell align="right">{datatype.states}</TableCell>
                      <TableCell align="right">{datatype.maxRepeat}</TableCell>
                      <TableCell align="right">{datatype.shortest}</TableCell>
                      <TableCell align="right">{datatype.valuesScored}</TableCell>
                      <TableCell align="right">{datatype.valuesTooLong}</TableCell>
                      <TableCell align="right">{datatype.slowestMs}</TableCell>
                      <TableCell align="right">{datatype.lastReloadMs}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Box>
        <Box component="section" aria-labelledby="statistics-rows">
          <Typography id="statistics-rows" variant="h6" component="h3" gutterBottom>
            Scored values
          </Typography>
          {rows.length === 0 ? (
            <Typography>No value has been scored against a datatype yet.</Typography>
          ) : (
            <>
              {rowCount > rows.length && (
                <Prose sx={{ mb: 1 }}>
                  The {rows.length} slowest of {rowCount}.
                </Prose>
              )}
              <RowsTable rows={rows} />
            </>
          )}
        </Box>
      </Stack>
    </Box>
  )
}
