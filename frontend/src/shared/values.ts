import type { CellValue, Masked } from '../api/grid.ts'

export function isMasked(value: CellValue | null | undefined): value is Masked {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && value.masked === true
}

export const MASK = '•'.repeat(8)

export function formatValue(value: CellValue | undefined): string {
  if (value === undefined) {
    return ''
  }
  if (isMasked(value)) {
    return MASK
  }
  if (Array.isArray(value)) {
    return value.join(', ')
  }
  if (typeof value === 'boolean') {
    return value ? '✓' : ''
  }
  return String(value)
}
