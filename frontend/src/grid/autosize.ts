import type { ColumnResizedEvent } from 'ag-grid-community'

// An autosized column never gets wider than this many characters of content.
export const MAX_AUTOSIZE_CHARS = 50
// Used when the real cell padding cannot be read (AG Grid Quartz is about 18px a side).
const FALLBACK_CELL_PADDING = 36
const FALLBACK_CHAR_WIDTH = 8

// The width cap for an autosized column: the characters plus the cell padding.
export function autosizeCap(charWidth: number, padding: number, maxChars: number = MAX_AUTOSIZE_CHARS): number {
  return Math.ceil(charWidth * maxChars + padding)
}

// The width an autosized column ends up with: its fitted width, but at most the cap.
export function cappedWidth(
  width: number,
  charWidth: number,
  padding: number = FALLBACK_CELL_PADDING,
  maxChars: number = MAX_AUTOSIZE_CHARS,
): number {
  return Math.min(width, autosizeCap(charWidth, padding, maxChars))
}

let canvas: OffscreenCanvas | undefined

// The width of one character in the font and padding of a rendered grid cell.
function measureCell(cell: Element | null): { charWidth: number; padding: number } {
  if (!cell) return { charWidth: FALLBACK_CHAR_WIDTH, padding: FALLBACK_CELL_PADDING }
  const style = getComputedStyle(cell)
  const padding =
    (Number.parseFloat(style.paddingLeft) || 0) +
    (Number.parseFloat(style.paddingRight) || 0) +
    (Number.parseFloat(style.borderLeftWidth) || 0) +
    (Number.parseFloat(style.borderRightWidth) || 0)
  // jsdom has no OffscreenCanvas, the fallback width is used there.
  let charWidth = 0
  try {
    if (typeof OffscreenCanvas === 'undefined') throw new Error('no canvas')
    canvas ??= new OffscreenCanvas(1, 1)
    const context = canvas.getContext('2d')
    if (context) {
      context.font = style.font || `${style.fontSize} ${style.fontFamily}`
      charWidth = context.measureText('0').width
    }
  } catch {
    charWidth = 0
  }
  return {
    charWidth: charWidth > 0 ? charWidth : FALLBACK_CHAR_WIDTH,
    padding: padding > 0 ? padding : FALLBACK_CELL_PADDING,
  }
}

// Caps the columns that a double click on a column edge just fitted. A manual drag has another source and may go wider.
export function capAutosizedColumns(event: ColumnResizedEvent): void {
  if (event.source !== 'autosizeColumns' || !event.finished) return
  const columns = event.columns ?? (event.column ? [event.column] : [])
  if (columns.length === 0) return
  const { charWidth, padding } = measureCell(document.querySelector('.ag-cell'))
  const changes = columns
    .map((column) => ({
      key: column.getColId(),
      width: column.getActualWidth(),
      newWidth: cappedWidth(column.getActualWidth(), charWidth, padding),
    }))
    .filter(({ width, newWidth }) => newWidth < width)
    .map(({ key, newWidth }) => ({ key, newWidth }))
  if (changes.length > 0) event.api.setColumnWidths(changes, true, 'api')
}
