import type { CellValue, GridRow } from '../api/grid.ts'
import { TYPE_LABELS } from '../shared/templateRules.ts'

// Empty string means "no filter". NONE matches items without a value.
export const NONE = '\u0000none'

export interface Filters {
  text: string
  type: string
  folder: string
  organization: string
  collection: string
}

export const noFilters: Filters = { text: '', type: '', folder: '', organization: '', collection: '' }

export interface FilterOptions {
  type: string[]
  folder: string[]
  organization: string[]
  collection: string[]
}

function sorted(values: Iterable<string>): string[] {
  return [...values].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
}

export function filterOptions(rows: GridRow[]): FilterOptions {
  const sets = { type: new Set<string>(), folder: new Set<string>(), organization: new Set<string>(), collection: new Set<string>() }
  for (const { cells } of rows) {
    for (const key of ['type', 'folder', 'organization'] as const) {
      const value = cells[key]
      if (typeof value === 'string') {
        sets[key].add(value)
      }
    }
    const collections = cells.collections
    if (Array.isArray(collections)) {
      for (const name of collections) {
        sets.collection.add(name)
      }
    }
  }
  const typeOrder = Object.keys(TYPE_LABELS)
  return {
    type: [...sets.type].sort((a, b) => typeOrder.indexOf(a) - typeOrder.indexOf(b)),
    folder: sorted(sets.folder),
    organization: sorted(sets.organization),
    collection: sorted(sets.collection),
  }
}

function matchesSingle(value: CellValue | undefined, wanted: string): boolean {
  if (wanted === '') {
    return true
  }
  if (wanted === NONE) {
    return value === undefined
  }
  return value === wanted
}

function matchesCollection(value: CellValue | undefined, wanted: string): boolean {
  if (wanted === '') {
    return true
  }
  const names = Array.isArray(value) ? value : []
  return wanted === NONE ? names.length === 0 : names.includes(wanted)
}

// Secret cells are masked objects and never searched.
function searchText(row: GridRow): string {
  const parts: string[] = []
  for (const value of Object.values(row.cells)) {
    if (typeof value === 'string') {
      parts.push(value)
    } else if (Array.isArray(value)) {
      parts.push(...value)
    }
  }
  return parts.join('\n').toLowerCase()
}

export function searchWords(text: string): string[] {
  return text.toLowerCase().split(/\s+/).filter(Boolean)
}

// True when the cell holds a string (or a list element) containing a search word.
// Secret cells are masked objects and never match.
export function cellMatchesSearch(value: CellValue | undefined, words: string[]): boolean {
  if (words.length === 0) {
    return false
  }
  const texts = typeof value === 'string' ? [value] : Array.isArray(value) ? value : []
  return texts.some((text) => {
    const lower = text.toLowerCase()
    return words.some((word) => lower.includes(word))
  })
}

export function filterRows(rows: GridRow[], filters: Filters): GridRow[] {
  const words = searchWords(filters.text)
  return rows.filter((row) => {
    const { cells } = row
    if (
      !matchesSingle(cells.type, filters.type) ||
      !matchesSingle(cells.folder, filters.folder) ||
      !matchesSingle(cells.organization, filters.organization) ||
      !matchesCollection(cells.collections, filters.collection)
    ) {
      return false
    }
    if (words.length === 0) {
      return true
    }
    const text = searchText(row)
    return words.every((word) => text.includes(word))
  })
}
