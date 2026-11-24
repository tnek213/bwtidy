import { Box, TextField, Typography } from '@mui/material'
import type { CustomCellEditorProps } from 'ag-grid-react'
import { useEffect, useRef, useState } from 'react'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import { useColorMode } from '../theme/colorMode.tsx'
import { liveFormatText, useFormatCheck } from './formats.ts'

// A value off its format still saves, so this only ever warns.
export function FormatFeedback({ datatypeId, value }: { datatypeId: string | null; value: string }) {
  const result = useFormatCheck(datatypeId, value)
  const { mode } = useColorMode()
  const shade = mode === 'dark' ? 'light' : 'dark'
  if (result === null) {
    return null
  }
  return (
    <Typography component="span" variant="caption" role="status" color={result.score === 1 ? `success.${shade}` : `warning.${shade}`}>
      {liveFormatText(result)}
    </Typography>
  )
}

export interface FormatEditorParams {
  datatypeId: string
  multiline: boolean
}

// Like agLargeTextCellEditor's limit.
const LONG_TEXT_MAX = 100_000

// Starts like AG Grid's own text editors: a typed character replaces the value, Backspace and
// Delete empty it, and the value is selected unless F2 opened the editor.
function startText({ value, eventKey, cellStartedEdit }: Pick<CustomCellEditorProps, 'value' | 'eventKey' | 'cellStartedEdit'>): string {
  if (cellStartedEdit && (eventKey === 'Backspace' || eventKey === 'Delete')) {
    return ''
  }
  if (cellStartedEdit && eventKey?.length === 1) {
    return eventKey
  }
  return value === null || value === undefined ? '' : String(value)
}

export function FormatCellEditor(props: CustomCellEditorProps & FormatEditorParams) {
  const { datatypeId, multiline, onValueChange } = props
  const opening = useRef(props)
  const [start] = useState(() => startText(props))
  const [text, setText] = useState(start)
  const box = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement | HTMLTextAreaElement>(null)

  useEffect(() => {
    const { eventKey } = opening.current
    opening.current.onValueChange(start)
    const element = input.current
    if (element) {
      element.focus()
      if (eventKey?.length !== 1 && eventKey !== 'F2' && start !== '') {
        element.select()
      }
    }
    // Shift+Enter is a new line. The grid's own listener sees the key before React would.
    const keepNewLine = (event: KeyboardEvent) => {
      if (event.key === 'Enter' && event.shiftKey) {
        event.stopPropagation()
      }
    }
    const wrapper = box.current
    wrapper?.addEventListener('keydown', keepNewLine)
    return () => wrapper?.removeEventListener('keydown', keepNewLine)
  }, [start])

  return (
    <Box ref={box} sx={{ p: 1, minWidth: multiline ? 480 : 300, bgcolor: 'background.paper' }}>
      <TextField
        fullWidth
        size="small"
        multiline={multiline}
        minRows={multiline ? 8 : undefined}
        maxRows={multiline ? 16 : undefined}
        value={text}
        inputRef={input}
        onChange={(event) => {
          setText(event.target.value)
          onValueChange(event.target.value)
        }}
        slotProps={{
          htmlInput: {
            ...NO_PASSWORD_MANAGER,
            'aria-label': 'Input Editor',
            spellCheck: false,
            maxLength: multiline ? LONG_TEXT_MAX : undefined,
          },
        }}
      />
      <div style={{ marginTop: 4, minHeight: 20 }}>
        <FormatFeedback datatypeId={datatypeId} value={text} />
      </div>
    </Box>
  )
}
