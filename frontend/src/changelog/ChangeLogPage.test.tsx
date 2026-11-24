import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App from '../App.tsx'
import { jsonResponse, mockFetch, renderWithQuery, typeDay } from '../test-utils.tsx'
import { formatTime } from '../shared/format.ts'
import { operationLabel } from '../shared/operationLabels.ts'
import { createTheme } from '@mui/material/styles'
import ChangeLogPage, { faded } from './ChangeLogPage.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

test('the change log is reachable from the app bar and names known items', async () => {
  mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false }),
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/grid': () =>
      jsonResponse(200, {
        columns: [{ id: 'name', label: 'name', group: 'item', secret: false, multi: false, readOnly: false }],
        rows: [{ id: 'item-1', revisionDate: '2026-10-01T10:00:00Z', cells: { name: 'Code forge' } }],
      }),
    'GET /api/change-log': () =>
      jsonResponse(200, [
        {
          id: 7,
          at: '2026-10-26T12:00:00Z',
          source: 'ui',
          operationKind: 'setLeaf',
          itemIds: ['item-1', 'gone-item'],
          createdItemIds: [],
          columns: ['login.username'],
          result: 'applied',
          backup: '20261026T120000Z.zip.age',
        },
      ]),
    'GET /api/backups': () => jsonResponse(200, []),
  })
  renderWithQuery(<App />)

  fireEvent.click(await screen.findByRole('button', { name: 'Change log' }))

  const table = await screen.findByRole('table', { name: 'Change log' })
  const row = within(table).getAllByRole('row')[1]
  expect(row).toHaveTextContent('Set a value')
  expect(row).toHaveTextContent('setLeaf')
  expect(row).toHaveTextContent('Code forge, gone-item')
  expect(row).toHaveTextContent('20261026T120000Z.zip.age')
})

test('entries are sorted newest first', async () => {
  mockFetch({
    'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
    'GET /api/change-log': () =>
      jsonResponse(200, [
        entry(1, { operationKind: 'trashItem', itemIds: ['a'], at: '2026-10-01T00:00:00Z' }),
        entry(2, { operationKind: 'moveToFolder', itemIds: ['b', 'c'], result: 'failed', at: '2026-10-02T00:00:00Z' }),
      ]),
    'GET /api/backups': () => jsonResponse(200, []),
  })
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  const [, newest, oldest] = within(table).getAllByRole('row')
  expect(newest).toHaveTextContent('moveToFolder')
  expect(oldest).toHaveTextContent('trashItem')
})

test('the operation shows its label, its kind and on hover what it did', async () => {
  mockFetch({
    'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
    'GET /api/change-log': () =>
      jsonResponse(200, [
        entry(1, { operationKind: 'setLeaf', itemIds: ['a'], backup: null }),
        entry(2, { source: 'mcp', operationKind: 'futureKind', itemIds: ['b'], backup: null }),
      ]),
    'GET /api/backups': () => jsonResponse(200, []),
  })
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  // Newest first
  const [, unknown, known] = within(table).getAllByRole('row')
  const operation = within(known).getAllByRole('cell')[2]
  expect(within(operation).getByText('Set a value')).toBeInTheDocument()
  const kind = within(operation).getByText('setLeaf')
  expect(getComputedStyle(kind).fontFamily).toBe('monospace')

  fireEvent.mouseOver(within(operation).getByText('Set a value'))
  expect(await screen.findByRole('tooltip')).toHaveTextContent(
    'Changed one value of an item: a built-in field such as the username, or a custom field.',
  )

  expect(within(unknown).getAllByRole('cell')[2]).toHaveTextContent(/^futureKind$/)
})

test('the operation names the fields it wrote, three of them and the rest on hover', async () => {
  mockFetch({
    'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
    'GET /api/change-log': () =>
      jsonResponse(200, [
        entry(1, { columns: ['login.username', 'fields.Pin'] }),
        entry(2, {
          operationKind: 'convertItem',
          columns: ['type', 'fields.username', 'login.username', 'notes', 'fields.Recovery codes'],
        }),
        entry(3, { operationKind: 'trashItem' }),
      ]),
    'GET /api/backups': () => jsonResponse(200, []),
  })
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  // Newest first
  const [none, five, two] = within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[2])

  const both = within(two).getByText('login.username, fields.Pin')
  expect(getComputedStyle(both).fontFamily).toBe('monospace')

  const shown = within(five).getByText('type, fields.username, login.username +2 more')
  expect(getComputedStyle(shown).fontFamily).toBe('monospace')
  fireEvent.mouseOver(shown)
  expect(await screen.findByRole('tooltip')).toHaveTextContent(
    'type, fields.username, login.username, notes, fields.Recovery codes',
  )

  expect(none.textContent).toBe(`${operationLabel('trashItem')?.label}trashItem`)
})

