import type { FormatResult } from '../api/datatypes.ts'

// The most examples, and the most counter-examples, the backend takes (MAX_EXAMPLES).
export const MAX_EXAMPLES = 50

// A $ after an odd number of backslashes is a literal, like the backend's parser reads it.
function endsWithAnchor(pattern: string): boolean {
  if (!pattern.endsWith('$')) {
    return false
  }
  const backslashes = pattern.slice(0, -1).match(/\\*$/)?.[0].length ?? 0
  return backslashes % 2 === 0
}

interface TypedPattern {
  pattern: string
  wholeValue: boolean
  // How many characters went from the start, for keeping the caret where it was.
  removedAtStart: number
}

// The typing rules of the pattern field (docs/spec.md, Datatypes): a ^ first and a $ last tick
// Whole value and go, and while it is ticked either one alone goes too, as it says nothing more.
export function typedPattern(text: string, wholeValue: boolean): TypedPattern {
  const start = text.startsWith('^')
  const end = text.length > (start ? 1 : 0) && endsWithAnchor(text)
  if (!(start && end) && !wholeValue) {
    return { pattern: text, wholeValue, removedAtStart: 0 }
  }
  const pattern = text.slice(start ? 1 : 0, end ? -1 : undefined)
  return { pattern, wholeValue: true, removedAtStart: start ? 1 : 0 }
}

// One value per line, empty lines left out. Spaces stay: they can be what a counter-example is about.
export function valueLines(text: string): string[] {
  return text.split(/\r?\n/).filter((line) => line !== '')
}

export function isFullMatch(result: FormatResult): boolean {
  return result.distance === 0
}

// firstEdit counts from 0, the user reads positions from 1 like the pattern's columns.
export function whereItDiffers(result: FormatResult): string {
  const { distance, firstEdit, expected } = result
  if (distance === null || distance === 0 || firstEdit === null) {
    return ''
  }
  const position = firstEdit + 1
  const where = distance === 1 ? `1 change at position ${position}` : `${distance} changes, the first at position ${position}`
  return expected === null ? where : `${where}, expected ${expected}`
}

// "matches format 75%, distance 1: 1 change at position 3, expected a digit"
export function resultText(result: FormatResult): string {
  if (result.tooLong) {
    return 'too long to check'
  }
  const percent = `matches format ${Math.round(result.score * 100)}%, distance ${result.distance}`
  const where = whereItDiffers(result)
  return where === '' ? percent : `${percent}: ${where}`
}
