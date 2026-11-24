import { expect, test } from 'vitest'
import { conflictMarks } from './conflicts.ts'
import type { GridRow } from '../api/grid.ts'

function row(id: string, cells: GridRow['cells'], revisionDate = '2026-10-01T10:00:00Z'): GridRow {
  return { id, revisionDate, cells: { ...cells, revisionDate } }
}

test('marks the cells whose shown value changed', () => {
  const before = [row('item-1', { name: 'Code forge', 'login.username': 'user', folder: 'Work', 'login.uris': ['a', 'b'] })]
  const after = [
    row('item-1', { name: 'Code forge', 'login.username': 'user.name', 'login.uris': ['a', 'c'] }, '2026-10-05T10:00:00Z'),
  ]

  const marks = conflictMarks(before, after)

  expect([...marks.keys()]).toEqual(['item-1'])
  expect([...marks.get('item-1')!].sort()).toEqual(['folder', 'login.uris', 'login.username'])
})

test('a row whose only change is masked is marked without cells', () => {
  const before = [row('item-1', { name: 'Code forge', 'login.password': { masked: true } })]
  const after = [row('item-1', { name: 'Code forge', 'login.password': { masked: true } }, '2026-10-05T10:00:00Z')]

  expect(conflictMarks(before, after)).toEqual(new Map([['item-1', new Set()]]))
})

test('a masked cell that appears or goes is never marked', () => {
  const before = [row('item-1', { name: 'Code forge', 'fields.PIN': { masked: true } })]
  const after = [row('item-1', { name: 'Code forge', 'login.totp': { masked: true } })]

  expect(conflictMarks(before, after).get('item-1')).toEqual(new Set())
})

test('values that look the same are not marked', () => {
  const before = [row('item-1', { name: 'Code forge', favorite: false, 'login.uris': ['a'] })]
  const after = [row('item-1', { name: 'Code forge', 'login.uris': ['a'] })]

  expect(conflictMarks(before, after).get('item-1')).toEqual(new Set())
})

test('a vanished item and rows outside the conflict get no marks', () => {
  const before = [row('item-1', { name: 'Code forge' })]
  const after = [row('item-2', { name: 'Laptop' })]

  expect(conflictMarks(before, after).size).toBe(0)
})