const BACKUP = '20261029T100000Z.zip.age'
const OLD_BACKUP = '20260701T100000Z.zip.age'

const PLAN = {
  changeLogId: 11,
  backup: BACKUP,
  summary: `Restore 2 items from ${BACKUP}`,
  operation: {
    kind: 'restoreItems',
    backup: BACKUP,
    itemIds: ['item-1', 'gone-item'],
    revisionDates: { 'item-1': '2026-10-29T10:00:05Z' },
  },
  items: [
    { itemId: 'item-1', itemName: 'Code forge', action: 'put', text: 'put back as before' },
    { itemId: 'gone-item', itemName: null, action: 'leave', text: 'leave as it is' },
  ],
}

function entry(id: number, fields: Record<string, unknown>) {
  return {
    id,
    at: `2026-10-29T10:00:${String(id).padStart(2, '0')}Z`,
    source: 'ui',
    operationKind: 'setLeaf',
    itemIds: ['item-1'],
    createdItemIds: [],
    columns: [],
    result: 'ok',
    backup: BACKUP,
    restoredBackup: null,
    restoredChangeId: null,
    restoredBy: null,
    ...fields,
  }
}

function mockChangeLog(routes: Record<string, (init: RequestInit) => Response> = {}) {
  return mockFetch({
    'GET /api/grid': () =>
      jsonResponse(200, {
        columns: [{ id: 'name', label: 'name', group: 'item', secret: false, multi: false, readOnly: false }],
        rows: [
          { id: 'item-1', revisionDate: '2026-10-29T10:00:05Z', cells: { name: 'Code forge' } },
          { id: 'note-1', revisionDate: '2026-10-29T10:00:04Z', cells: { name: 'bwtidy' }, toolNote: true },
        ],
        toolNoteId: 'note-1',
      }),
    'GET /api/change-log': () =>
      jsonResponse(200, [
        entry(11, { itemIds: ['item-1', 'gone-item'] }),
        entry(12, { itemIds: ['note-1'] }),
        entry(13, { result: 'failed' }),
        entry(14, { backup: OLD_BACKUP }),
      ]),
    'GET /api/backups': () => jsonResponse(200, [{ name: BACKUP, size: 1000, time: '2026-10-29T10:00:00Z' }]),
    ...routes,
  })
}

function restoreButtonOf(name: RegExp) {
  return within(screen.getByRole('row', { name })).queryByRole('button', { name: 'Restore' })
}

test('an ok row with a backup has Restore, the tool note, a failure and a deleted backup have none', async () => {
  mockChangeLog()
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  await waitFor(() => expect(within(table).getAllByRole('button', { name: 'Restore' })).toHaveLength(1))
  expect(restoreButtonOf(/Code forge, gone-item/)).toBeInTheDocument()
  expect(restoreButtonOf(/bwtidy/)).toBeNull()
  expect(restoreButtonOf(/Failed/)).toBeNull()
  expect(restoreButtonOf(new RegExp(OLD_BACKUP))).toBeNull()
})

test('Source and Result show readable labels, other values as they are', async () => {
  mockFetch({
    'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
    'GET /api/change-log': () =>
      jsonResponse(200, [
        entry(1, { source: 'ui', result: 'ok', backup: null }),
        entry(2, { source: 'mcp', result: 'failed', backup: null }),
        entry(3, { source: 'mixed', result: 'ok', backup: null }),
        entry(4, { source: 'future', result: 'later', backup: null }),
      ]),
    'GET /api/backups': () => jsonResponse(200, []),
  })
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  // Newest first
  const [, future, mixed, mcp, ui] = within(table).getAllByRole('row')
  const cellsOf = (row: HTMLElement) => within(row).getAllByRole('cell')
  expect([cellsOf(ui)[1].textContent, cellsOf(ui)[4].textContent]).toEqual(['UI', 'OK'])
  expect([cellsOf(mcp)[1].textContent, cellsOf(mcp)[4].textContent]).toEqual(['MCP', 'Failed'])
  expect([cellsOf(mixed)[1].textContent, cellsOf(mixed)[4].textContent]).toEqual(['Mixed', 'OK'])
  expect([cellsOf(future)[1].textContent, cellsOf(future)[4].textContent]).toEqual(['future', 'later'])
})

