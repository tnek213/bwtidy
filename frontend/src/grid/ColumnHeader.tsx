import { Box, Button, Stack, Tooltip, Typography } from '@mui/material'
import type { CustomHeaderProps } from 'ag-grid-react'
import { type RefObject, useContext, useEffect, useRef, useState } from 'react'
import { useColorMode } from '../theme/colorMode.tsx'
import type { GridRow } from '../api/grid.ts'
import { HeaderContext, type NearDuplicate } from './normalize.ts'

// AG Grid sorts on a native click on the header and starts a column move on
// mousedown. Both are stopped right at the button, which also means React
// never sees the click, so the handler runs from the native listener.
function useOwnClick(ref: RefObject<HTMLElement | null>, onClick: (element: HTMLElement) => void) {
  const handler = useRef(onClick)
  useEffect(() => {
    handler.current = onClick
  })
  useEffect(() => {
    const element = ref.current
    if (!element) {
      return
    }
    const click = (event: Event) => {
      event.stopPropagation()
      handler.current(element)
    }
    const stop = (event: Event) => event.stopPropagation()
    element.addEventListener('click', click)
    element.addEventListener('mousedown', stop)
    return () => {
      element.removeEventListener('click', click)
      element.removeEventListener('mousedown', stop)
    }
  }, [ref])
}

const iconButton = {
  border: 0,
  background: 'none',
  padding: '0 2px',
  cursor: 'pointer',
  color: 'inherit',
  font: 'inherit',
  lineHeight: 1,
  flexShrink: 0,
}

function NearDuplicateMarker({ columnId, nearDuplicate }: { columnId: string; nearDuplicate: NearDuplicate }) {
  const { mode } = useColorMode()
  const { mergeColumns } = useContext(HeaderContext)
  const [open, setOpen] = useState(false)
  const marker = useRef<HTMLButtonElement>(null)
  useOwnClick(marker, () => setOpen((was) => !was))
  const others = nearDuplicate.group.columns.filter((column) => column !== columnId)
  const text = `Near duplicate of ${others.join(', ')}`
  return (
    <Tooltip
      // Above, so it stays clear of the grid's own header tooltip with the column name.
      placement="top"
      open={open}
      onOpen={() => setOpen(true)}
      onClose={() => setOpen(false)}
      title={
        <Stack spacing={0.5} sx={{ alignItems: 'flex-start' }}>
          <Typography variant="body2">{text}</Typography>
          <Button
            size="small"
            variant="contained"
            color="inherit"
            sx={{ color: 'text.primary' }}
            onClick={() => {
              setOpen(false)
              mergeColumns(nearDuplicate.group.columns)
            }}
          >
            Merge these
          </Button>
        </Stack>
      }
    >
      <Box
        component="button"
        type="button"
        ref={marker}
        aria-label={text}
        data-testid="near-duplicate-marker"
        sx={{ ...iconButton, display: 'inline-flex', alignItems: 'center' }}
      >
        <Box
          component="span"
          sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: mode === 'dark' ? nearDuplicate.darkColor : nearDuplicate.color, display: 'inline-block' }}
        />
      </Box>
    </Tooltip>
  )
}

// The inner part of a field column header: the near-duplicate marker, the
// name and the menu button. AG Grid keeps sorting and resizing around it.
export function FieldHeader(props: CustomHeaderProps<GridRow>) {
  const { nearDuplicates, openMenu } = useContext(HeaderContext)
  const columnId = props.column.getColId()
  const nearDuplicate = nearDuplicates.get(columnId)
  const menuButton = useRef<HTMLButtonElement>(null)
  useOwnClick(menuButton, (element) => openMenu(columnId, element))
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0 }}>
      {nearDuplicate && <NearDuplicateMarker columnId={columnId} nearDuplicate={nearDuplicate} />}
      <Box component="span" sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {props.displayName}
      </Box>
      <Box component="button" type="button" ref={menuButton} aria-label={`Actions for ${columnId}`} sx={iconButton}>
        ⋮
      </Box>
    </Box>
  )
}
