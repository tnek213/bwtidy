import { alpha, Box, TextField, Tooltip } from '@mui/material'
import { type UIEvent, useState } from 'react'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'

// right: the line keeps its box's rule, wrong: it breaks it, pending: no answer for it yet.
type MarkKind = 'right' | 'wrong' | 'pending'

export interface LineMark {
  kind: MarkKind
  symbol: string
  label: string
  details: string
}

// Fixed in px in the gutter and the textarea alike, so the marks stay on their lines whatever
// font a glyph falls back to.
const LINE_PX = 24
const PAD_PX = 16
const MIN_ROWS = 3
const MAX_ROWS = 12

const MARK_COLOR: Record<MarkKind, string> = {
  right: 'text.primary',
  wrong: 'error.main',
  pending: 'text.disabled',
}

const lineBox = { height: LINE_PX, lineHeight: `${LINE_PX}px` } as const

interface Props {
  label: string
  value: string
  onChange: (value: string) => void
  error?: boolean
  helperText: string
  // The mark of one line, asked only for lines that are not empty (an empty one holds no value).
  markOf: (line: string) => LineMark
}

// A multiline text field with one mark per value line in a gutter on its left. Lines never wrap
// (one value is one visual line) and the gutter follows the textarea's scrolling.
export default function MarkedLines({ label, value, onChange, error, helperText, markOf }: Props) {
  const [scrollTop, setScrollTop] = useState(0)
  const lines = value.split('\n')
  const marks = lines.map((line) => (line === '' ? null : markOf(line)))
  const shift = { transform: `translateY(${-scrollTop}px)` }

  const gutter = (
    <Box
      role="group"
      aria-label={`${label} marks`}
      sx={{ alignSelf: 'stretch', flexShrink: 0, width: 20, mr: 1, overflow: 'hidden', position: 'relative', zIndex: 1 }}
    >
      <Box sx={shift}>
        {marks.map((mark, line) =>
          mark === null ? (
            <Box key={line} sx={lineBox} />
          ) : (
            <Tooltip key={line} title={mark.details} describeChild placement="top-start">
              <Box
                role="img"
                aria-label={mark.label}
                data-mark={mark.kind}
                sx={{
                  ...lineBox,
                  fontFamily: 'monospace',
                  fontSize: '1rem',
                  textAlign: 'center',
                  overflow: 'hidden',
                  color: MARK_COLOR[mark.kind],
                  fontWeight: mark.kind === 'wrong' ? 'bold' : undefined,
                }}
              >
                {mark.symbol}
              </Box>
            </Tooltip>
          ),
        )}
      </Box>
    </Box>
  )

  // Behind the textarea: a faint red row under each line that breaks its box's rule.
  const backdrop = (
    <Box
      aria-hidden
      sx={{ position: 'absolute', top: PAD_PX, bottom: PAD_PX, left: 0, right: 0, overflow: 'hidden', pointerEvents: 'none' }}
    >
      <Box sx={shift}>
        {marks.map((mark, line) => (
          <Box
            key={line}
            data-testid={mark?.kind === 'wrong' ? 'wrong-line' : undefined}
            sx={{ ...lineBox, bgcolor: mark?.kind === 'wrong' ? (theme) => alpha(theme.palette.error.main, 0.12) : undefined }}
          />
        ))}
      </Box>
    </Box>
  )

  return (
    <TextField
      fullWidth
      multiline
      rows={Math.min(Math.max(lines.length, MIN_ROWS), MAX_ROWS)}
      label={label}
      value={value}
      error={error}
      helperText={helperText}
      onChange={(event) => onChange(event.target.value)}
      slotProps={{
        input: {
          startAdornment: (
            <>
              {backdrop}
              {gutter}
            </>
          ),
          sx: { alignItems: 'stretch', py: `${PAD_PX}px`, pl: 0.5 },
        },
        htmlInput: {
          ...NO_PASSWORD_MANAGER,
          wrap: 'off',
          spellCheck: false,
          onScroll: (event: UIEvent<HTMLTextAreaElement>) => setScrollTop(event.currentTarget.scrollTop),
          sx: {
            fontFamily: 'monospace',
            fontSize: '1rem',
            lineHeight: `${LINE_PX}px`,
            whiteSpace: 'pre',
            overflowWrap: 'normal',
            overflowX: 'auto',
            position: 'relative',
            zIndex: 1,
          },
        },
      }}
    />
  )
}