test('rows that follow a row with the same backup show their backup and Restore faded', async () => {
  mockFetch({
    'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
    'GET /api/change-log': () =>
      jsonResponse(200, [
        entry(1, { backup: OLD_BACKUP }),
        entry(2, { backup: BACKUP }),
        entry(3, { backup: BACKUP }),
        entry(4, { backup: BACKUP }),
        entry(5, { backup: null }),
      ]),
    'GET /api/backups': () =>
      jsonResponse(200, [
        { name: BACKUP, size: 1000, time: '2026-10-29T09:00:00Z' },
        { name: OLD_BACKUP, size: 1000, time: '2026-07-01T10:00:00Z' },
      ]),
  })
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  await waitFor(() => expect(within(table).getAllByRole('button', { name: 'Restore' })).toHaveLength(4))
  // Newest first: 5 (no backup), 4, 3, 2 (BACKUP), 1 (OLD_BACKUP), then the two backup rows
  const rows = within(table).getAllByRole('row')
  const colorsOf = (row: HTMLElement) => ({
    backup: getComputedStyle(within(row).getAllByRole('cell')[5]).color,
    restore: getComputedStyle(within(row).getByRole('button', { name: 'Restore' })).color,
  })
  const first = colorsOf(rows[2])
  const second = colorsOf(rows[3])
  const third = colorsOf(rows[4])
  const other = colorsOf(rows[5])
  expect(second.backup).not.toBe(first.backup)
  expect(second.restore).not.toBe(first.restore)
  expect(third).toEqual(second)
  expect(other).toEqual(first)
})

test('a repeated backup is mixed 43% toward the background, so 57% of the color is kept', () => {
  const theme = createTheme()
  expect(faded('#ffffff', theme)).toBe(`color-mix(in srgb, #ffffff 57%, ${theme.palette.background.default})`)
})

async function openRestore() {
  renderWithQuery(<ChangeLogPage />)
  await screen.findByRole('table', { name: 'Change log' })
  fireEvent.click(await screen.findByRole('button', { name: 'Restore' }))
  return screen.findByRole('dialog', { name: 'Restore' })
}

test('Restore lists the plan and queues the restoreItems operation', async () => {
  const fetchMock = mockChangeLog({
    'GET /api/restore-plan?changeLogId=11': () => jsonResponse(200, PLAN),
    'POST /api/queue': () => jsonResponse(201, { ids: [31] }),
  })
  const dialog = await openRestore()

  expect(
    await within(dialog).findByText(`Put these items back as they were before this change, from backup ${BACKUP}.`),
  ).toBeInTheDocument()
  const plan = within(dialog).getByRole('list', { name: 'What the restore does' })
  const items = within(plan).getAllByRole('listitem')
  expect(items[0]).toHaveTextContent('Code forge')
  expect(items[0]).toHaveTextContent('put back as before')
  expect(items[1]).toHaveTextContent('gone-item')
  expect(items[1]).toHaveTextContent('leave as it is')
  expect(within(dialog).getByText('bwtidy takes a new backup first, so this restore can be restored too.')).toBeInTheDocument()

  fireEvent.click(within(dialog).getByRole('button', { name: 'Restore' }))

  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({ operations: [PLAN.operation] })
})

