import { expect, test } from 'vitest'
import { type ChangeLogFilters, hasFilters, noChangeLogFilters } from './filters.ts'

test('no filters is not a filter', () => {
  expect(hasFilters(noChangeLogFilters)).toBe(false)
})

test.each<[string, Partial<ChangeLogFilters>]>([
  ['from', { from: '2026-11-01' }],
  ['to', { to: '2026-11-02' }],
  ['sources', { sources: ['ui'] }],
  ['kinds', { kinds: ['setValue'] }],
  ['columns', { columns: ['name'] }],
  ['items', { items: 'Bank' }],
  ['blank items', { items: ' ' }],
  ['result', { result: 'failed' }],
])('%s alone is a filter', (_name, part) => {
  expect(hasFilters({ ...noChangeLogFilters, ...part })).toBe(true)
})
