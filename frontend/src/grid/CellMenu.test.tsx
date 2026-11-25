import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { jsonResponse, type mockFetch } from '../test-utils.tsx'
import type { GridData, GridRow } from '../api/grid.ts'
import { gridAnswer, gridColumn, renderGrid, type Routes } from './testSupport.tsx'
import { toFillAction } from './toFill.ts'

afterEach(() => {
  vi.unstubAllGlobals()
})

const username = gridColumn('login.username')
const password = gridColumn('login.password', { secret: true })
const bios = gridColumn('fields.BIOS Password', { secret: true })

// Code forge's password is on the list, Laptop's BIOS password is empty but not listed, Mail's password is filled.
const grid = gridAnswer(
  ['name', 'type', username, password, bios],
  [
    { id: 'item-1', revisionDate: 'r1', cells: { name: 'Code forge', type: 'login', 'login.username': 'user' }, emptySecrets: ['login.password'] },
    { id: 'item-2', revisionDate: 'r2', cells: { name: 'Laptop', type: 'note' }, emptySecrets: ['fields.BIOS Password'] },
    { id: 'item-3', revisionDate: 'r3', cells: { name: 'Mail', type: 'login', 'login.password': { masked: true } } },
  ],
  { toFill: { 'item-1': ['login.password'] } },
)

function routes(toFill: (init: RequestInit) => Response): Routes {
  return {
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'POST /api/to-fill': toFill,
  }
}

function cell(itemId: string, columnId: string): HTMLElement {
  const found = document.querySelector<HTMLElement>(
    `.ag-row[row-id="${CSS.escape(itemId)}"] .ag-cell[col-id="${CSS.escape(columnId)}"]`,
  )
  if (!found) {
    throw new Error(`No cell ${itemId} ${columnId}`)
  }
  return found
}

async function openMenu(itemId: string, columnId: string) {
  fireEvent.contextMenu(cell(itemId, columnId), { clientX: 10, clientY: 10 })
  return screen.findByRole('menu')
}

function marks(fetchMock: ReturnType<typeof mockFetch>): unknown[] {
  return fetchMock.mock.calls
    .filter(([path, init]) => path === '/api/to-fill' && init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init?.body)))
}

test('a listed cell offers Unmark, and the chip goes with the answer', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes(() => jsonResponse(200, { entryId: 7, toFill: {} })) })
  await screen.findByText('Code forge')
  expect(screen.getByRole('button', { name: 'To fill in (1)' })).toBeInTheDocument()

  const menu = await openMenu('item-1', 'login.password')
  expect(within(menu).queryByRole('menuitem', { name: 'Mark to fill in' })).toBeNull()
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Unmark' }))

  await waitFor(() => expect(marks(fetchMock)).toEqual([{ itemId: 'item-1', column: 'login.password', mark: false }]))
  await waitFor(() => expect(screen.queryByRole('button', { name: /To fill in/ })).toBeNull())
})

test('an empty secret cell offers Mark to fill in', async () => {
  const { fetchMock } = renderGrid(grid, {
    routes: routes(() =>
    jsonResponse(200, { entryId: 8, toFill: { 'item-1': ['login.password'], 'item-2': ['fields.BIOS Password'] } })),
  })
  await screen.findByText('Laptop')

  const menu = await openMenu('item-2', 'fields.BIOS Password')
  expect(within(menu).queryByRole('menuitem', { name: 'Unmark' })).toBeNull()
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Mark to fill in' }))

  await waitFor(() => expect(marks(fetchMock)).toEqual([{ itemId: 'item-2', column: 'fields.BIOS Password', mark: true }]))
  expect(await screen.findByRole('button', { name: 'To fill in (2)' })).toBeInTheDocument()
})

test('a refused mark says why', async () => {
  renderGrid(grid, { routes: routes(() => jsonResponse(422, { detail: 'login.password has a value already' })) })
  await screen.findByText('Code forge')

  const menu = await openMenu('item-1', 'login.password')
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Unmark' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Could not change the to-fill list: login.password has a value already')
})

