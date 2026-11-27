import { expect, test } from 'vitest'
import { cellDescription, CONFLICT_CELL_TEXT, FAILED_CELL_TEXT, SEARCH_HIT_TEXT } from './cellDescription.ts'
import { type CellMarks, noCellMarks } from './cellMarks.ts'
import { gridColumn } from './testSupport.tsx'
import type { ShownRow } from './queued.ts'
import { TO_FILL_TOOLTIP } from './toFill.ts'

const username = gridColumn('login.username')
const password = gridColumn('login.password', { secret: true })
const brand = gridColumn('card.brand')

const login: ShownRow = {
  id: 'item-1',
  revisionDate: '2026-10-01T10:00:00Z',
  cells: { name: 'Code forge', type: 'login', 'login.username': 'user' },
}

function marks(extra: Partial<CellMarks>): CellMarks {
  return { ...noCellMarks, ...extra }
}

test('a plain cell without marks has no description', () => {
  expect(cellDescription(username, login, noCellMarks)).toBeUndefined()
})

test('a cell that cannot hold a value says why', () => {
  expect(cellDescription(brand, login, noCellMarks)).toBe('Only card items have card.brand.')
})

test('the marks that only show as colors are said in words, one sentence each', () => {
  const all = marks({
    isConflictCell: () => true,
    isFailedCell: () => true,
    isToFillCell: () => true,
    searchWords: () => ['user'],
  })
  expect(cellDescription(username, login, all)).toBe(
    [CONFLICT_CELL_TEXT, FAILED_CELL_TEXT, `${TO_FILL_TOOLTIP}.`, SEARCH_HIT_TEXT].join(' '),
  )
})

test('a format result is said, the value is not', () => {
  const format = marks({
    formatOf: () => ({ level: 'partial', hover: 'matches format 80%, expected a digit at position 1', datatypeId: 'd' }),
  })
  const text = cellDescription(username, login, format)
  expect(text).toBe('matches format 80%, expected a digit at position 1.')
  expect(text).not.toContain('user')
})

test('a secret cell on the to-fill list says so', () => {
  const toFill = marks({ isToFillCell: (_, column) => column === 'login.password' })
  expect(cellDescription(password, login, toFill)).toBe(`${TO_FILL_TOOLTIP}.`)
})
