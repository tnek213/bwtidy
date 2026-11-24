import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { formatTime } from '../shared/format.ts'
import { jsonResponse, mockFetch, renderWithQuery } from '../test-utils.tsx'
import ChangeLogPage from './ChangeLogPage.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

function entry(id: number, at: string, fields: Record<string, unknown>) {
  return {
    id,
    at,
    source: 'ui',
    operationKind: 'setLeaf',
    itemIds: ['item-1'],
    createdItemIds: [],
    columns: [],
    result: 'ok',
    backup: null,
    restoredBackup: null,
    restoredChangeId: null,
    ...fields,
  }
}

const ENTRIES = [
  entry(3, '2026-10-29T10:00:03Z', { source: 'mcp', operationKind: 'trashItem', itemIds: ['item-2'] }),
  entry(2, '2026-10-29T10:00:02Z', {
    source: 'mixed',
    operationKind: 'setLeaf',
    itemIds: ['item-1', 'gone-item'],
    columns: ['login.username', 'fields.Pin'],
  }),
  entry(1, '2026-10-29T10:00:01Z', {
    operationKind: 'createItem',
    itemIds: [],
    createdItemIds: ['item-2'],
    columns: ['name'],
  }),
]

function show(entries: unknown[], folders: unknown[] = []) {
  mockFetch({
    'GET /api/grid': () =>
      jsonResponse(200, {
        columns: [{ id: 'name', label: 'name', group: 'item', secret: false, multi: false, readOnly: false }],
        rows: [
          { id: 'item-1', revisionDate: '2026-10-29T10:00:05Z', cells: { name: 'Code forge' } },
          { id: 'item-2', revisionDate: '2026-10-29T10:00:05Z', cells: { name: 'Bank\tof\nTabs' } },
        ],
        folders,
      }),
    'GET /api/change-log': () => jsonResponse(200, entries),
    'GET /api/backups': () =>
      jsonResponse(200, [{ name: '20261029T100000Z.zip.age', size: 1000, time: '2026-10-29T10:00:00Z' }]),
  })
  renderWithQuery(<ChangeLogPage />)
}

const time = (second: number) => formatTime(`2026-10-29T10:00:0${second}Z`, 'system')

