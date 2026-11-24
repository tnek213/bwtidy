import { alpha, type Theme } from '@mui/material'
import { FAILED_CELL_CLASS } from './cellMarks.ts'
import { SEARCH_HIT_CLASS, UNAVAILABLE_CELL } from './columns.tsx'
import { conflictStyles } from '../shared/conflicts.ts'
import { PLAIN_IN_SECRET_CLASS } from './editing.ts'
import { formatMarkStyles } from './formats.ts'
import { ownGeneratorMarkStyles } from './ownGenerator.ts'
import { QUEUED_CELL_CLASS, QUEUED_TRASH_CLASS } from './queued.ts'
import { TO_FILL_CELL_CLASS } from './toFill.ts'

// How the grid page's cells show their marks, for the sx of the box around the grid.
export function gridCellStyles(theme: Theme) {
  return {
    // First, so the tints of queued, failed and conflict cells lie on top.
    [`& .${UNAVAILABLE_CELL}`]: {
      backgroundColor: `color-mix(in srgb, ${theme.palette.action.hover} 35%, transparent)`,
    },
    // Amber at low alpha, so a queued value reads as not written yet without hiding the grid's own row colors.
    [`& .${QUEUED_CELL_CLASS}`]: { backgroundColor: 'rgba(255, 179, 0, 0.16)' },
    [`& .${QUEUED_TRASH_CLASS} .ag-cell`]: { opacity: 0.5, textDecoration: 'line-through' },
    [`& .${FAILED_CELL_CLASS}`]: { backgroundColor: 'rgba(211, 47, 47, 0.16)' },
    // An outline, so it shows beside the tints of queued, failed and searched cells.
    [`& .${TO_FILL_CELL_CLASS}`]: { outline: '2px solid rgba(237, 108, 2, 0.8)', outlineOffset: '-2px' },
    '& .ag-tooltip': { whiteSpace: 'pre-line' },
    ...conflictStyles,
    ...formatMarkStyles(theme),
    ...ownGeneratorMarkStyles(theme),
    // A corner at the top left: the format dot has the top right, the tints the background.
    [`& .${PLAIN_IN_SECRET_CLASS}::before`]: {
      content: '""',
      position: 'absolute',
      top: 0,
      left: 0,
      borderTop: `8px solid ${theme.palette.warning.main}`,
      borderRight: '8px solid transparent',
      pointerEvents: 'none',
    },
    // Blue at low alpha: amber is taken by queued values. Row hover and focus stay visible.
    [`& .${SEARCH_HIT_CLASS}`]: { backgroundColor: alpha(theme.palette.info.main, 0.12) },
  }
}
