import { expect, test } from 'vitest'
import type { GridRow } from '../api/grid.ts'
import { type AutoCommitStatus, commitText, conflictsOf, failedEntryMarks, failureOf } from './autoCommit.ts'
import type { QueueEntry } from '../api/queue.ts'

const status: AutoCommitStatus = {
  afterSeconds: 10,
  maxEntries: 20,
  pending: 2,
  committing: false,
  nextCommitAt: '2026-10-28T12:00:10Z',
  commitCount: 0,
  phase: null,
}

test('the commit text counts down to the next commit', () => {
  const now = Date.parse('2026-10-28T12:00:03.500Z')
  expect(commitText(status, now)).toBe('Saved automatically in 7 s.')
  expect(commitText(status, Date.parse('2026-10-28T12:00:11Z'))).toBe('Saved in a moment.')
  expect(commitText({ ...status, nextCommitAt: null }, now)).toBe('Saved with the next commit.')
  expect(commitText({ ...status, nextCommitAt: null, committing: true }, now)).toBe('Saved after the commit that runs now.')
})

function failed(id: number, result: QueueEntry['result'], itemId: string, column: string): QueueEntry {
  return {
    id,
    createdAt: '2026-10-28T12:00:00Z',
    source: 'ui',
    summary: `Entry ${id}`,
    status: 'failed',
    decidedAt: '2026-10-28T12:00:10Z',
    operation: { kind: 'setLeaf' },
    diff: [{ itemId, itemName: itemId === 'a' ? 'Code forge' : 'Visa', column, before: 'x', after: 'y' }],
    diffError: null,
    result,
  }
}

test('a failed commit names the items and reasons, marks the conflicts and tints the other cells', () => {
  const before: GridRow[] = [
    { id: 'a', revisionDate: '1', cells: { name: 'Code forge', folder: 'Work' } },
    { id: 'b', revisionDate: '1', cells: { name: 'Visa' } },
  ]
  const after: GridRow[] = [
    { id: 'a', revisionDate: '2', cells: { name: 'Code forge', folder: 'Home' } },
    { id: 'b', revisionDate: '1', cells: { name: 'Visa' } },
  ]
  const parts = failedEntryMarks(
    [
      failed(1, { error: 'conflicts', detail: 'Code forge changed elsewhere', conflicts: [{ itemId: 'a' }] }, 'a', 'login.username'),
      failed(2, { error: 'invalid', detail: 'Only card items have card.brand' }, 'b', 'card.brand'),
    ],
    before,
    after,
  )
  const failure = failureOf(parts)!
  const conflicts = conflictsOf(parts)

  expect(failure.message).toBe('Could not save 2 changes. Items that changed elsewhere are marked in the reloaded grid.')
  expect(failure.lines).toEqual(['Code forge: Code forge changed elsewhere', 'Visa: Only card items have card.brand'])
  expect([...conflicts.keys()]).toEqual(['a'])
  expect([...conflicts.get('a')!]).toEqual(['folder'])
  expect([...failure.errorCells.keys()]).toEqual(['b'])
  expect([...failure.errorCells.get('b')!]).toEqual(['card.brand'])
})

test('the marks of the failed entries left stay when one is taken away', () => {
  const before: GridRow[] = [
    { id: 'a', revisionDate: '1', cells: { name: 'Code forge', folder: 'Work' } },
    { id: 'b', revisionDate: '1', cells: { name: 'Visa', notes: 'old' } },
  ]
  const after: GridRow[] = [
    { id: 'a', revisionDate: '2', cells: { name: 'Code forge', folder: 'Home' } },
    { id: 'b', revisionDate: '2', cells: { name: 'Visa', notes: 'new' } },
  ]
  const parts = failedEntryMarks(
    [
      failed(1, { error: 'conflicts', detail: 'changed', conflicts: [{ itemId: 'a' }] }, 'a', 'login.username'),
      failed(2, { error: 'conflicts', detail: 'changed', conflicts: [{ itemId: 'b' }] }, 'b', 'notes'),
      failed(3, { error: 'invalid', detail: 'no' }, 'b', 'card.brand'),
    ],
    before,
    after,
  )
  expect([...conflictsOf(parts).keys()]).toEqual(['a', 'b'])

  const left = parts.filter((part) => part.id !== 1)
  expect([...conflictsOf(left).keys()]).toEqual(['b'])
  expect(failureOf(left)?.message).toBe('Could not save 2 changes. Items that changed elsewhere are marked in the reloaded grid.')

  const last = parts.filter((part) => part.id === 3)
  expect(conflictsOf(last).size).toBe(0)
  expect(failureOf(last)).toEqual({ message: 'Could not save a change.', lines: ['Visa: no'], errorCells: new Map([['b', new Set(['card.brand'])]]) })
  expect(failureOf([])).toBeNull()
})
