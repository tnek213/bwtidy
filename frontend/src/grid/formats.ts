import type { Theme } from '@mui/material'
import { apiFetch } from '../api/api.ts'
import type { FormatResult } from '../api/datatypes.ts'
import type { FormatScore, Template, TemplateMatches } from '../api/templates.ts'
import { useDebouncedRequest } from '../shared/useDebouncedRequest.ts'
import { columnHeaderLabel } from './labels.ts'
import { coordinateOfColumn, formatLine, formatPercent, isSecretColumnOf, shownScore } from './matches.ts'

type FormatLevel = 'full' | 'partial' | 'none'

export const FORMAT_CLASSES: Record<FormatLevel, string> = {
  full: 'bw-format-full',
  partial: 'bw-format-partial',
  none: 'bw-format-none',
}

// What a cell shows for the datatype of its column in the template of its row.
export interface CellFormat {
  level: FormatLevel
  hover: string
  // Null when the templates list does not know it yet. The check needs it, the mark does not.
  datatypeId: string | null
}

export type CellFormats = ReadonlyMap<string, ReadonlyMap<string, CellFormat>>

function levelOf(format: FormatScore): FormatLevel {
  if (format.score === 1) {
    return 'full'
  }
  return format.score > 0 ? 'partial' : 'none'
}

function datatypeIdOf(template: Template | undefined, column: string): string | null {
  return coordinateOfColumn(template, column)?.datatype ?? null
}

export function cellFormats(
  matches: TemplateMatches | undefined,
  templates: readonly Template[],
  isSecretColumn: (column: string) => boolean,
): CellFormats {
  const byItem = new Map<string, Map<string, CellFormat>>()
  for (const [itemId, match] of Object.entries(matches ?? {})) {
    const score = shownScore(match)
    if (!score?.formats?.length) {
      continue
    }
    const template = templates.find((candidate) => candidate.name === score.template)
    byItem.set(
      itemId,
      new Map(
        score.formats.map((format) => [
          format.column,
          {
            level: levelOf(format),
            hover: score.missing.includes(format.column)
              ? `${columnHeaderLabel(format.column)}: no value`
              : formatLine(format, isSecretColumnOf(template, format.column, isSecretColumn)),
            datatypeId: datatypeIdOf(template, format.column),
          },
        ]),
      ),
    )
  }
  return byItem
}

// A small round mark in the cell's corner: filled, half filled or empty. Inside the to-fill
// outline, beside the eye of a secret cell, and it takes no clicks.
const mark = (color: string, background: string) => ({
  content: '""',
  position: 'absolute',
  top: 4,
  right: 4,
  width: 8,
  height: 8,
  boxSizing: 'border-box',
  borderRadius: '50%',
  border: `1.5px solid ${color}`,
  background,
  pointerEvents: 'none',
})

export const formatMarkStyles = ({ palette }: Theme) => ({
  [`& .${FORMAT_CLASSES.full}::after`]: mark(palette.success.main, palette.success.main),
  [`& .${FORMAT_CLASSES.partial}::after`]: mark(
    palette.warning.main,
    `linear-gradient(90deg, ${palette.warning.main} 50%, transparent 50%)`,
  ),
  [`& .${FORMAT_CLASSES.none}::after`]: mark(palette.error.main, 'transparent'),
})

export function liveFormatText({ score, firstEdit, expected, tooLong }: Omit<FormatResult, 'distance'>): string {
  if (tooLong) {
    return 'too long to check'
  }
  if (score === 1) {
    return 'matches format'
  }
  const percent = formatPercent(score)
  return firstEdit !== null && expected ? `${percent}, expected ${expected} at position ${firstEdit + 1}` : percent
}

const CHECK_DELAY_MS = 250

function checkFormat(datatypeId: string, value: string): Promise<FormatResult> {
  return apiFetch<FormatResult>(`/api/datatypes/${encodeURIComponent(datatypeId)}/check`, { method: 'POST', body: { value } })
}

// The result of the value against the datatype, once the typing pauses. While a changed value
// waits, the result before it stays. A refused check (a datatype that went away or broke) shows
// nothing.
export function useFormatCheck(datatypeId: string | null, value: string): FormatResult | null {
  const key = datatypeId !== null && value !== '' ? JSON.stringify([datatypeId, value]) : null
  const check = useDebouncedRequest(key, () => checkFormat(datatypeId!, value), CHECK_DELAY_MS)
  return key !== null && 'value' in check ? check.value : null
}

type FormatVerdictState = 'none' | 'pending' | 'matches' | 'off'

interface FormatVerdict {
  state: FormatVerdictState
  // The first value that is off its format, for the text under the input.
  off: FormatResult | null
}

const NO_VERDICT: FormatVerdict = { state: 'none', off: null }
const PENDING: FormatVerdict = { state: 'pending', off: null }

// Whether all of the values match the datatype, for a form that must not accept a value off it.
// Debounced like useFormatCheck, but the verdict is for the exact values: changed values are
// pending in the render that changed them, before the debounce. A refused check (a datatype that
// went away or broke) is none and never locks.
export function useFormatVerdict(datatypeId: string | null, values: string[]): FormatVerdict {
  const verdict = useDebouncedRequest(
    datatypeId !== null && values.length > 0 ? JSON.stringify([datatypeId, values]) : null,
    async (): Promise<FormatVerdict> => {
      const results = await Promise.all(values.map((value) => checkFormat(datatypeId!, value)))
      const off = results.find((result) => result.score !== 1 || result.tooLong) ?? null
      return { state: off ? 'off' : 'matches', off }
    },
    CHECK_DELAY_MS,
  )
  if (verdict.state === 'answered') {
    return verdict.value
  }
  return verdict.state === 'pending' ? PENDING : NO_VERDICT
}
