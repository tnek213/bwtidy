import { Box, Chip, IconButton, Tooltip } from '@mui/material'
import type { GridApi, IRowNode } from 'ag-grid-community'
import type { CustomCellRendererProps } from 'ag-grid-react'
import { Fragment, use, useLayoutEffect } from 'react'
import { EyeIcon } from '../shared/EyeIcon.tsx'
import type { CellValue, GridColumn, GridRow } from '../api/grid.ts'
import { NameSuffixContext, shownSuffixes } from './nameSuffix.tsx'
import type { ShownRow } from './queued.ts'
import { useRevealedSecret } from './reveal.ts'
import { jsonKey, jsonValue, rareEntries } from './rareFields.ts'
import { isMasked, MASK } from '../shared/values.ts'

export function rowInView(api: Pick<GridApi, 'getVerticalPixelRange'>, node: IRowNode): boolean {
  const { rowTop, rowHeight } = node
  if (node.rowIndex === null || rowTop === null || !rowHeight) {
    return false
  }
  const { top, bottom } = api.getVerticalPixelRange()
  return rowTop < bottom && rowTop + rowHeight > top
}

// useRevealedSecret, which drops an answer for a row out of view, and a scroll
// that leaves the row out of view hides the revealed value and makes a pending
// answer stale.
// Unmounting alone is not enough: a click on the eye focuses the cell, and AG
// Grid keeps the focused cell's row rendered when it scrolls out of view.
function useRevealedInRow(itemId: string, columnId: string, api: GridApi, node: IRowNode) {
  const revealed = useRevealedSecret(itemId, columnId, () => rowInView(api, node))
  const { value, loading, hide } = revealed
  const active = loading || value !== null
  useLayoutEffect(() => {
    if (!active) {
      return
    }
    const onScroll = () => {
      if (!rowInView(api, node)) {
        hide()
      }
    }
    api.addEventListener('bodyScroll', onScroll)
    return () => {
      if (!api.isDestroyed()) {
        api.removeEventListener('bodyScroll', onScroll)
      }
    }
  }, [active, api, node, hide])
  return revealed
}

export function SecretCell(props: CustomCellRendererProps<GridRow, CellValue>) {
  const { data, column } = props
  if (!data || !column || props.value === undefined) {
    return null
  }
  return <RevealableSecret itemId={data.id} columnId={column.getColId()} api={props.api} node={props.node} />
}

function RevealableSecret({
  itemId,
  columnId,
  api,
  node,
}: {
  itemId: string
  columnId: string
  api: GridApi
  node: IRowNode
}) {
  const { value: revealed, failed, loading, toggle } = useRevealedInRow(itemId, columnId, api, node)
  const shown = revealed !== null
  return (
    <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, width: '100%', verticalAlign: 'middle' }}>
      <Box
        component="span"
        data-testid="secret-value"
        sx={{
          flexGrow: 1,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          fontFamily: shown ? 'monospace' : undefined,
          color: failed ? 'error.main' : undefined,
        }}
        title={failed ? 'Could not reveal the value' : undefined}
      >
        {shown ? revealed : MASK}
      </Box>
      <Tooltip title={shown ? 'Hide' : 'Reveal'}>
        <span>
          <IconButton
            size="small"
            aria-label={shown ? `Hide ${columnId}` : `Reveal ${columnId}`}
            disabled={loading}
            onClick={toggle}
          >
            <EyeIcon off={shown} />
          </IconButton>
        </span>
      </Tooltip>
    </Box>
  )
}

// One secret value of a Rare fields cell: an eye in place of its JSON value until it is revealed.
function RareSecret({ itemId, columnId, api, node }: { itemId: string; columnId: string; api: GridApi; node: IRowNode }) {
  const { value: revealed, failed, loading, toggle } = useRevealedInRow(itemId, columnId, api, node)
  const shown = revealed !== null
  return (
    <>
      {shown && (
        <Box component="span" data-testid="rare-secret-value" sx={{ fontFamily: 'monospace' }}>
          {jsonValue(revealed)}
        </Box>
      )}
      <Tooltip title={failed ? 'Could not reveal the value' : shown ? 'Hide' : 'Reveal'}>
        <span>
          <IconButton
            size="small"
            aria-label={shown ? `Hide ${columnId}` : `Reveal ${columnId}`}
            disabled={loading}
            onClick={toggle}
            sx={{ p: 0.25, color: failed ? 'error.main' : undefined }}
          >
            <EyeIcon off={shown} />
          </IconButton>
        </span>
      </Tooltip>
    </>
  )
}

export interface RareFieldsCellParams {
  // The rare columns in the grid's order.
  rareColumns: readonly GridColumn[]
}

// The rare columns the item uses as JSON, from the vault values (docs/spec.md, Grid).
export function RareFieldsCell(props: CustomCellRendererProps<ShownRow, string> & RareFieldsCellParams) {
  const { data, rareColumns } = props
  const entries = data ? rareEntries(data, rareColumns) : []
  if (!data || entries.length === 0) {
    return null
  }
  return (
    <Box component="span" data-testid="rare-fields" sx={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
      {'{ '}
      {entries.map((entry, index) => (
        <Fragment key={entry.columnId}>
          {index > 0 && ', '}
          {jsonKey(entry.key)}
          {isMasked(entry.value) ? (
            <RareSecret itemId={data.id} columnId={entry.columnId} api={props.api} node={props.node} />
          ) : (
            jsonValue(entry.value)
          )}
        </Fragment>
      ))}
      {' }'}
    </Box>
  )
}

export function ChipsCell(props: CustomCellRendererProps<GridRow, CellValue>) {
  const { value } = props
  if (!Array.isArray(value)) {
    return null
  }
  return (
    <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, maxWidth: '100%', overflow: 'hidden', verticalAlign: 'middle' }}>
      {value.map((text, index) => (
        <Chip key={`${index}:${text}`} label={text} size="small" />
      ))}
    </Box>
  )
}

// The name, then the suffix of every column that is out of view (see nameSuffix.tsx).
export function NameCell(props: CustomCellRendererProps<ShownRow, CellValue>) {
  const state = use(NameSuffixContext)
  const { data } = props
  const text = props.valueFormatted ?? (typeof props.value === 'string' ? props.value : '')
  const suffixes = data ? shownSuffixes(state.outOfView) : []
  if (suffixes.length === 0) {
    return text
  }
  return (
    <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.75, width: '100%', overflow: 'hidden', verticalAlign: 'middle' }}>
      <Box component="span" sx={{ minWidth: 0, flexShrink: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {text}
      </Box>
      {suffixes.map((suffix) => (
        <Fragment key={suffix.columnId}>{data && suffix.render(data, state)}</Fragment>
      ))}
    </Box>
  )
}
