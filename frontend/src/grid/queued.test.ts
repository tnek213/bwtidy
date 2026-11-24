import { expect, test } from 'vitest'
import type { DiffRow, QueueEntry } from '../api/queue.ts'
import { editBlocker, unavailableReason } from './editing.ts'
import type { GridColumn, GridRow } from '../api/grid.ts'
import {
  buildOverlay,
  cellTooltip,
  isQueuedForTrash,
  QUEUED_BLOCKER,
  queuedTooltip,
  shownRows,
  shownValue,
  withExtraColumns,
} from './queued.ts'
import { gridColumn } from './testSupport.tsx'

const columns: GridColumn[] = [
  gridColumn('name'),
  gridColumn('folder'),
  gridColumn('login.username'),
  gridColumn('login.password', { secret: true }),
  gridColumn('fields.Alpha'),
  gridColumn('fields.PIN', { secret: true }),
  gridColumn('fields.Zulu'),
]

const row: GridRow = {
  id: 'item-1',
  revisionDate: '2026-10-01T10:00:00Z',
  cells: { name: 'Code forge', folder: 'Work', 'login.username': 'user', 'fields.PIN': { masked: true } },
}

function diffRow(column: string, before: DiffRow['before'], after: DiffRow['after'], itemId = 'item-1'): DiffRow {
  return { itemId, itemName: 'Code forge', column, before, after }
}

function entry(id: number, summary: string, diff: DiffRow[], diffError: string | null = null): QueueEntry {
  return {
    id,
    createdAt: '2026-10-28T10:00:00Z',
    source: 'mcp',
    summary,
    status: 'pending',
    decidedAt: null,
    operation: {},
    diff,
    diffError,
    result: null,
  }
}

function shown(entries: QueueEntry[]) {
  const [decorated] = shownRows([row], buildOverlay(entries, columns))
  return decorated
}

test('a restore row is no cell and makes no extra column', () => {
  const overlay = buildOverlay(
    [
      entry(7, 'Restore 1 item from 20261029T100000Z.zip.age', [
        diffRow('restore', null, 'put back as before'),
        diffRow('login.username', 'user', 'user.l'),
      ]),
    ],
    columns,
  )
  expect(overlay.extraColumns).toEqual([])
  const [decorated] = shownRows([row], overlay)
  expect(decorated.queued?.cells.has('restore')).toBe(false)
  expect(shownValue(decorated, 'login.username')).toBe('user.l')
})

test('a new item is no row and makes no extra column', () => {
  const create = entry(8, 'Create login "Forge"', [
    { itemId: null, itemName: 'Forge', column: 'name', before: null, after: 'Forge' },
    { itemId: null, itemName: 'Forge', column: 'fields.Rack', before: null, after: 'B2' },
  ])
  const overlay = buildOverlay([{ ...create, operation: { kind: 'createItem' } }], columns)
  expect(overlay.rows.size).toBe(0)
  expect(overlay.extraColumns).toEqual([])
})

test('a queued value replaces the vault value and keeps the vault value as before', () => {
  const decorated = shown([entry(3, 'Move "Code forge" to folder Private', [diffRow('folder', 'Work', 'Private')])])

  expect(shownValue(decorated, 'folder')).toBe('Private')
  expect(decorated.cells.folder).toBe('Work')
  expect(shownValue(decorated, 'name')).toBe('Code forge')
  expect(queuedTooltip(decorated, 'folder')).toBe('Queued in #3: Move "Code forge" to folder Private. Was: Work')
  expect(queuedTooltip(decorated, 'name')).toBeNull()
})

test('a queued removal shows an empty cell and a missing before is empty in the tooltip', () => {
  const decorated = shown([
    entry(1, 'Remove the folder.', [diffRow('folder', 'Work', null)]),
    entry(2, 'Set a username', [diffRow('login.password', null, { masked: true })]),
  ])

  expect(shownValue(decorated, 'folder')).toBeUndefined()
  expect(queuedTooltip(decorated, 'folder')).toBe('Queued in #1: Remove the folder. Was: Work')
  expect(shownValue(decorated, 'login.password')).toEqual({ masked: true })
  expect(queuedTooltip(decorated, 'login.password')).toBe('Queued in #2: Set a username. Was: ')
})

test('the newest entry wins and the tooltip lists every entry with the conflict note', () => {
  const entries = [
    entry(9, 'Move to C', [diffRow('folder', 'Work', 'C')]),
    entry(4, 'Move to A', [diffRow('folder', 'Work', 'A')]),
    entry(7, 'Move to B', [diffRow('folder', 'Work', 'B')]),
  ]
  const decorated = shown(entries)

  expect(shownValue(decorated, 'folder')).toBe('C')
  const cell = decorated.queued!.cells.get('folder')!
  expect(cell.changes.map((change) => change.entryId)).toEqual([4, 7, 9])
  expect(cellTooltip(cell)).toBe(
    [
      'Queued in #4: Move to A. Was: Work',
      'Queued in #7: Move to B. Was: Work',
      'Queued in #9: Move to C. Was: Work',
      'Approved in order, #7, #9 fail with a conflict.',
    ].join('\n'),
  )
})

