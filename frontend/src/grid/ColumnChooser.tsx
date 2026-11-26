import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  ListSubheader,
  Popover,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { useState } from 'react'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import { toggled } from '../shared/sets.ts'
import { GROUP_ORDER } from '../shared/coordinates.ts'
import { type GridColumn, NAME_COLUMN } from '../api/grid.ts'
import { isMergeable } from './normalize.ts'
import { GROUP_LABELS } from './labels.ts'

interface ColumnChooserProps {
  columns: GridColumn[]
  hidden: ReadonlySet<string>
  onChange: (hidden: Set<string>) => void
  // Columns picked for a merge, a second checkbox next to the visibility one.
  selected: ReadonlySet<string>
  onSelected: (selected: Set<string>) => void
  onMergeSelected: () => void
}

export default function ColumnChooser(props: ColumnChooserProps) {
  const { columns, hidden, onChange, selected, onSelected } = props
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const [search, setSearch] = useState('')
  const needle = search.trim().toLowerCase()
  const matching = columns.filter((column) => column.label.toLowerCase().includes(needle))

  function toggle(id: string, visible: boolean) {
    onChange(toggled(hidden, id, !visible))
  }

  // Shows every listed column of a group when none is shown or some are, else hides them. name stays.
  function toggleGroup(listed: GridColumn[], show: boolean) {
    const next = new Set(hidden)
    for (const column of listed) {
      if (column.id === NAME_COLUMN) {
        continue
      }
      if (show) {
        next.delete(column.id)
      } else {
        next.add(column.id)
      }
    }
    onChange(next)
  }

  function select(id: string, on: boolean) {
    onSelected(toggled(selected, id, on))
  }

  return (
    <>
      <Button variant="outlined" onClick={(event) => setAnchor(event.currentTarget)}>
        Columns{hidden.size > 0 ? ` (${hidden.size} hidden)` : ''}
      </Button>
      <Popover
        open={anchor !== null}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Box sx={{ width: 320, maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
          <Stack direction="row" spacing={1} sx={{ p: 1.5, pb: 1 }}>
            <TextField
              size="small"
              label="Find column"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              sx={{ flexGrow: 1 }}
              slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
            />
            <Button size="small" onClick={() => onChange(new Set())} disabled={hidden.size === 0}>
              Show all
            </Button>
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ px: 1.5 }}>
            The box on the right selects a column for a merge.
          </Typography>
          <Box sx={{ overflowY: 'auto', px: 1.5, pb: 1 }}>
            {GROUP_ORDER.map((group) => {
              const inGroup = matching.filter((column) => column.group === group)
              if (inGroup.length === 0) {
                return null
              }
              const toggleable = inGroup.filter((column) => column.id !== NAME_COLUMN)
              const shown = toggleable.filter((column) => !hidden.has(column.id)).length
              return (
                <Box key={group} role="group" aria-label={GROUP_LABELS[group]}>
                  <ListSubheader component="div" disableGutters sx={{ lineHeight: '32px' }}>
                    {toggleable.length > 0 ? (
                      <FormControlLabel
                        control={
                          <Checkbox
                            size="small"
                            checked={shown === toggleable.length}
                            indeterminate={shown > 0 && shown < toggleable.length}
                            onChange={() => toggleGroup(toggleable, shown === 0)}
                          />
                        }
                        label={GROUP_LABELS[group]}
                      />
                    ) : (
                      GROUP_LABELS[group]
                    )}
                  </ListSubheader>
                  {inGroup.map((column) => (
                    <Stack key={column.id} direction="row" sx={{ alignItems: 'center' }}>
                      <FormControlLabel
                        sx={{ flexGrow: 1, mr: 0, minWidth: 0 }}
                        control={
                          <Checkbox
                            size="small"
                            checked={!hidden.has(column.id)}
                            disabled={column.id === NAME_COLUMN}
                            onChange={(event) => toggle(column.id, event.target.checked)}
                          />
                        }
                        label={column.label}
                      />
                      {isMergeable(column) && (
                        <Tooltip title="Select for merge">
                          <Checkbox
                            size="small"
                            color="secondary"
                            checked={selected.has(column.id)}
                            onChange={(event) => select(column.id, event.target.checked)}
                            slotProps={{ input: { 'aria-label': `Select ${column.label} for merge` } }}
                          />
                        </Tooltip>
                      )}
                    </Stack>
                  ))}
                </Box>
              )
            })}
          </Box>
          <Stack direction="row" spacing={1} sx={{ p: 1.5, pt: 1, borderTop: 1, borderColor: 'divider', alignItems: 'center' }}>
            <Button
              size="small"
              variant="contained"
              disabled={selected.size < 2}
              sx={{ whiteSpace: 'nowrap' }}
              onClick={() => {
                setAnchor(null)
                props.onMergeSelected()
              }}
            >
              Merge selected columns
            </Button>
            <Button size="small" disabled={selected.size === 0} onClick={() => onSelected(new Set())}>
              Clear
            </Button>
          </Stack>
        </Box>
      </Popover>
    </>
  )
}