test('a filled secret cell and a cell that is no secret offer neither', async () => {
  renderGrid(grid, { routes: routes(() => jsonResponse(500)) })
  await screen.findByText('Mail')

  for (const [itemId, columnId] of [
    ['item-3', 'login.password'],
    ['item-1', 'login.username'],
  ]) {
    const menu = await openMenu(itemId, columnId)
    expect(within(menu).queryByRole('menuitem', { name: 'Mark to fill in' })).toBeNull()
    expect(within(menu).queryByRole('menuitem', { name: 'Unmark' })).toBeNull()
    fireEvent.keyDown(menu, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
  }
})

test('toFillAction judges the vault, the unsaved edits and the tool note', () => {
  const [codeForge, laptop, mail] = grid.rows
  const toFill = grid.toFill ?? {}
  expect(toFillAction(password, codeForge, toFill)).toBe('unmark')
  expect(toFillAction(bios, laptop, toFill)).toBe('mark')
  expect(toFillAction(password, mail, toFill)).toBeNull()
  expect(toFillAction(username, codeForge, toFill)).toBeNull()
  // An item without the hidden field has no cell to fill in.
  expect(toFillAction(bios, codeForge, toFill)).toBeNull()
  const toolNote: GridRow = { ...laptop, toolNote: true }
  expect(toFillAction(bios, toolNote, toFill)).toBeNull()
  // An unsaved edit fills the cell already.
  const edited = {
    ...laptop,
    queued: {
      cells: new Map([
        ['fields.BIOS Password', { value: { masked: true as const }, changes: [] }],
      ]),
      trash: [],
    },
  }
  expect(toFillAction(bios, edited, toFill)).toBeNull()
})

const attachmentsColumn = gridColumn('attachments', { multi: true, readOnly: true })

// Codes has two attachments, Wiki none, and the tool note carries one to show the menu leaves it out anyway.
const attachmentsGrid: GridData = {
  columns: [grid.columns[0], attachmentsColumn, username],
  rows: [
    {
      id: 'item-1',
      revisionDate: 'r1',
      cells: { name: 'Codes', 'login.username': 'user', attachments: ['codes.txt', 'keys.pem'] },
      attachments: [
        { id: 'att-1', fileName: 'codes.txt' },
        { id: 'att-2', fileName: 'keys.pem' },
      ],
    },
    { id: 'item-2', revisionDate: 'r2', cells: { name: 'Wiki', 'login.username': 'user' } },
    {
      id: 'tool',
      revisionDate: 'r3',
      cells: { name: 'bwtidy', attachments: ['note.txt'] },
      attachments: [{ id: 'att-3', fileName: 'note.txt' }],
    },
  ],
  toolNoteId: 'tool',
}

test('an attachments cell of an item with attachments offers Rename attachments, which opens its dialog', async () => {
  renderGrid(attachmentsGrid, { routes: routes(() => jsonResponse(500)) })
  await screen.findByText('Wiki')

  const menu = await openMenu('item-1', 'attachments')
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Rename attachments' }))

  const dialog = await screen.findByRole('dialog', { name: 'Attachments of Codes' })
  expect(within(dialog).getByRole('listitem', { name: 'codes.txt' })).toBeInTheDocument()
  expect(within(dialog).getByRole('listitem', { name: 'keys.pem' })).toBeInTheDocument()
})

test('Rename attachments is not offered on another column, an item without attachments or the tool note', async () => {
  renderGrid(attachmentsGrid, { routes: routes(() => jsonResponse(500)) })
  await screen.findByText('Wiki')

  for (const [itemId, columnId] of [
    ['item-1', 'login.username'],
    ['item-2', 'attachments'],
    ['tool', 'attachments'],
  ]) {
    const menu = await openMenu(itemId, columnId)
    expect(within(menu).queryByRole('menuitem', { name: 'Rename attachments' })).toBeNull()
    fireEvent.keyDown(menu, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
  }
})

test('an edit attempt on an attachments cell points to the cell menu', async () => {
  renderGrid(attachmentsGrid, { routes: routes(() => jsonResponse(500)) })
  await screen.findByText('Wiki')

  for (const attempt of [
    () => fireEvent.doubleClick(cell('item-1', 'attachments'), { detail: 2 }),
    () => fireEvent.keyDown(cell('item-1', 'attachments'), { key: 'Enter' }),
    () => fireEvent.keyDown(cell('item-1', 'attachments'), { key: 'x' }),
  ]) {
    attempt()
    expect(await screen.findByRole('alert')).toHaveTextContent('Rename an attachment from the cell menu.')
    fireEvent.click(within(screen.getByRole('alert')).getByRole('button', { name: /close/i }))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  }
})