test('a 422 when queueing shows its reason in the dialog', async () => {
  const fetchMock = mockChangeLog({
    'GET /api/restore-plan?changeLogId=11': () => jsonResponse(200, PLAN),
    'POST /api/queue': () => jsonResponse(422, { detail: `backup ${BACKUP} does not decrypt with the session's password` }),
  })
  const dialog = await openRestore()
  await within(dialog).findByRole('list', { name: 'What the restore does' })

  fireEvent.click(within(dialog).getByRole('button', { name: 'Restore' }))

  expect(await within(dialog).findByText(/does not decrypt with the session's password/)).toBeInTheDocument()
  expect(screen.getByRole('dialog', { name: 'Restore' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})

test('a 422 for the plan shows its reason and nothing can be queued', async () => {
  const fetchMock = mockChangeLog({
    'GET /api/restore-plan?changeLogId=11': () =>
      jsonResponse(422, { detail: 'item gone-item cannot be created anew with its attachments' }),
  })
  const dialog = await openRestore()

  expect(await within(dialog).findByText(/cannot be created anew with its attachments/)).toBeInTheDocument()
  expect(within(dialog).getByRole('button', { name: 'Restore' })).toBeDisabled()
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
})

const OLDER_BACKUP = '20261029T095900Z.zip.age'

function mockWithBackups(routes: Record<string, (init: RequestInit) => Response> = {}) {
  return mockChangeLog({
    'GET /api/backups': () =>
      jsonResponse(200, [
        { name: BACKUP, size: 1234567, time: '2026-10-29T10:00:00Z' },
        { name: OLDER_BACKUP, size: 2048, time: '2026-10-29T09:59:00Z' },
      ]),
    ...routes,
  })
}

test('backups are rows of their own, merged by time, with a download link and the size', async () => {
  mockWithBackups()
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  const newest = await within(table).findByRole('row', { name: new RegExp(`backup.*${BACKUP}`) })
  expect(newest).toHaveTextContent('1.2 MB')
  const link = within(newest).getByRole('link', { name: BACKUP })
  expect(link).toHaveAttribute('href', `/api/backups/${BACKUP}`)
  expect(link).toHaveAttribute('download', BACKUP)
  const older = within(table).getByRole('row', { name: new RegExp(`backup.*${OLDER_BACKUP}`) })
  expect(older).toHaveTextContent('2.0 kB')

  // The change rows (10:00:11 and later) come before the backup of 10:00:00, which comes before the older backup.
  const rows = within(table).getAllByRole('row').slice(1)
  const kinds = rows.map((row) => (within(row).queryByRole('link') ? 'backup' : 'change'))
  expect(kinds).toEqual(['change', 'change', 'change', 'change', 'backup', 'backup'])
  expect(screen.getByText(/age -d -o backup\.zip/)).toBeInTheDocument()
  expect(screen.getByText(/docs\/restore\.md/)).toBeInTheDocument()
})

test('the newest backup has a disabled Delete, an older one asks first and sends DELETE', async () => {
  const fetchMock = mockWithBackups({
    [`DELETE /api/backups/${OLDER_BACKUP}`]: () => jsonResponse(204),
  })
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  const newest = await within(table).findByRole('row', { name: new RegExp(`backup.*${BACKUP}`) })
  expect(within(newest).getByRole('button', { name: 'Delete' })).toBeDisabled()
  const older = within(table).getByRole('row', { name: new RegExp(`backup.*${OLDER_BACKUP}`) })
  fireEvent.click(within(older).getByRole('button', { name: 'Delete' }))

  const dialog = await screen.findByRole('dialog', { name: 'Delete backup' })
  expect(dialog).toHaveTextContent(
    `Delete backup ${OLDER_BACKUP} for good? Changes made with it can no longer be restored.`,
  )
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)

  fireEvent.click(within(older).getByRole('button', { name: 'Delete' }))
  fireEvent.click(within(await screen.findByRole('dialog', { name: 'Delete backup' })).getByRole('button', { name: 'Delete' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const deletes = fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE')
  expect(deletes.map(([input]) => String(input))).toEqual([`/api/backups/${OLDER_BACKUP}`])
})

test('a 409 stays in the dialog with its reason', async () => {
  mockWithBackups({
    [`DELETE /api/backups/${OLDER_BACKUP}`]: () =>
      jsonResponse(409, { detail: `Backup ${OLDER_BACKUP} is what the queued restore #5 reads.` }),
  })
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  const older = await within(table).findByRole('row', { name: new RegExp(`backup.*${OLDER_BACKUP}`) })
  fireEvent.click(within(older).getByRole('button', { name: 'Delete' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete backup' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))

  expect(await within(dialog).findByText(/Nothing was deleted: .*queued restore #5/)).toBeInTheDocument()
  expect(screen.getByRole('dialog', { name: 'Delete backup' })).toBeInTheDocument()
})

test('a change row whose backup is gone keeps the name struck through without Restore', async () => {
  mockWithBackups()
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  await within(table).findByRole('row', { name: new RegExp(`backup.*${BACKUP}`) })
  const gone = within(table).getByRole('row', { name: new RegExp(OLD_BACKUP) })
  const name = within(gone).getByText(OLD_BACKUP)
  expect(name).toHaveStyle({ textDecoration: 'line-through' })
  expect(name).toHaveAttribute('title', 'This backup was deleted')
  expect(within(gone).queryByRole('button', { name: 'Restore' })).toBeNull()
  // A row whose backup exists is not struck through.
  const kept = within(table).getByRole('row', { name: /Code forge, gone-item/ })
  expect(within(kept).getByText(BACKUP)).not.toHaveStyle({ textDecoration: 'line-through' })
})

test('a createItem row names the item it created', async () => {
  mockChangeLog({
    'GET /api/change-log': () =>
      jsonResponse(200, [entry(15, { operationKind: 'createItem', itemIds: [], createdItemIds: ['item-1'] })]),
  })
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  const row = within(table).getAllByRole('row')[1]
  await waitFor(() => expect(within(row).getAllByRole('cell')[3]).toHaveTextContent(/^Code forge$/))
  expect(row).toHaveTextContent('Create an item')
  expect(within(row).getByRole('button', { name: 'Restore' })).toBeInTheDocument()
})

test('a restore row names the backup it put back and links to the change it restored', async () => {
  const scrollIntoView = vi.fn()
  Element.prototype.scrollIntoView = scrollIntoView
  mockChangeLog({
    'GET /api/change-log': () =>
      jsonResponse(200, [
        entry(5, { itemIds: ['item-1'], backup: OLD_BACKUP }),
        entry(6, { itemIds: ['item-1'], backup: BACKUP }),
        entry(7, {
          operationKind: 'restoreItems',
          itemIds: ['item-1'],
          restoredBackup: OLD_BACKUP,
          restoredChangeId: 5,
        }),
        entry(8, {
          operationKind: 'restoreItems',
          itemIds: ['item-1'],
          restoredBackup: OLD_BACKUP,
          restoredChangeId: 999,
        }),
      ]),
  })
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  const [, withoutRow, withLink] = within(table).getAllByRole('row')
  expect(withoutRow).toHaveTextContent(`From backup ${OLD_BACKUP}`)
  expect(within(withoutRow).queryByRole('link', { name: /the change of/ })).toBeNull()
  expect(within(withoutRow).queryByRole('button', { name: /the change of/ })).toBeNull()
  expect(withLink).toHaveTextContent(`From backup ${OLD_BACKUP}`)

  const target = document.getElementById('change-log-row-5') as HTMLElement
  expect(target).not.toBeNull()
  const link = within(withLink).getByRole('button', { name: /^the change of / })
  fireEvent.click(link)
  expect(scrollIntoView).toHaveBeenCalledTimes(1)
  expect(scrollIntoView.mock.contexts[0]).toBe(target)
  const before = getComputedStyle(document.getElementById('change-log-row-6') as HTMLElement).backgroundColor
  const highlighted = getComputedStyle(target).backgroundColor
  expect(highlighted).not.toBe(before)
  await waitFor(() => expect(getComputedStyle(target).backgroundColor).toBe(before), { timeout: 3000 })
})

test('the intro is capped at a readable width', async () => {
  mockFetch({
    'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
    'GET /api/change-log': () => jsonResponse(200, []),
    'GET /api/backups': () => jsonResponse(200, []),
  })
  renderWithQuery(<ChangeLogPage />)

  const intro = await screen.findByText(/The latest changes written to the vault/)
  // jsdom turns ch into px by font size, so compare with an element of that font size styled with 75ch itself.
  const probe = document.createElement('div')
  probe.style.fontSize = getComputedStyle(intro).fontSize
  probe.style.maxWidth = '75ch'
  document.body.append(probe)
  expect(getComputedStyle(probe).maxWidth).not.toBe('none')
  expect(getComputedStyle(intro).maxWidth).toBe(getComputedStyle(probe).maxWidth)
  probe.remove()
})

const FILTER_GRID = {
  columns: [
    { id: 'name', label: 'name', group: 'item', secret: false, multi: false, readOnly: false },
    { id: 'login.username', label: 'login.username', group: 'login', secret: false, multi: false, readOnly: false },
    { id: 'revisionDate', label: 'revisionDate', group: 'item', secret: false, multi: false, readOnly: true },
  ],
  rows: [
    { id: 'item-1', revisionDate: '2026-10-29T10:00:05Z', cells: { name: 'Code forge' } },
    { id: 'item-2', revisionDate: '2026-10-29T10:00:05Z', cells: { name: 'Code forge backup' } },
    { id: 'item-3', revisionDate: '2026-10-29T10:00:05Z', cells: { name: 'Bank' } },
  ],
}

// Every GET /api/change-log answers the entries whatever its query, and the URLs asked are kept.
function mockFilteredLog(entries: unknown[] = [entry(1, {})], more = false) {
  const asked: string[] = []
  const routes = mockFetch({
    'GET /api/grid': () => jsonResponse(200, FILTER_GRID),
    'GET /api/backups': () =>
      jsonResponse(200, [
        { name: BACKUP, size: 1000, time: '2026-10-29T10:00:00Z' },
        { name: OLD_BACKUP, size: 2000, time: '2026-07-01T10:00:00Z' },
      ]),
  })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input)
      if (!url.startsWith('/api/change-log')) {
        return routes(input, init)
      }
      asked.push(url)
      return jsonResponse(200, entries, { 'X-Bwtidy-More': more ? 'true' : 'false' })
    }),
  )
  return asked
}

function lastQuery(asked: string[]): URLSearchParams {
  const url = asked[asked.length - 1]
  return new URLSearchParams(url.includes('?') ? url.slice(url.indexOf('?')) : '')
}

async function choose(label: string, ...options: string[]) {
  fireEvent.mouseDown(screen.getByRole('combobox', { name: label }))
  const listbox = await screen.findByRole('listbox')
  for (const option of options) {
    fireEvent.click(within(listbox).getByRole('option', { name: option }))
  }
  fireEvent.keyDown(listbox, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('listbox')).not.toBeInTheDocument())
}

test('the dates send the UTC instants of the local days start and end', async () => {
  const asked = mockFilteredLog()
  renderWithQuery(<ChangeLogPage />)
  await screen.findByRole('table', { name: 'Change log' })
  expect(asked).toEqual(['/api/change-log'])

  typeDay('From', '2026-11-01')
  typeDay('To', '2026-11-03')

  await waitFor(() => expect(lastQuery(asked).get('to')).toBe(new Date(2026, 10, 3, 23, 59, 59, 999).toISOString()))
  expect(lastQuery(asked).get('from')).toBe(new Date(2026, 10, 1).toISOString())
})

test('source, operation and result send what is chosen', async () => {
  const asked = mockFilteredLog()
  renderWithQuery(<ChangeLogPage />)
  await screen.findByRole('table', { name: 'Change log' })

  await choose('Source', 'MCP', 'Mixed')
  await waitFor(() => expect(lastQuery(asked).getAll('source')).toEqual(['mcp', 'mixed']))
  await choose('Operation', 'Move to folder', 'Set a value')
  await waitFor(() => expect(lastQuery(asked).getAll('kind')).toEqual(['moveToFolder', 'setLeaf']))
  await choose('Result', 'Failed')
  await waitFor(() => expect(lastQuery(asked).get('result')).toBe('failed'))
  expect(lastQuery(asked).getAll('source')).toEqual(['mcp', 'mixed'])
})

test('the field filter offers the writable columns and the item coordinates', async () => {
  const asked = mockFilteredLog()
  renderWithQuery(<ChangeLogPage />)
  await screen.findByRole('table', { name: 'Change log' })

  const field = screen.getByRole('combobox', { name: 'Field' })
  fireEvent.mouseDown(field)
  const options = within(await screen.findByRole('listbox'))
    .getAllByRole('option')
    .map((option) => option.textContent)
  expect(options).toEqual(['name', 'folder', 'collections', 'organization', 'login.username'])

  fireEvent.click(screen.getByRole('option', { name: 'login.username' }))
  await waitFor(() => expect(lastQuery(asked).getAll('column')).toEqual(['login.username']))
  // A field no grid column has any more is typed in.
  fireEvent.change(field, { target: { value: 'fields.Old pin' } })
  fireEvent.keyDown(field, { key: 'Enter' })
  await waitFor(() => expect(lastQuery(asked).getAll('column')).toEqual(['login.username', 'fields.Old pin']))
})

test('the items text sends the ids of the items named like it, nothing when none is', async () => {
  const asked = mockFilteredLog()
  renderWithQuery(<ChangeLogPage />)
  await screen.findByRole('table', { name: 'Change log' })

  fireEvent.change(screen.getByLabelText('Items'), { target: { value: 'code forge' } })
  await waitFor(() => expect(lastQuery(asked).getAll('item')).toEqual(['item-1', 'item-2']))
  expect(screen.getByText('2 items')).toBeInTheDocument()

  const before = asked.length
  fireEvent.change(screen.getByLabelText('Items'), { target: { value: 'nowhere' } })
  expect(await screen.findByText('No changes match the filters.')).toBeInTheDocument()
  expect(screen.getByText('No item has that in its name')).toBeInTheDocument()
  expect(asked).toHaveLength(before)
})

test('Clear filters empties the fields and shows the whole log again', async () => {
  const asked = mockFilteredLog()
  renderWithQuery(<ChangeLogPage />)
  await screen.findByRole('table', { name: 'Change log' })
  typeDay('From', '2026-11-01')
  fireEvent.change(screen.getByLabelText('Items'), { target: { value: 'Bank' } })
  await choose('Result', 'OK')
  await waitFor(() => expect(lastQuery(asked).get('result')).toBe('ok'))

  fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))

  expect(screen.getByLabelText('From', { selector: 'input' })).toHaveValue('')
  expect(screen.getByLabelText('Items')).toHaveValue('')
  expect(screen.getByRole('combobox', { name: 'Result' })).toHaveTextContent('All')
  // The answer to no filters is cached already, so asking again is not needed to show it.
  await waitFor(() => expect(within(screen.getByRole('table', { name: 'Change log' })).getAllByRole('link')).toHaveLength(2))
})

test('Clear filters is disabled while nothing is filtered', async () => {
  mockFilteredLog()
  renderWithQuery(<ChangeLogPage />)
  await screen.findByRole('table', { name: 'Change log' })
  const clear = screen.getByRole('button', { name: 'Clear filters' })
  expect(clear).toBeDisabled()

  fireEvent.change(screen.getByLabelText('Items'), { target: { value: 'Bank' } })
  expect(clear).toBeEnabled()

  fireEvent.click(clear)
  expect(clear).toBeDisabled()
})

test('with a filter set only the backups a shown change names are rows', async () => {
  mockFilteredLog([entry(1, { backup: BACKUP })])
  renderWithQuery(<ChangeLogPage />)
  const table = await screen.findByRole('table', { name: 'Change log' })
  await waitFor(() => expect(within(table).getAllByRole('link').map((link) => link.textContent)).toEqual([BACKUP, OLD_BACKUP]))

  await choose('Source', 'UI')

  await waitFor(() => expect(within(table).getAllByRole('link').map((link) => link.textContent)).toEqual([BACKUP]))
})

test('more matches than the page holds say so', async () => {
  mockFilteredLog([entry(1, {})], true)
  renderWithQuery(<ChangeLogPage />)
  expect(await screen.findByText('Showing the newest 200 changes.')).toBeInTheDocument()

  await choose('Operation', 'Set a value')

  expect(await screen.findByText('Showing the newest 200 matches.')).toBeInTheDocument()
})

test('a change a later restore undid has a restore mark that names the fields and items and shows the restore', async () => {
  const scrollIntoView = vi.fn()
  Element.prototype.scrollIntoView = scrollIntoView
  const restoreAt = '2026-10-29T10:00:07Z'
  const offPage = '2026-11-01T12:00:00Z'
  mockChangeLog({
    'GET /api/change-log': () =>
      jsonResponse(200, [
        entry(4, {
          itemIds: ['item-1', 'gone-item'],
          columns: ['login.username', 'notes'],
          restoredBy: { id: 7, at: restoreAt, itemIds: ['item-1', 'gone-item'] },
        }),
        entry(5, { operationKind: 'trashItem', restoredBy: { id: 999, at: offPage, itemIds: ['item-1'] } }),
        entry(6, { columns: ['name'] }),
        entry(7, { operationKind: 'restoreItems', itemIds: ['item-1', 'gone-item'], restoredBackup: OLD_BACKUP }),
      ]),
  })
  renderWithQuery(<ChangeLogPage />)

  const table = await screen.findByRole('table', { name: 'Change log' })
  // Newest first
  const [, restore, unmarked, trashed, changed] = within(table).getAllByRole('row')
  const text = `Restored ${formatTime(restoreAt)}: login.username, notes on Code forge, gone-item`
  const mark = await within(changed).findByRole('button', { name: text })
  fireEvent.mouseOver(mark)
  expect(await screen.findByRole('tooltip')).toHaveTextContent(text)
  fireEvent.click(mark)
  expect(scrollIntoView).toHaveBeenCalledTimes(1)
  expect(scrollIntoView.mock.contexts[0]).toBe(restore)
  expect(getComputedStyle(restore).backgroundColor).not.toBe(getComputedStyle(unmarked).backgroundColor)

  // A row without fields names the items only, and a restore not on the page is no link.
  expect(within(trashed).getByRole('img', { name: `Restored ${formatTime(offPage)}: Code forge` })).toBeInTheDocument()
  expect(within(trashed).queryByRole('button', { name: /^Restored/ })).toBeNull()
  expect(within(unmarked).queryByLabelText(/^Restored/)).toBeNull()
  expect(within(restore).queryByLabelText(/^Restored/)).toBeNull()
})

test('a folder row names the folder and its Restore queues the restoreFolder', async () => {
  const operation = { kind: 'restoreFolder', backup: BACKUP, folderId: 'f-work', changeLogId: 21 }
  const fetchMock = mockFetch({
    'GET /api/grid': () =>
      jsonResponse(200, {
        columns: [],
        rows: [],
        folders: [{ id: 'f-work', name: 'Job', items: 2, trashedItems: 0 }],
      }),
    'GET /api/change-log': () =>
      jsonResponse(200, [
        entry(21, { operationKind: 'renameFolder', itemIds: [], columns: ['folder'], folderId: 'f-work' }),
        entry(22, { operationKind: 'deleteFolder', itemIds: [], columns: ['folder'], folderId: 'f-gone' }),
      ]),
    'GET /api/backups': () => jsonResponse(200, [{ name: BACKUP, size: 1000, time: '2026-10-29T10:00:00Z' }]),
    'GET /api/restore-plan?changeLogId=21': () =>
      jsonResponse(200, {
        changeLogId: 21,
        backup: BACKUP,
        summary: `Restore the folder "Job" from ${BACKUP}`,
        operation,
        items: [],
        folder: { folderId: 'f-work', name: 'Job', action: 'renameBack', text: 'rename back to "Work"' },
      }),
    'POST /api/queue': () => jsonResponse(201, { ids: [32] }),
  })
  renderWithQuery(<ChangeLogPage />)
  const table = await screen.findByRole('table', { name: 'Change log' })
  expect(await within(table).findByRole('row', { name: /Folder Job/ })).toHaveTextContent('Rename folder')
  expect(within(table).getByRole('row', { name: /Folder f-gone/ })).toHaveTextContent('Delete folder')

  fireEvent.click(within(screen.getByRole('row', { name: /Folder Job/ })).getByRole('button', { name: 'Restore' }))
  const dialog = await screen.findByRole('dialog', { name: 'Restore' })
  expect(
    await within(dialog).findByText(`Put this folder back as it was before this change, from backup ${BACKUP}.`),
  ).toBeInTheDocument()
  const [step] = within(within(dialog).getByRole('list', { name: 'What the restore does' })).getAllByRole('listitem')
  expect(step).toHaveTextContent('Folder Job')
  expect(step).toHaveTextContent('rename back to "Work"')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Restore' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({ operations: [operation] })
})

test('an attachment rename shows its attachment ids and its Restore renames the copy back', async () => {
  const operation = {
    kind: 'renameAttachment',
    itemId: 'item-1',
    revisionDate: '2026-10-30T10:00:00Z',
    attachmentId: 'att-new',
    fileName: 'codes.txt',
    shownFileName: 'recovery codes.txt',
  }
  const fetchMock = mockFetch({
    'GET /api/grid': () =>
      jsonResponse(200, {
        columns: [],
        rows: [{ id: 'item-1', revisionDate: '2026-10-30T10:00:00Z', cells: { name: 'Code forge' } }],
      }),
    'GET /api/change-log': () =>
      jsonResponse(200, [
        entry(24, {
          source: 'mcp',
          operationKind: 'renameAttachment',
          itemIds: ['item-1'],
          columns: ['attachments'],
          attachmentIds: { before: 'att-old', after: 'att-new' },
        }),
        entry(25, {
          operationKind: 'renameAttachment',
          itemIds: ['item-1'],
          columns: ['attachments'],
          result: 'failed',
          attachmentIds: { before: 'att-new', after: null },
        }),
      ]),
    'GET /api/backups': () => jsonResponse(200, [{ name: BACKUP, size: 1000, time: '2026-10-29T10:00:00Z' }]),
    'GET /api/restore-plan?changeLogId=24': () =>
      jsonResponse(200, {
        changeLogId: 24,
        backup: BACKUP,
        summary: 'Rename attachment "recovery codes.txt" of "Code forge" to "codes.txt"',
        operation,
        items: [
          {
            itemId: 'item-1',
            itemName: 'Code forge',
            action: 'renameAttachment',
            text: 'rename the attachment "recovery codes.txt" back to "codes.txt"',
          },
        ],
      }),
    'POST /api/queue': () => jsonResponse(201, { ids: [33] }),
  })
  renderWithQuery(<ChangeLogPage />)
  const table = await screen.findByRole('table', { name: 'Change log' })
  const renamed = await within(table).findByRole('row', { name: /replaced by att-new/ })
  expect(renamed).toHaveTextContent('Rename attachment')
  expect(renamed).toHaveTextContent('Code forge')
  expect(renamed).toHaveTextContent('Attachment att-old replaced by att-new')
  const failed = within(table).getByRole('row', { name: /Failed/ })
  expect(failed).toHaveTextContent('Attachment att-new')
  expect(failed).not.toHaveTextContent('replaced by')

  fireEvent.click(within(renamed).getByRole('button', { name: 'Restore' }))
  const dialog = await screen.findByRole('dialog', { name: 'Restore' })
  expect(
    await within(dialog).findByText(
      `Give the attachment back the name it had before this change, from backup ${BACKUP}. The rest of the item stays as it is.`,
    ),
  ).toBeInTheDocument()
  const [step] = within(within(dialog).getByRole('list', { name: 'What the restore does' })).getAllByRole('listitem')
  expect(step).toHaveTextContent('rename the attachment "recovery codes.txt" back to "codes.txt"')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Restore' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({ operations: [operation] })
})

test('a row that names neither an item nor a folder has no Restore', async () => {
  mockFetch({
    'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
    'GET /api/change-log': () => jsonResponse(200, [entry(23, { operationKind: 'createItem', itemIds: [] })]),
    'GET /api/backups': () => jsonResponse(200, [{ name: BACKUP, size: 1000, time: '2026-10-29T10:00:00Z' }]),
  })
  renderWithQuery(<ChangeLogPage />)
  const table = await screen.findByRole('table', { name: 'Change log' })
  await within(table).findByRole('row', { name: /createItem/ })
  expect(within(table).queryByRole('button', { name: 'Restore' })).toBeNull()
})