test('the Diff report button is disabled when no change row is shown, a backup row alone does not count', async () => {
  show([])
  expect(await screen.findByRole('link', { name: '20261029T100000Z.zip.age' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Diff report' })).toBeDisabled()
})

const HINT = 'Lists what every shown change did. Filter the log first to narrow it down.'

test('the Diff report button explains itself in a tooltip, disabled or not', async () => {
  show([])
  const disabled = await screen.findByRole('button', { name: 'Diff report' })
  expect(disabled).toBeDisabled()
  fireEvent.mouseOver(disabled.parentElement as HTMLElement)
  expect(await screen.findByRole('tooltip')).toHaveTextContent(HINT)
  cleanup()

  show(ENTRIES)
  const enabled = await screen.findByRole('button', { name: 'Diff report' })
  await waitFor(() => expect(enabled).toBeEnabled())
  fireEvent.mouseOver(enabled.parentElement as HTMLElement)
  expect(await screen.findByRole('tooltip')).toHaveTextContent(HINT)
})

test('the diff report has one line per item and field, oldest first, without backups', async () => {
  show(ENTRIES)
  await screen.findByRole('table', { name: 'Change log' })
  fireEvent.click(screen.getByRole('button', { name: 'Diff report' }))

  const dialog = await screen.findByRole('dialog', { name: 'Diff report' })
  expect(dialog).toHaveTextContent('3 operations, 6 lines')
  const table = within(dialog).getByRole('table', { name: 'Diff report' })
  const lines = within(table)
    .getAllByRole('row')
    .map((row) => [...row.children].map((cell) => cell.textContent))
  expect(lines).toEqual([
    ['Time', 'Source', 'Operation', 'Item', 'Field'],
    [time(1), 'UI', 'Create an item', 'Bank\tof\nTabs', 'name'],
    [time(2), 'Mixed', 'Set a value', 'Code forge', 'login.username'],
    [time(2), 'Mixed', 'Set a value', 'Code forge', 'fields.Pin'],
    [time(2), 'Mixed', 'Set a value', 'gone-item', 'login.username'],
    [time(2), 'Mixed', 'Set a value', 'gone-item', 'fields.Pin'],
    [time(3), 'MCP', 'Move to trash', 'Bank\tof\nTabs', '(whole item)'],
  ])
})

const FOLDER_ENTRIES = [
  entry(13, '2026-10-29T10:00:03Z', { operationKind: 'deleteFolder', itemIds: [], columns: ['folder'], folderId: 'f-gone' }),
  entry(12, '2026-10-29T10:00:02Z', { operationKind: 'renameFolder', itemIds: [], columns: ['folder'], folderId: 'f-work' }),
  entry(11, '2026-10-29T10:00:01Z', { operationKind: 'trashItem', itemIds: ['item-1'] }),
  entry(10, '2026-10-29T10:00:00Z', { operationKind: 'setLeaf', itemIds: [], createdItemIds: [], columns: ['name'] }),
]

test('folder rows give a Folder NAME line, by id when the vault lacks the folder, a row without items or folder none', async () => {
  const writeText = vi.fn(async () => {})
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
  show(FOLDER_ENTRIES, [{ id: 'f-work', name: 'Job', items: 2, trashedItems: 0 }])
  await screen.findByRole('table', { name: 'Change log' })
  fireEvent.click(screen.getByRole('button', { name: 'Diff report' }))

  const dialog = await screen.findByRole('dialog', { name: 'Diff report' })
  expect(dialog).toHaveTextContent('4 operations, 3 lines')
  const table = within(dialog).getByRole('table', { name: 'Diff report' })
  const lines = within(table)
    .getAllByRole('row')
    .map((row) => [...row.children].map((cell) => cell.textContent))
  expect(lines).toEqual([
    ['Time', 'Source', 'Operation', 'Item', 'Field'],
    [time(1), 'UI', 'Move to trash', 'Code forge', '(whole item)'],
    [time(2), 'UI', 'Rename folder', 'Folder Job', 'folder'],
    [time(3), 'UI', 'Delete folder', 'Folder f-gone', 'folder'],
  ])

  fireEvent.click(within(dialog).getByRole('button', { name: 'Copy' }))
  expect(await within(dialog).findByText('Copied')).toBeInTheDocument()
  expect(writeText).toHaveBeenCalledWith(
    [
      'Time\tSource\tOperation\tItem\tField',
      `${time(1)}\tUI\tMove to trash\tCode forge\t(whole item)`,
      `${time(2)}\tUI\tRename folder\tFolder Job\tfolder`,
      `${time(3)}\tUI\tDelete folder\tFolder f-gone\tfolder`,
    ].join('\n'),
  )
})

test('Copy writes the table as tab separated text with a header line', async () => {
  const writeText = vi.fn(async () => {})
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
  show(ENTRIES)
  await screen.findByRole('table', { name: 'Change log' })
  fireEvent.click(screen.getByRole('button', { name: 'Diff report' }))
  const dialog = await screen.findByRole('dialog', { name: 'Diff report' })

  fireEvent.click(within(dialog).getByRole('button', { name: 'Copy' }))

  expect(await within(dialog).findByText('Copied')).toBeInTheDocument()
  expect(writeText).toHaveBeenCalledWith(
    [
      'Time\tSource\tOperation\tItem\tField',
      `${time(1)}\tUI\tCreate an item\tBank of Tabs\tname`,
      `${time(2)}\tMixed\tSet a value\tCode forge\tlogin.username`,
      `${time(2)}\tMixed\tSet a value\tCode forge\tfields.Pin`,
      `${time(2)}\tMixed\tSet a value\tgone-item\tlogin.username`,
      `${time(2)}\tMixed\tSet a value\tgone-item\tfields.Pin`,
      `${time(3)}\tMCP\tMove to trash\tBank of Tabs\t(whole item)`,
    ].join('\n'),
  )
})

test('Copy says so when the clipboard refuses', async () => {
  const writeText = vi.fn(async () => {
    throw new Error('denied')
  })
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
  show(ENTRIES)
  await screen.findByRole('table', { name: 'Change log' })
  fireEvent.click(screen.getByRole('button', { name: 'Diff report' }))
  const dialog = await screen.findByRole('dialog', { name: 'Diff report' })

  fireEvent.click(within(dialog).getByRole('button', { name: 'Copy' }))

  expect(await within(dialog).findByText('Could not copy, select it by hand')).toBeInTheDocument()
})