test('a trashed item is marked and its tooltip names the entry', () => {
  const decorated = shown([entry(5, 'Move "Code forge" to the trash', [diffRow('trash', false, true)])])

  expect(isQueuedForTrash(decorated)).toBe(true)
  expect(decorated.queued!.cells.has('trash')).toBe(false)
  expect(queuedTooltip(decorated, 'name')).toBe('Queued for the trash in #5: Move "Code forge" to the trash.')
  expect(isQueuedForTrash(row)).toBe(false)
})

test('a column only a queued change has becomes an extra column in its group and order', () => {
  const overlay = buildOverlay(
    [
      entry(1, 'Rename PIN to Code', [diffRow('fields.PIN', { masked: true }, null), diffRow('fields.Code', null, { masked: true })]),
      entry(2, 'Apply template', [diffRow('fields.notes about', null, 'x'), diffRow('login.totp', null, { masked: true })]),
    ],
    columns,
  )

  expect(overlay.extraColumns).toEqual([
    { id: 'fields.Code', label: 'fields.Code', group: 'fields', secret: true, multi: false, readOnly: false },
    { id: 'fields.notes about', label: 'fields.notes about', group: 'fields', secret: false, multi: false, readOnly: false },
    { id: 'login.totp', label: 'login.totp', group: 'login', secret: true, multi: false, readOnly: false },
  ])
  expect(withExtraColumns(columns, overlay.extraColumns).map((c) => c.id)).toEqual([
    'name',
    'folder',
    'login.username',
    'login.password',
    'fields.Alpha',
    'fields.Code',
    'fields.notes about',
    'fields.PIN',
    'fields.Zulu',
    'login.totp',
  ])
})

test('an entry with a diff error and rows of other items change nothing here', () => {
  const overlay = buildOverlay(
    [
      entry(1, 'Stale', [], 'item item-1 is not in the vault'),
      entry(2, 'Other item', [diffRow('folder', 'Home', 'Away', 'item-2')]),
    ],
    columns,
  )
  const [decorated] = shownRows([row], overlay)

  expect(decorated.queued).toBeUndefined()
  expect(shownValue(decorated, 'folder')).toBe('Work')
  expect(overlay.extraColumns).toEqual([])
})

test('a masked before shows bullets and a queued cell is read-only', () => {
  const decorated = shown([entry(6, 'Merge PIN into Alpha', [diffRow('fields.PIN', { masked: true }, null)])])

  expect(queuedTooltip(decorated, 'fields.PIN')).toBe('Queued in #6: Merge PIN into Alpha. Was: ••••••••')
  expect(editBlocker(columns[5], decorated)).toBe(QUEUED_BLOCKER)
  expect(editBlocker(columns[0], decorated)).toBeNull()
  // A proposal's cell has the queued tint, not the grey of a cell without a value.
  expect(unavailableReason(columns[5], decorated)).toBeNull()
})

function uiEntry(id: number, summary: string, diff: DiffRow[], status: QueueEntry['status'] = 'pending'): QueueEntry {
  return { ...entry(id, summary, diff), source: 'ui', status }
}

test('a UI edit stays editable and its tooltip says when it is saved, a proposal stays read-only', () => {
  const decorated = shown([
    uiEntry(7, 'Set login.username of "Code forge"', [diffRow('login.username', 'user', 'user.l')]),
    entry(8, 'Rename "Code forge" to "Codeharbor"', [diffRow('name', 'Code forge', 'Codeharbor')]),
  ])

  expect(shownValue(decorated, 'login.username')).toBe('user.l')
  expect(editBlocker(columns[2], { ...decorated, cells: { ...decorated.cells, type: 'login' } })).toBeNull()
  expect(editBlocker(columns[0], decorated)).toBe(QUEUED_BLOCKER)
  expect(queuedTooltip(decorated, 'login.username', 'Saved automatically in 4 s.')).toBe(
    'Unsaved in #7: Set login.username of "Code forge". Was: user\nSaved automatically in 4 s.',
  )
  expect(queuedTooltip(decorated, 'name', 'Saved automatically in 4 s.')).toBe(
    'Queued in #8: Rename "Code forge" to "Codeharbor". Was: Code forge',
  )
})

test('UI edits of one cell follow each other without a conflict note, and one being saved says so', () => {
  const decorated = shown([
    uiEntry(7, 'Set login.username', [diffRow('login.username', 'user', 'k1')], 'approved'),
    uiEntry(9, 'Set login.username', [diffRow('login.username', 'k1', 'k2')]),
  ])

  expect(shownValue(decorated, 'login.username')).toBe('k2')
  expect(queuedTooltip(decorated, 'login.username', 'Saved with the next commit.')).toBe(
    'Saving in #7: Set login.username. Was: user\nUnsaved in #9: Set login.username. Was: k1\nSaved with the next commit.',
  )
})
