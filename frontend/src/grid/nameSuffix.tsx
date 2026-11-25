import { Box, SvgIcon, Tooltip } from '@mui/material'
import { createContext, type ReactNode } from 'react'
import { TEMPLATE_COLUMN } from '../api/grid.ts'
import { shownValue, type ShownRow } from './queued.ts'
import type { TemplateMatches } from '../api/templates.ts'
import { TYPE_LABELS } from '../shared/templateRules.ts'
import { matchLabel } from './matches.ts'

// What a suffix may read besides its row.
interface SuffixData {
  matches: TemplateMatches | undefined
}

// A suffix stands for one column and shows in the name cell while that column is out of view.
// Another suffix is one more entry in NAME_SUFFIXES.
interface NameSuffix {
  columnId: string
  render: (row: ShownRow, data: SuffixData) => ReactNode
}

// Material Design icon paths (Apache 2.0), so the app needs no icon package.
const TYPE_ICONS: Record<string, string> = {
  login:
    'M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2m6.93 6h-2.95c-.32-1.25-.78-2.45-1.38-3.56 1.84.63 3.37 1.91 4.33 3.56M12 4.04c.83 1.2 1.48 2.53 1.91 3.96h-3.82c.43-1.43 1.08-2.76 1.91-3.96M4.26 14C4.1 13.36 4 12.69 4 12s.1-1.36.26-2h3.38c-.08.66-.14 1.32-.14 2s.06 1.34.14 2zm.82 2h2.95c.32 1.25.78 2.45 1.38 3.56-1.84-.63-3.37-1.9-4.33-3.56m2.95-8H5.08c.96-1.66 2.49-2.93 4.33-3.56C8.81 5.55 8.35 6.75 8.03 8M12 19.96c-.83-1.2-1.48-2.53-1.91-3.96h3.82c-.43 1.43-1.08 2.76-1.91 3.96M14.34 14H9.66c-.09-.66-.16-1.32-.16-2s.07-1.35.16-2h4.68c.09.65.16 1.32.16 2s-.07 1.34-.16 2m.25 5.56c.6-1.11 1.06-2.31 1.38-3.56h2.95c-.96 1.65-2.49 2.93-4.33 3.56M16.36 14c.08-.66.14-1.32.14-2s-.06-1.34-.14-2h3.38c.16.64.26 1.31.26 2s-.1 1.36-.26 2z',
  note: 'M19 3H4.99C3.89 3 3 3.9 3 5l.01 14c0 1.1.89 2 1.99 2h10l6-6V5c0-1.1-.9-2-2-2M7 8h10v2H7zm5 6H7v-2h5zm2 5.5V14h5.5z',
  card: 'M20 4H4c-1.11 0-1.99.89-1.99 2L2 18c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V6c0-1.11-.89-2-2-2m0 14H4v-6h16zm0-10H4V6h16z',
  identity: 'M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4m0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4',
  sshKey:
    'M12.65 10C11.83 7.67 9.61 6 7 6c-3.31 0-6 2.69-6 6s2.69 6 6 6c2.61 0 4.83-1.67 5.65-4H17v4h4v-4h2v-4zM7 14c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2',
}

const TEMPLATE_ICON = 'M3 13h8V3H3zm0 8h8v-6H3zm10 0h8V11h-8zm0-18v6h8V3z'

// The name cell's own tooltip stays away while the pointer is on a suffix, whose hover text is its own.
export const SUFFIX_CLASS = 'bw-name-suffix'

function typeSuffix(type: string): ReactNode {
  const label = TYPE_LABELS[type] ?? type
  const path = TYPE_ICONS[type]
  if (!path) {
    return (
      <Box component="span" sx={{ color: 'text.secondary', flexShrink: 0 }}>
        {label}
      </Box>
    )
  }
  return (
    <Tooltip title={label} placement="top" disableInteractive>
      <Box
        component="span"
        role="img"
        className={SUFFIX_CLASS}
        data-suffix="type"
        sx={{ display: 'inline-flex', flexShrink: 0, fontSize: 16, color: 'text.secondary' }}
      >
        <SvgIcon fontSize="inherit">
          <path d={path} />
        </SvgIcon>
      </Box>
    </Tooltip>
  )
}

function templateSuffix(label: string): ReactNode {
  return (
    <Box
      component="span"
      data-suffix="template"
      sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.25, minWidth: 0, flexShrink: 1, color: 'text.secondary' }}
    >
      <SvgIcon sx={{ fontSize: 14, flexShrink: 0 }} aria-hidden>
        <path d={TEMPLATE_ICON} />
      </SvgIcon>
      <Box component="span" sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {label}
      </Box>
    </Box>
  )
}

export const NAME_SUFFIXES: readonly NameSuffix[] = [
  {
    columnId: 'type',
    render: (row) => {
      const type = shownValue(row, 'type')
      return typeof type === 'string' ? typeSuffix(type) : null
    },
  },
  {
    columnId: TEMPLATE_COLUMN,
    render: (row, { matches }) => {
      const label = matchLabel(matches?.[row.id])
      return label ? templateSuffix(label) : null
    },
  },
]

// The entries whose column is out of view, in registry order.
export function shownSuffixes(outOfView: ReadonlySet<string>, suffixes: readonly NameSuffix[] = NAME_SUFFIXES): NameSuffix[] {
  return suffixes.filter((suffix) => outOfView.has(suffix.columnId))
}

interface ColumnView {
  isVisible(): boolean
  getPinned(): unknown
  getLeft(): number | null
  getActualWidth(): number
}

// The part of the grid's API that tells what is in view. GridApi has it (getHorizontalPixelRange needs ScrollApiModule).
export interface ViewApi {
  getColumn(id: string): ColumnView | null
  getHorizontalPixelRange(): { left: number; right: number }
}

// A column less than this share inside the viewport counts as out of view.
const IN_VIEW_SHARE = 0.9

// Hidden columns, and visible ones the horizontal scroll leaves less than 90% of.
// A pinned column is always in view. Without a layout (a zero wide viewport, as in jsdom) every visible column counts as in view.
export function outOfViewColumns(api: ViewApi, columnIds: readonly string[] = NAME_SUFFIXES.map((s) => s.columnId)): Set<string> {
  const range = api.getHorizontalPixelRange()
  const out = new Set<string>()
  for (const id of columnIds) {
    const column = api.getColumn(id)
    if (!column || !column.isVisible()) {
      out.add(id)
      continue
    }
    const width = column.getActualWidth()
    if (column.getPinned() || range.right <= range.left || width <= 0) {
      continue
    }
    const left = column.getLeft() ?? 0
    const inside = Math.min(left + width, range.right) - Math.max(left, range.left)
    if (inside < width * IN_VIEW_SHARE) {
      out.add(id)
    }
  }
  return out
}

export interface NameSuffixState extends SuffixData {
  outOfView: ReadonlySet<string>
}

export const NameSuffixContext = createContext<NameSuffixState>({ outOfView: new Set(), matches: undefined })
