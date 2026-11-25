import { createTheme } from '@mui/material'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, onTestFinished, test, vi } from 'vitest'
import { jsonResponse } from '../test-utils.tsx'
import { REVEAL_MS } from './reveal.ts'
import { noCellMarks } from './cellMarks.ts'
import { columnDef } from './columns.tsx'
import { PLAIN_IN_SECRET_CLASS, PLAIN_IN_SECRET_TOOLTIP } from './editing.ts'
import { gridAnswer, gridColumn, renderGrid } from './testSupport.tsx'
import type { GridColumn, GridData, GridRow } from '../api/grid.ts'
import type { ShownRow } from './queued.ts'
import { hasOwnGeneratorMark, OWN_GENERATOR_CLASS, OWN_GENERATOR_TOOLTIP, ownGeneratorMarkStyles } from './ownGenerator.ts'
import { TO_FILL_CELL_CLASS, TO_FILL_TOOLTIP } from './toFill.ts'
import type { DiffRow, QueueEntry } from '../api/queue.ts'
import { columnTooltip } from './labels.ts'
import { columnCounts } from './columnValues.ts'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

// jsdom lays nothing out, so the grid's viewport is 0 high and no row is in view, which a reveal
// needs. A height gives the body viewport room for the rows.
function givenGridViewport() {
  const height = vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(600)
  onTestFinished(() => height.mockRestore())
}

const grid = gridAnswer(
  [
    'name',
    'type',
    'folder',
    'favorite',
    gridColumn('collections', { multi: true }),
    gridColumn('attachments', { multi: true, readOnly: true }),
    'login.username',
    gridColumn('login.password', { secret: true }),
    gridColumn('login.uris', { multi: true }),
    'card.brand',
    gridColumn('fields.BIOS Password', { secret: true }),
  ],
  [
    {
      id: 'item-1',
      cells: {
        name: 'Code forge',
        type: 'login',
        folder: 'Work',
        favorite: true,
        attachments: ['codes.txt', 'recovery.pdf'],
        'login.username': 'user',
        'login.password': { masked: true },
        'login.uris': ['https://git.example.org', 'https://code.example.org'],
      },
    },
    {
      id: 'item-2',
      cells: { name: 'Laptop', type: 'note', collections: ['Family'], 'fields.BIOS Password': { masked: true } },
    },
    {
      id: 'item-3',
      cells: { name: 'Visa', type: 'card', 'card.brand': 'Visa' },
    },
  ],
)

function header(columnId: string): HTMLElement | null {
  return document.querySelector(`.ag-header-cell[col-id="${CSS.escape(columnId)}"]`)
}

function groupHeaders(): string[] {
  return Array.from(document.querySelectorAll('.ag-header-group-cell'), (cell) => (cell.textContent ?? '').trim())
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

function rowNames(): string[] {
  return Array.from(document.querySelectorAll('.ag-row .ag-cell[col-id="name"]'), (cell) => cell.textContent ?? '').sort()
}

async function choose(label: string, option: string) {
  fireEvent.mouseDown(screen.getByRole('combobox', { name: label }))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}

test('renders the pinned name column and the group headers', async () => {
  renderGrid(grid)
  await screen.findByText('Code forge')

  const name = header('name')
  expect(name).not.toBeNull()
  expect(name!.closest('.ag-grid-pinned-left-cells')).not.toBeNull()
  expect(name).toHaveTextContent('name')
  expect(cell('item-1', 'name').closest('.ag-grid-pinned-left-cells')).not.toBeNull()
  expect(rowNames()).toEqual(['Code forge', 'Laptop', 'Visa'])

  expect(groupHeaders()).toEqual(['Item', 'Login (login)', 'Card (card)', 'Custom fields (fields)'])
  expect(header('login.username')).toHaveTextContent(/^username$/)
  await waitFor(() => expect(header('fields.BIOS Password')).toHaveTextContent(/^BIOS Password/))
  expect(cell('item-2', 'type')).toHaveTextContent('Secure note')
  expect(cell('item-1', 'favorite')).toHaveTextContent('✓')
  const uris = cell('item-1', 'login.uris')
  await waitFor(() => expect(within(uris).getByText('https://git.example.org')).toBeInTheDocument())
  expect(within(uris).getByText('https://code.example.org')).toBeInTheDocument()
})

test('an attachments cell shows every file name', async () => {
  renderGrid(grid)
  await screen.findByText('Code forge')

  const attachments = cell('item-1', 'attachments')
  await waitFor(() => expect(within(attachments).getByText('codes.txt')).toBeInTheDocument())
  expect(within(attachments).getByText('recovery.pdf')).toBeInTheDocument()
  expect(cell('item-2', 'attachments')).toHaveTextContent('')
})

test('a secret cell shows the mask and reveals the value on click for 30 seconds', async () => {
  givenGridViewport()
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const { fetchMock } = renderGrid(grid, {
    routes: {
      'GET /api/items/item-1/secret?column=login.password': () => jsonResponse(200, { value: 'hunter2' }),
  
    },
  })
  await screen.findByText('Code forge')

  const secret = cell('item-1', 'login.password')
  await waitFor(() => expect(within(secret).getByTestId('secret-value')).toHaveTextContent('•'.repeat(8)))
  expect(screen.queryByText('hunter2')).toBeNull()

  fireEvent.click(within(secret).getByRole('button', { name: 'Reveal login.password' }))

  await waitFor(() => expect(within(secret).getByTestId('secret-value')).toHaveTextContent('hunter2'))
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/items/item-1/secret?column=login.password',
    expect.objectContaining({ method: 'GET' }),
  )

  await act(() => vi.advanceTimersByTimeAsync(REVEAL_MS))

  expect(within(secret).getByTestId('secret-value')).toHaveTextContent('•'.repeat(8))
  expect(screen.queryByText('hunter2')).toBeNull()
})

const plainGrid: GridData = {
  ...grid,
  rows: [
    ...grid.rows,
    {
      id: 'item-4',
      revisionDate: '2026-10-04T10:00:00Z',
      cells: { name: 'Router', type: 'note', 'fields.BIOS Password': 'in the open' },
    },
  ],
}

test('a text field value in a secret column shows plain with a mark, a hidden one stays masked', async () => {
  renderGrid(plainGrid)
  await screen.findByText('Router')

  const plain = cell('item-4', 'fields.BIOS Password')
  await waitFor(() => expect(plain).toHaveTextContent(/^in the open$/))
  expect(plain).toHaveClass(PLAIN_IN_SECRET_CLASS)
  expect(within(plain).queryByTestId('secret-value')).toBeNull()
  expect(within(plain).queryByRole('button', { name: 'Reveal fields.BIOS Password' })).toBeNull()

  const hidden = cell('item-2', 'fields.BIOS Password')
  expect(within(hidden).getByTestId('secret-value')).toHaveTextContent('•'.repeat(8))
  expect(within(hidden).getByRole('button', { name: 'Reveal fields.BIOS Password' })).toBeInTheDocument()
  expect(hidden).not.toHaveClass(PLAIN_IN_SECRET_CLASS)
  expect(cell('item-1', 'fields.BIOS Password')).not.toHaveClass(PLAIN_IN_SECRET_CLASS)
  expect(cell('item-1', 'login.password')).not.toHaveClass(PLAIN_IN_SECRET_CLASS)
})

test('a text field value in a secret column says an agent can read it, with the value and its format', () => {
  const column = grid.columns.find((candidate) => candidate.id === 'fields.BIOS Password')!
  const [, hidden, , plain] = plainGrid.rows
  const tooltip = columnDef(column, false, noCellMarks).tooltip as (params: unknown) => string | undefined
  expect(tooltip({ data: plain, valueFormatted: 'in the open' })).toBe(`in the open\n${PLAIN_IN_SECRET_TOOLTIP}`)
  expect(tooltip({ data: hidden, valueFormatted: '•'.repeat(8) })).toBeUndefined()

  const formats = { ...noCellMarks, formatOf: () => ({ level: 'none' as const, hover: 'off format', datatypeId: 'd' }) }
  const withFormat = columnDef(column, false, formats).tooltip as (params: unknown) => string | undefined
  expect(withFormat({ data: plain, valueFormatted: 'in the open' })).toBe(`in the open\noff format\n${PLAIN_IN_SECRET_TOOLTIP}`)
  expect(withFormat({ data: hidden, valueFormatted: '•'.repeat(8) })).toBe('off format')
})

test('a queued edit of a text field in a secret column has the mark like a written one', () => {
  const column = grid.columns.find((candidate) => candidate.id === 'fields.BIOS Password')!
  const plain = plainGrid.rows[3]
  const change = { entryId: 7, summary: 'Set a value', before: 'in the open', after: 'typed', fromUi: true, committing: false }
  const queued: ShownRow = {
    ...plain,
    queued: { cells: new Map([[column.id, { value: 'typed', changes: [change] }]]), trash: [] },
  }
  const def = columnDef(column, false, noCellMarks)
  const marked = def.cellClassRules![PLAIN_IN_SECRET_CLASS] as (params: { data: ShownRow }) => boolean
  expect(marked({ data: queued })).toBe(true)
  expect(marked({ data: plain })).toBe(true)
  expect(marked({ data: grid.rows[1] })).toBe(false)
  const renderer = def.cellRendererSelector as (params: { data: ShownRow }) => unknown
  expect(renderer({ data: queued })).toBeUndefined()
  expect(renderer({ data: plain })).toBeUndefined()
  expect(renderer({ data: grid.rows[1] })).toBeDefined()
})

test('the type filter reduces the rows', async () => {
  renderGrid(grid)
  await screen.findByText('Code forge')

  await choose('Type', 'Card')

  await waitFor(() => expect(rowNames()).toEqual(['Visa']))
  expect(screen.getByText('1 of 3 items')).toBeInTheDocument()
})

test('a group header names the prefix and its columns drop it but keep the coordinate as tooltip', async () => {
  renderGrid(grid)
  await screen.findByText('Code forge')

  expect(groupHeaders()).toContain('Card (card)')
  const brand = header('card.brand')
  expect(brand).toHaveTextContent(/^brand$/)
  // AG Grid's tooltip popup does not position in jsdom, so check the column definition.
  const column: GridColumn = { id: 'card.number', label: 'card.number', group: 'card', secret: true, multi: false, readOnly: false }
  expect(columnDef(column, false, noCellMarks)).toMatchObject({ headerName: 'number', headerTooltip: 'card.number, in no item', colId: 'card.number' })
  const custom: GridColumn = { ...column, id: 'fields.WPA key#2', label: 'fields.WPA key#2', group: 'fields' }
  expect(columnDef(custom, false, noCellMarks)).toMatchObject({ headerName: 'WPA key#2', headerTooltip: 'fields.WPA key#2, in no item' })
})

test('a header tooltip says in how many items the column has a value', () => {
  const row = (id: string, cells: GridRow['cells']): GridRow => ({ id, revisionDate: '', cells })
  const rows = [
    row('a', { 'card.number': { masked: true }, favorite: false, notes: 'x' }),
    row('b', { 'card.number': { masked: true }, favorite: false, notes: 'y' }),
    row('c', { 'card.number': { masked: true }, favorite: false, notes: '', 'card.brand': 'Visa' }),
  ]
  const counts = columnCounts(rows)
  const tooltipOf = (id: string, group: GridColumn['group'], from = counts) =>
    columnDef({ id, label: id, group, secret: false, multi: false, readOnly: false }, false, noCellMarks, false, from).headerTooltip
  // A masked secret counts as present.
  expect(tooltipOf('card.number', 'card')).toBe('card.number, in 3 items')
  expect(tooltipOf('notes', 'item')).toBe('notes, in 2 items')
  expect(tooltipOf('card.brand', 'card')).toBe('card.brand, in 1 item')
  // A false boolean counts as empty.
  expect(tooltipOf('favorite', 'item')).toBe('favorite, in no item')
  expect(tooltipOf('card.code', 'card')).toBe('card.code, in no item')
  expect(columnTooltip('name', 1)).toBe('name, in 1 item')
})

test('hide empty columns hides a column only the filtered out rows had', async () => {
  renderGrid(grid)
  await screen.findByText('Code forge')

  await choose('Type', 'Login')
  await waitFor(() => expect(rowNames()).toEqual(['Code forge']))
  expect(header('card.brand')).toBeInTheDocument()
  expect(groupHeaders()).toContain('Card (card)')

  fireEvent.click(screen.getByRole('switch', { name: 'Hide empty columns' }))

  await waitFor(() => expect(header('card.brand')).toBeNull())
  expect(groupHeaders()).not.toContain('Card (card)')
  expect(header('login.username')).toBeInTheDocument()
  expect(header('name')).toBeInTheDocument()
})

test('the column chooser hides a column', async () => {
  renderGrid(grid)
  await screen.findByText('Code forge')

  fireEvent.click(screen.getByRole('button', { name: 'Columns' }))
  fireEvent.click(await screen.findByRole('checkbox', { name: 'login.username' }))

  await waitFor(() => expect(header('login.username')).toBeNull())
  expect(screen.getByRole('checkbox', { name: 'name' })).toBeDisabled()
})

// Opens the column chooser, ticks or unticks the column and closes the chooser again.
async function toggleColumn(label: string) {
  fireEvent.click(screen.getByRole('button', { name: /^Columns/ }))
  const checkbox = await screen.findByRole('checkbox', { name: label })
  fireEvent.click(checkbox)
  fireEvent.keyDown(checkbox, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('checkbox', { name: label })).toBeNull())
}

test('a hidden type column shows as an icon after the name, with the type on hover', async () => {
  renderGrid(grid)
  await screen.findByText('Code forge')
  const name = () => cell('item-1', 'name')
  expect(within(name()).queryByRole('img')).toBeNull()

  await toggleColumn('type')

  expect(header('type')).toBeNull()
  const icon = await within(name()).findByRole('img', { name: 'Login' })
  expect(name()).toHaveTextContent(/^Code forge$/)
  expect(within(cell('item-3', 'name')).getByRole('img', { name: 'Card' })).toBeInTheDocument()
  fireEvent.mouseOver(icon)
  expect(await screen.findByRole('tooltip')).toHaveTextContent('Login')

  await toggleColumn('type')

  expect(header('type')).not.toBeNull()
  await waitFor(() => expect(within(name()).queryByRole('img')).toBeNull())
})

test('the column chooser hides the template column, and the name shows the template', async () => {
  renderGrid(grid, {
    routes: {
      'GET /api/queue?status=pending': () => jsonResponse(200, []),
      'GET /api/templates': () => jsonResponse(200, { templates: [] }),
      'GET /api/template-matches': () => jsonResponse(200, { 'item-1': { match: 'Web login', scores: [] } }),
  
    },
  })
  await screen.findByText('Code forge')
  await waitFor(() => expect(cell('item-1', 'template')).toHaveTextContent('Web login'))
  expect(cell('item-1', 'name')).toHaveTextContent(/^Code forge$/)

  await toggleColumn('template')

  expect(header('template')).toBeNull()
  expect(screen.getByRole('button', { name: 'Columns (1 hidden)' })).toBeInTheDocument()
  await waitFor(() => expect(cell('item-1', 'name')).toHaveTextContent(/^Code forgeWeb login$/))
  expect(cell('item-2', 'name')).toHaveTextContent(/^Laptop$/)

  await toggleColumn('template')

  expect(header('template')).not.toBeNull()
  await waitFor(() => expect(cell('item-1', 'name')).toHaveTextContent(/^Code forge$/))
})

test('the quick filter narrows the rows by text', async () => {
  renderGrid(grid)
  await screen.findByText('Code forge')

  fireEvent.change(screen.getByRole('searchbox', { name: 'Search' }), { target: { value: 'code.example' } })

  await waitFor(() => expect(rowNames()).toEqual(['Code forge']))
})

function pendingEntry(id: number, summary: string, diff: DiffRow[]): QueueEntry {
  return {
    id,
    createdAt: '2026-10-28T10:00:00Z',
    source: 'mcp',
    summary,
    status: 'pending',
    decidedAt: null,
    operation: {},
    diff,
    diffError: null,
    result: null,
  }
}

const pending: QueueEntry[] = [
  pendingEntry(4, 'Rename "Code forge" to "Codeharbor"', [
    { itemId: 'item-1', itemName: 'Code forge', column: 'name', before: 'Code forge', after: 'Codeharbor' },
  ]),
  pendingEntry(5, 'Rename field BIOS Password to UEFI Password', [
    { itemId: 'item-2', itemName: 'Laptop', column: 'fields.BIOS Password', before: { masked: true }, after: null },
    { itemId: 'item-2', itemName: 'Laptop', column: 'fields.UEFI Password', before: null, after: { masked: true } },
  ]),
  pendingEntry(6, 'Move "Visa" to the trash', [
    { itemId: 'item-3', itemName: 'Visa', column: 'trash', before: false, after: true },
  ]),
]

function renderWithQueue() {
  return renderGrid(grid, { routes: { 'GET /api/queue?status=pending': () => jsonResponse(200, pending) } }).fetchMock
}

test('a queued value shows in its cell with a background and a tooltip, and the cell is read-only', async () => {
  renderWithQueue()
  await screen.findByText('Codeharbor')

  const name = cell('item-1', 'name')
  expect(name).toHaveClass('bw-queued-cell')
  expect(cell('item-1', 'login.username')).not.toHaveClass('bw-queued-cell')

  fireEvent.doubleClick(name)
  expect(await screen.findByText('A queued change waits on the Queue page. Approve or reject it first.')).toBeInTheDocument()
  expect(name.querySelector('input')).toBeNull()

  // AG Grid positions the tooltip against the grid's offsetParent, which jsdom never sets.
  const offsetParent = vi.spyOn(HTMLElement.prototype, 'offsetParent', 'get').mockReturnValue(document.body)
  onTestFinished(() => offsetParent.mockRestore())
  fireEvent.mouseOver(name)
  fireEvent.mouseEnter(name)
  fireEvent.mouseMove(name)
  const tooltip = await screen.findByText('Queued in #4: Rename "Code forge" to "Codeharbor". Was: Code forge', {}, { timeout: 3000 })
  expect(tooltip.closest('.ag-tooltip')).not.toBeNull()
  fireEvent.mouseOut(name)
  fireEvent.mouseLeave(name)
  await waitFor(() => expect(screen.queryByText(/Queued in #4/)).toBeNull())
})

test('a queued rename adds its new column, empties the old cell and fades a trashed row', async () => {
  renderWithQueue()
  await screen.findByText('Codeharbor')

  await waitFor(() => expect(header('fields.UEFI Password')).toBeInTheDocument())
  const renamed = cell('item-2', 'fields.UEFI Password')
  expect(renamed).toHaveClass('bw-queued-cell')
  expect(renamed).toHaveTextContent('•'.repeat(8))
  expect(within(renamed).queryByRole('button')).toBeNull()
  expect(cell('item-2', 'fields.BIOS Password')).toHaveTextContent('')
  expect(cell('item-3', 'name').closest('.ag-row')).toHaveClass('bw-queued-trash')
  expect(cell('item-1', 'name').closest('.ag-row')).not.toHaveClass('bw-queued-trash')
})

test('the switch turns the queued changes off', async () => {
  renderWithQueue()
  await screen.findByText('Codeharbor')

  fireEvent.click(screen.getByRole('switch', { name: 'Show queued changes' }))

  await waitFor(() => expect(cell('item-1', 'name')).toHaveTextContent('Code forge'))
  expect(cell('item-1', 'name')).not.toHaveClass('bw-queued-cell')
  expect(header('fields.UEFI Password')).toBeNull()
  expect(cell('item-3', 'name').closest('.ag-row')).not.toHaveClass('bw-queued-trash')
})

test('search works on the vault values, not the queued ones', async () => {
  renderWithQueue()
  await screen.findByText('Codeharbor')

  const search = screen.getByRole('searchbox', { name: 'Search' })
  fireEvent.change(search, { target: { value: 'codeharbor' } })
  await waitFor(() => expect(rowNames()).toEqual([]))

  fireEvent.change(search, { target: { value: 'code forge' } })
  await waitFor(() => expect(rowNames()).toEqual(['Codeharbor']))
})

async function editText(itemId: string, columnId: string, value: string) {
  // AG Grid only starts an editor for a real double click, detail 2.
  fireEvent.doubleClick(cell(itemId, columnId), { detail: 2 })
  const input = await waitFor(() => {
    const found = cell(itemId, columnId).querySelector('input')
    if (!found) {
      throw new Error('No editor')
    }
    return found
  })
  // AG Grid's text editor listens to input events, not change events.
  fireEvent.input(input, { target: { value } })
  fireEvent.keyDown(input, { key: 'Enter' })
}

function rowElements(itemId: string): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>(`.ag-row[row-id="${CSS.escape(itemId)}"]`))
}

test('an edit conflict marks the rows and changed cells until the next successful save', async () => {
  const changed: GridData = {
    columns: grid.columns,
    rows: [
      {
        ...grid.rows[0],
        revisionDate: '2026-10-05T10:00:00Z',
        cells: { ...grid.rows[0].cells, folder: 'Home', 'login.username': 'user.name' },
      },
      grid.rows[2],
    ],
  }
  let loads = 0
  const posts = [
    () =>
      jsonResponse(409, {
        detail: 'Changed elsewhere',
        conflicts: [
          { itemId: 'item-1', expected: '2026-10-01T10:00:00Z', actual: '2026-10-05T10:00:00Z' },
          { itemId: 'item-2', expected: '2026-10-02T10:00:00Z', actual: null },
        ],
      }),
    () => jsonResponse(201, { ids: [1] }),
  ]
  renderGrid(() => (loads++ === 0 ? grid : changed), {
    routes: { 'POST /api/queue': () => posts.shift()!() },
  })
  await screen.findByText('Code forge')
  expect(document.querySelector('.bw-conflict-row')).toBeNull()

  await editText('item-1', 'login.username', 'k')

  await waitFor(() => expect(cell('item-1', 'login.username')).toHaveClass('bw-conflict-cell'))
  expect(rowElements('item-1').length).toBeGreaterThan(0)
  for (const element of rowElements('item-1')) {
    expect(element).toHaveClass('bw-conflict-row')
  }
  expect(cell('item-1', 'folder')).toHaveClass('bw-conflict-cell')
  expect(cell('item-1', 'name')).not.toHaveClass('bw-conflict-cell')
  expect(cell('item-1', 'login.password')).not.toHaveClass('bw-conflict-cell')
  await waitFor(() => expect(rowElements('item-2')).toEqual([]))
  for (const element of rowElements('item-3')) {
    expect(element).not.toHaveClass('bw-conflict-row')
  }

  await editText('item-3', 'card.brand', 'Mastercard')

  await waitFor(() => expect(document.querySelector('.bw-conflict-row')).toBeNull())
  expect(document.querySelector('.bw-conflict-cell')).toBeNull()
})

const withToolNote: GridData = {
  ...grid,
  toolNoteId: 'tool',
  rows: [...grid.rows, { id: 'tool', revisionDate: '2026-10-28T10:00:00Z', cells: { name: 'bwtidyK', type: 'note' } }],
}

// Without expected the answer has no toolNoteExpectedName, which reads as bwtidy.
function templatesDoc(name: string, expected?: string) {
  return {
    templates: [],
    revisionDate: '2026-10-28T10:00:00Z',
    toolNoteId: 'tool',
    toolNoteName: name,
    ...(expected === undefined ? {} : { toolNoteExpectedName: expected }),
  }
}

test('the tool note row is grey and read-only', async () => {
  renderGrid(withToolNote)
  await screen.findByText('bwtidyK')

  const row = (id: string) => document.querySelector<HTMLElement>(`.ag-row[row-id="${id}"]`)!
  await waitFor(() => expect(row('tool').style.backgroundColor).not.toBe(''))
  expect(row('item-1').style.backgroundColor).toBe('')

  fireEvent.doubleClick(cell('tool', 'name'), { detail: 2 })
  expect(await screen.findByRole('alert')).toHaveTextContent("bwtidy's own note")
  expect(cell('tool', 'name').querySelector('input')).toBeNull()
})

test.each([
  [true, false],
  [false, true],
])('with hideToolNote %s the tool note row is rendered: %s', async (hideToolNote, rendered) => {
  renderGrid(withToolNote, {
    routes: { 'GET /api/settings/display': () => jsonResponse(200, { dateFormat: 'system', hideToolNote }) },
  })
  await screen.findByText('Laptop')
  await waitFor(() => expect(document.querySelector('.ag-row[row-id="tool"]') !== null).toBe(rendered))
  expect(screen.queryByText('bwtidyK') !== null).toBe(rendered)
  // The count follows the rows shown.
  const total = withToolNote.rows.length - (hideToolNote ? 1 : 0)
  expect(screen.getByText(`${total} items`)).toBeInTheDocument()
})

test('a renamed tool note shows a warning, and Rename it back renames it', async () => {
  let name = 'bwtidyK'
  const { fetchMock } = renderGrid(withToolNote, {
    routes: {
      'GET /api/templates': () => jsonResponse(200, templatesDoc(name)),
      'POST /api/tool-note/rename-back': () => {
        name = 'bwtidy'
        return jsonResponse(200, { toolNoteName: 'bwtidy', revisionDate: '2026-10-28T11:00:00Z', backup: 'b.zip.age' })
      },
    },
  })

  expect(await screen.findByText('bwtidy\'s note is named "bwtidyK" now.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Keep this name' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Rename it back' }))

  expect(await screen.findByText('bwtidy\'s note is named "bwtidy" again. Backup: b.zip.age')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByText(/is named "bwtidyK" now/)).toBeNull())
  const calls = fetchMock.mock.calls.filter(([path]) => path === '/api/tool-note/rename-back')
  expect(calls).toHaveLength(1)
  expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ revisionDate: '2026-10-28T10:00:00Z' })
})

test('Keep this name makes the name the note has now its expected name', async () => {
  let expected = 'bwtidy'
  const { fetchMock } = renderGrid(withToolNote, {
    routes: {
      'GET /api/templates': () => jsonResponse(200, templatesDoc('bwtidyK', expected)),
      'POST /api/tool-note/keep-name': () => {
        expected = 'bwtidyK'
        const answer = { toolNoteName: 'bwtidyK', toolNoteExpectedName: 'bwtidyK', revisionDate: '2026-10-28T11:00:00Z' }
        return jsonResponse(200, { ...answer, backup: 'b.zip.age' })
      },
    },
  })

  expect(await screen.findByText('bwtidy\'s note is named "bwtidyK" now.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Keep this name' }))

  expect(await screen.findByText('bwtidy\'s note keeps the name "bwtidyK". Backup: b.zip.age')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByText(/is named "bwtidyK" now/)).toBeNull())
  const calls = fetchMock.mock.calls.filter(([path]) => path === '/api/tool-note/keep-name')
  expect(calls).toHaveLength(1)
  expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ name: 'bwtidyK' })
  expect(fetchMock.mock.calls.filter(([path]) => path === '/api/tool-note/rename-back')).toHaveLength(0)
})

test('no warning while the tool note has the name it kept', async () => {
  renderGrid(withToolNote, { routes: { 'GET /api/templates': () => jsonResponse(200, templatesDoc('bwtidyK', 'bwtidyK')) } })
  await screen.findByText('bwtidyK')

  await waitFor(() => expect(screen.queryByRole('button', { name: 'Keep this name' })).toBeNull())
  expect(screen.queryByRole('button', { name: 'Rename it back' })).toBeNull()
  expect(screen.queryByText(/is named/)).toBeNull()
})

test('a note renamed away from the name it kept shows the warning, and Rename it back names it so again', async () => {
  let name = 'Other'
  renderGrid(withToolNote, {
    routes: {
      'GET /api/templates': () => jsonResponse(200, templatesDoc(name, 'bwtidyK')),
      'POST /api/tool-note/rename-back': () => {
        name = 'bwtidyK'
        const answer = { toolNoteName: 'bwtidyK', toolNoteExpectedName: 'bwtidyK', revisionDate: '2026-10-28T11:00:00Z' }
        return jsonResponse(200, { ...answer, backup: 'b.zip.age' })
      },
    },
  })

  expect(await screen.findByText('bwtidy\'s note is named "Other" now.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Rename it back' }))

  expect(await screen.findByText('bwtidy\'s note is named "bwtidyK" again. Backup: b.zip.age')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByText(/is named "Other" now/)).toBeNull())
})

test('no warning while the tool note is named bwtidy', async () => {
  renderGrid(withToolNote, { routes: { 'GET /api/templates': () => jsonResponse(200, templatesDoc('bwtidy')) } })
  await screen.findByText('bwtidyK')

  await waitFor(() => expect(screen.queryByRole('button', { name: 'Rename it back' })).toBeNull())
  expect(screen.queryByText(/is named/)).toBeNull()
})

test('a double click on a column edge autosizes the column', async () => {
  renderGrid(grid)
  await screen.findByText('Code forge')
  const cell = header('folder') as HTMLElement
  const before = cell.style.width
  const handle = cell.querySelector('.ag-header-cell-resize') as HTMLElement
  fireEvent.doubleClick(handle)
  await waitFor(() => expect((header('folder') as HTMLElement).style.width).not.toBe(before))
})

test('search gives the matching cells a highlight and leaves the others alone', async () => {
  renderGrid(grid)
  await screen.findByText('Code forge')
  expect(document.querySelector('.bw-search-hit')).toBeNull()

  fireEvent.change(screen.getByRole('searchbox', { name: 'Search' }), { target: { value: 'code.example' } })

  await waitFor(() => expect(rowNames()).toEqual(['Code forge']))
  await waitFor(() => expect(cell('item-1', 'login.uris')).toHaveClass('bw-search-hit'))
  expect(cell('item-1', 'name')).not.toHaveClass('bw-search-hit')
  expect(cell('item-1', 'login.username')).not.toHaveClass('bw-search-hit')
  expect(cell('item-1', 'login.password')).not.toHaveClass('bw-search-hit')

  fireEvent.change(screen.getByRole('searchbox', { name: 'Search' }), { target: { value: 'user forge' } })

  await waitFor(() => expect(cell('item-1', 'name')).toHaveClass('bw-search-hit'))
  expect(cell('item-1', 'login.username')).toHaveClass('bw-search-hit')
  expect(cell('item-1', 'login.uris')).not.toHaveClass('bw-search-hit')
  expect(cell('item-1', 'login.password')).not.toHaveClass('bw-search-hit')

  fireEvent.change(screen.getByRole('searchbox', { name: 'Search' }), { target: { value: '' } })

  await waitFor(() => expect(cell('item-1', 'name')).not.toHaveClass('bw-search-hit'))
})

test('a cell with an unsaved UI edit is editable from the queued value, a proposal cell is not', async () => {
  const entries = [
    { ...pendingEntry(7, 'Set login.username of "Code forge"', [
      { itemId: 'item-1', itemName: 'Code forge', column: 'login.username', before: 'user', after: 'user.l' },
    ]), source: 'ui' },
    pending[0],
  ]
  const { fetchMock } = renderGrid(grid, {
    routes: {
      'GET /api/queue?status=pending': () => jsonResponse(200, entries),
      'POST /api/queue': () => jsonResponse(201, { ids: [8] }),
  
    },
  })
  await screen.findByText('user.l')
  expect(cell('item-1', 'login.username')).toHaveClass('bw-queued-cell')

  fireEvent.doubleClick(cell('item-1', 'login.username'), { detail: 2 })
  const input = await waitFor(() => {
    const found = cell('item-1', 'login.username').querySelector('input')
    if (!found) {
      throw new Error('No editor')
    }
    return found
  })
  expect(input).toHaveValue('user.l')
  fireEvent.input(input, { target: { value: 'user.name' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  await waitFor(() =>
    expect(fetchMock.mock.calls.filter(([path, init]) => path === '/api/queue' && init?.method === 'POST')).toHaveLength(1),
  )

  fireEvent.doubleClick(cell('item-1', 'name'), { detail: 2 })
  expect(await screen.findByText('A queued change waits on the Queue page. Approve or reject it first.')).toBeInTheDocument()
  expect(cell('item-1', 'name').querySelector('input')).toBeNull()
})

test('hovering a near miss in the template column lists what the item lacks', async () => {
  const computer = {
    name: 'Computer',
    type: 1,
    coordinates: [
      { kind: 'path', path: 'name' },
      { kind: 'field', name: 'BIOS Password', fieldType: 1 },
      { kind: 'field', name: 'disk encryption', fieldType: 1 },
    ],
  }
  renderGrid(grid, {
    routes: {
      'GET /api/queue?status=pending': () => jsonResponse(200, []),
      'GET /api/templates': () => jsonResponse(200, { templates: [computer] }),
      'GET /api/template-matches': () =>
        jsonResponse(200, {
          'item-2': {
            match: null,
            scores: [
              { template: 'Computer', score: 2 / 4, typeMatches: false, filled: 2, total: 4, missing: ['fields.disk encryption'] },
            ],
          },
        }),
  
    },
  })
  await screen.findByText('Laptop')
  const template = cell('item-2', 'template')
  await waitFor(() => expect(template).toHaveTextContent('Computer 2/4'))

  // AG Grid positions the tooltip against the grid's offsetParent, which jsdom never sets.
  const offsetParent = vi.spyOn(HTMLElement.prototype, 'offsetParent', 'get').mockReturnValue(document.body)
  onTestFinished(() => offsetParent.mockRestore())
  fireEvent.mouseOver(template)
  fireEvent.mouseEnter(template)
  fireEvent.mouseMove(template)
  const tooltip = await screen.findByText(/^Missing for Computer:/, {}, { timeout: 3000 })
  expect(tooltip.closest('.ag-tooltip')).not.toBeNull()
  expect(tooltip.textContent).toBe('Missing for Computer:\n\u2022 Type: note, the template wants login\n\u2022 disk encryption')
})

test("a note's login.username cell is grey, says why on hover and opens no editor", async () => {
  renderGrid(grid, { routes: { 'GET /api/queue?status=pending': () => jsonResponse(200, []) } })
  await screen.findByText('Laptop')

  const username = cell('item-2', 'login.username')
  expect(username).toHaveClass('bw-unavailable-cell')
  expect(cell('item-1', 'login.username')).not.toHaveClass('bw-unavailable-cell')
  expect(cell('item-2', 'name')).not.toHaveClass('bw-unavailable-cell')

  // AG Grid positions the tooltip against the grid's offsetParent, which jsdom never sets.
  const offsetParent = vi.spyOn(HTMLElement.prototype, 'offsetParent', 'get').mockReturnValue(document.body)
  onTestFinished(() => offsetParent.mockRestore())
  fireEvent.mouseOver(username)
  fireEvent.mouseEnter(username)
  fireEvent.mouseMove(username)
  const tooltip = await screen.findByText('Only login items have login.username.', {}, { timeout: 3000 })
  expect(tooltip.closest('.ag-tooltip')).not.toBeNull()
  fireEvent.mouseOut(username)
  fireEvent.mouseLeave(username)

  fireEvent.doubleClick(username, { detail: 2 })
  fireEvent.keyDown(username, { key: 'Enter' })
  fireEvent.doubleClick(cell('item-2', 'login.uris'), { detail: 2 })
  await new Promise((resolve) => setTimeout(resolve, 50))
  expect(username.querySelector('input')).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.queryByRole('alert')).toBeNull()
})

test('New item in the toolbar opens the dialog with the stored templates', async () => {
  renderGrid(grid, {
    routes: {
      'GET /api/templates': () =>
        jsonResponse(200, { templates: [{ name: 'Computer', type: 2, coordinates: [] }], revisionDate: 'r1' }),
  
    },
  })
  await screen.findByText('Code forge')

  fireEvent.click(screen.getByRole('button', { name: 'New item' }))
  const dialog = await screen.findByRole('dialog', { name: 'New item' })
  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Start from' }))
  expect(await screen.findByRole('option', { name: 'Computer (Secure note)' })).toBeInTheDocument()
  fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New item' })).toBeNull())
})

// item-1 lacks its password and item-2 its BIOS password, both on the to-fill list. item-3 is not.
const toFillGrid: GridData = {
  ...grid,
  rows: grid.rows.map((row) => {
    const cells = { ...row.cells }
    if (row.id === 'item-1') {
      delete cells['login.password']
      return { ...row, cells, emptySecrets: ['login.password'] }
    }
    if (row.id === 'item-2') {
      delete cells['fields.BIOS Password']
      return { ...row, cells, emptySecrets: ['fields.BIOS Password'] }
    }
    return row
  }),
  toFill: { 'item-1': ['login.password'], 'item-2': ['fields.BIOS Password'] },
}

test('the to-fill chip counts the listed cells and filters the grid to their items', async () => {
  renderGrid(toFillGrid)
  await screen.findByText('Code forge')

  const chip = screen.getByRole('button', { name: 'To fill in (2)' })
  expect(chip).toHaveAttribute('aria-pressed', 'false')
  fireEvent.click(chip)
  await waitFor(() => expect(rowNames()).toEqual(['Code forge', 'Laptop']))
  expect(screen.getByText('2 of 3 items')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'To fill in (2)' })).toHaveAttribute('aria-pressed', 'true')

  // A second click clears the filter, like choosing All in the other filters.
  fireEvent.click(screen.getByRole('button', { name: 'To fill in (2)' }))
  await waitFor(() => expect(rowNames()).toEqual(['Code forge', 'Laptop', 'Visa']))
})

test('every listed cell is marked and no other', async () => {
  renderGrid(toFillGrid)
  await screen.findByText('Code forge')

  await waitFor(() => expect(cell('item-1', 'login.password')).toHaveClass(TO_FILL_CELL_CLASS))
  expect(cell('item-2', 'fields.BIOS Password')).toHaveClass(TO_FILL_CELL_CLASS)
  expect(cell('item-1', 'login.username')).not.toHaveClass(TO_FILL_CELL_CLASS)
  expect(cell('item-3', 'login.password')).not.toHaveClass(TO_FILL_CELL_CLASS)
})

test('a listed cell says how to fill it in', () => {
  const column = grid.columns.find((candidate) => candidate.id === 'login.password')!
  const row = toFillGrid.rows[0]
  const listed = { ...noCellMarks, isToFillCell: (itemId: string, columnId: string) => itemId === row.id && columnId === column.id }
  const tooltip = columnDef(column, false, listed).tooltip as (params: unknown) => string | undefined
  expect(tooltip({ data: row, valueFormatted: '' })).toBe(TO_FILL_TOOLTIP)
  expect(tooltip({ data: { ...row, id: 'item-9' }, valueFormatted: '' })).toBeUndefined()
})

test('hide empty columns keeps a column with a listed cell', async () => {
  const rows = toFillGrid.rows.map((row) => {
    const cells = { ...row.cells }
    delete cells['login.password']
    return { ...row, cells }
  })
  renderGrid({ ...toFillGrid, rows })
  await screen.findByText('Code forge')

  fireEvent.click(screen.getByRole('button', { name: 'To fill in (2)' }))
  await waitFor(() => expect(rowNames()).toEqual(['Code forge', 'Laptop']))
  fireEvent.click(screen.getByRole('switch', { name: 'Hide empty columns' }))

  await waitFor(() => expect(header('card.brand')).toBeNull())
  expect(header('login.password')).toBeInTheDocument()
})

test('there is no chip while the list is empty', async () => {
  renderGrid({ ...grid, toFill: {} })
  await screen.findByText('Code forge')

  expect(screen.queryByRole('button', { name: /To fill in/ })).toBeNull()
})

const ownGrid: GridData = {
  ...grid,
  rows: [
    { ...grid.rows[0], ownGenerator: true },
    { ...grid.rows[1], ownGenerator: true },
    // A text value in a secret column has no generator to set, whatever the item has
    { ...plainGrid.rows[3], ownGenerator: true },
    grid.rows[2],
  ],
}

test('the cells with generator settings of an item with its own setting have the mark, no other cell', async () => {
  renderGrid(ownGrid)
  await screen.findByText('Router')

  expect(cell('item-1', 'login.password')).toHaveClass(OWN_GENERATOR_CLASS)
  expect(cell('item-2', 'fields.BIOS Password')).toHaveClass(OWN_GENERATOR_CLASS)
  for (const [itemId, columnId] of [
    ['item-1', 'name'],
    ['item-1', 'login.username'],
    ['item-1', 'login.uris'],
    ['item-1', 'fields.BIOS Password'],
    ['item-2', 'name'],
    ['item-4', 'fields.BIOS Password'],
    ['item-4', 'name'],
    ['item-3', 'name'],
    ['item-3', 'card.brand'],
    ['item-3', 'login.password'],
  ]) {
    expect(cell(itemId, columnId)).not.toHaveClass(OWN_GENERATOR_CLASS)
  }
})

test('the mark says Own generator setting, added to what the cell says already', () => {
  const password = grid.columns.find((candidate) => candidate.id === 'login.password')!
  const username = grid.columns.find((candidate) => candidate.id === 'login.username')!
  const [own, , plain] = ownGrid.rows as ShownRow[]
  const tooltip = columnDef(password, false, noCellMarks).tooltip as (params: unknown) => string | undefined
  expect(tooltip({ data: own, valueFormatted: '•'.repeat(8) })).toBe(OWN_GENERATOR_TOOLTIP)
  expect(tooltip({ data: grid.rows[0], valueFormatted: '•'.repeat(8) })).toBeUndefined()
  const userTooltip = columnDef(username, false, noCellMarks).tooltip as (params: unknown) => string | undefined
  expect(userTooltip({ data: own, valueFormatted: 'user' })).toBe('user')

  const field = grid.columns.find((candidate) => candidate.id === 'fields.BIOS Password')!
  const fieldTooltip = columnDef(field, false, noCellMarks).tooltip as (params: unknown) => string | undefined
  expect(fieldTooltip({ data: plain, valueFormatted: 'in the open' })).toBe(`in the open\n${PLAIN_IN_SECRET_TOOLTIP}`)

  const toFill = { ...noCellMarks, isToFillCell: () => true }
  const listed = columnDef(password, false, toFill).tooltip as (params: unknown) => string | undefined
  expect(listed({ data: own, valueFormatted: '' })).toBe(`${TO_FILL_TOOLTIP}\n${OWN_GENERATOR_TOOLTIP}`)
})

test('the mark takes the secondary text color of the color mode', () => {
  const colors = (mode: 'light' | 'dark') => {
    const theme = createTheme({ palette: { mode } })
    const style = ownGeneratorMarkStyles(theme)[`& .${OWN_GENERATOR_CLASS}`]
    return { style, color: theme.palette.text.secondary }
  }
  const light = colors('light')
  const dark = colors('dark')
  expect(light.color).not.toBe(dark.color)
  for (const { style, color } of [light, dark]) {
    expect(decodeURIComponent(style.backgroundImage)).toContain(`fill="${color}"`)
  }
  expect(light.style.backgroundImage).not.toBe(dark.style.backgroundImage)
})

test('an empty secret the item has gets the mark too, a column the item has no secret in does not', () => {
  const password = grid.columns.find((candidate) => candidate.id === 'login.password')!
  const field = grid.columns.find((candidate) => candidate.id === 'fields.BIOS Password')!
  const empty: ShownRow = {
    id: 'item-5',
    revisionDate: '2026-10-05T10:00:00Z',
    cells: { name: 'Empty', type: 'login' },
    emptySecrets: ['login.password'],
    ownGenerator: true,
  }
  expect(hasOwnGeneratorMark(password, empty)).toBe(true)
  expect(hasOwnGeneratorMark(field, empty)).toBe(false)
  expect(hasOwnGeneratorMark(password, { ...empty, ownGenerator: undefined })).toBe(false)
})

test('the own generator chip counts the items with their own setting and filters the grid to them', async () => {
  renderGrid(ownGrid)
  await screen.findByText('Router')

  const chip = screen.getByRole('button', { name: 'Own generator settings (3)' })
  expect(chip).toHaveAttribute('aria-pressed', 'false')
  fireEvent.click(chip)
  await waitFor(() => expect(rowNames()).toEqual(['Code forge', 'Laptop', 'Router']))
  expect(screen.getByText('3 of 4 items')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Own generator settings (3)' })).toHaveAttribute('aria-pressed', 'true')

  // A second click clears the filter
  fireEvent.click(screen.getByRole('button', { name: 'Own generator settings (3)' }))
  await waitFor(() => expect(rowNames()).toEqual(['Code forge', 'Laptop', 'Router', 'Visa']))
})

test('the own generator filter works together with the other filters and its delete icon clears it', async () => {
  renderGrid(ownGrid)
  await screen.findByText('Router')

  fireEvent.click(screen.getByRole('button', { name: 'Own generator settings (3)' }))
  await waitFor(() => expect(rowNames()).toEqual(['Code forge', 'Laptop', 'Router']))
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search' }), { target: { value: 'laptop' } })
  await waitFor(() => expect(rowNames()).toEqual(['Laptop']))
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search' }), { target: { value: 'visa' } })
  await waitFor(() => expect(rowNames()).toEqual([]))
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search' }), { target: { value: '' } })

  const chip = screen.getByRole('button', { name: 'Own generator settings (3)' })
  fireEvent.click(within(chip).getByTestId('CancelIcon'))
  await waitFor(() => expect(rowNames()).toEqual(['Code forge', 'Laptop', 'Router', 'Visa']))
})

test('the own generator chip works with the to-fill chip', async () => {
  const both: GridData = {
    ...toFillGrid,
    rows: toFillGrid.rows.map((row) => (row.id === 'item-2' ? row : { ...row, ownGenerator: true })),
  }
  renderGrid(both)
  await screen.findByText('Code forge')

  fireEvent.click(screen.getByRole('button', { name: 'To fill in (2)' }))
  await waitFor(() => expect(rowNames()).toEqual(['Code forge', 'Laptop']))
  fireEvent.click(screen.getByRole('button', { name: 'Own generator settings (2)' }))
  await waitFor(() => expect(rowNames()).toEqual(['Code forge']))
})

test('there is no own generator chip while no item has its own setting', async () => {
  renderGrid(grid)
  await screen.findByText('Code forge')

  expect(screen.queryByRole('button', { name: /Own generator settings/ })).toBeNull()
})

test('the own generator chip goes with the last own setting, and the filter with it', async () => {
  let current: GridData = ownGrid
  const { queryClient } = renderGrid(() => current)
  await screen.findByText('Router')

  fireEvent.click(screen.getByRole('button', { name: 'Own generator settings (3)' }))
  await waitFor(() => expect(rowNames()).toEqual(['Code forge', 'Laptop', 'Router']))
  current = { ...ownGrid, rows: ownGrid.rows.map((row) => ({ ...row, ownGenerator: undefined })) }
  await queryClient.invalidateQueries()

  await waitFor(() => expect(screen.queryByRole('button', { name: /Own generator settings/ })).toBeNull())
  expect(rowNames()).toEqual(['Code forge', 'Laptop', 'Router', 'Visa'])
})

// fields.vlan is on Laptop alone, like fields.BIOS Password, and Code forge uses no custom field.
const rareGrid: GridData = {
  ...grid,
  columns: [...grid.columns, { id: 'fields.vlan', label: 'fields.vlan', group: 'fields', secret: false, multi: false, readOnly: false }],
  rows: grid.rows.map((row) => (row.id === 'item-2' ? { ...row, cells: { ...row.cells, 'fields.vlan': '20' } } : row)),
}

function displayWith(rareFieldsMax: number) {
  return () => jsonResponse(200, { dateFormat: 'system', hideToolNote: false, rareFieldsMax })
}

const rareSwitch = () => screen.getByRole('switch', { name: 'Hide rare field columns' })

test('Rare fields is off at 0, and setting N shows the rare fields as JSON and hides their columns', async () => {
  givenGridViewport()
  vi.useFakeTimers({ shouldAdvanceTime: true })
  let saved = 0
  const { fetchMock } = renderGrid(rareGrid, {
    routes: {
      'GET /api/settings/display': () => displayWith(saved)(),
      'PUT /api/settings/display': (init) => {
        saved = (JSON.parse(String(init.body)) as { rareFieldsMax: number }).rareFieldsMax
        return displayWith(saved)()
      },
      'GET /api/items/item-2/secret?column=fields.BIOS%20Password': () => jsonResponse(200, { value: 's3cret' }),
    },
  })
  await screen.findByText('Code forge')

  await waitFor(() => expect(header('rareFields')).toHaveTextContent(/^Rare fields \(off\)/))
  expect(rareSwitch()).toBeDisabled()
  expect(rareSwitch()).not.toBeChecked()
  expect(cell('item-2', 'rareFields')).toHaveTextContent(/^$/)

  fireEvent.click(await screen.findByRole('button', { name: 'Actions for rareFields' }))
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Set N' }))
  const dialog = await screen.findByRole('dialog', { name: 'Rare fields' })
  expect(dialog).toHaveTextContent('0 turns Rare fields off. A field goes in when at most N items use it.')
  const input = within(dialog).getByRole('spinbutton', { name: 'N' })
  fireEvent.change(input, { target: { value: '100' } })
  expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled()
  fireEvent.change(input, { target: { value: '2' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(header('rareFields')).toHaveTextContent(/^Rare fields ≤ 2/))
  const puts = fetchMock.mock.calls.filter(([path, init]) => path === '/api/settings/display' && init?.method === 'PUT')
  expect(puts.map(([, init]) => JSON.parse(String(init?.body)))).toEqual([{ rareFieldsMax: 2 }])
  // Turned on by itself, so the columns that went into Rare fields hide.
  await waitFor(() => expect(rareSwitch()).toBeChecked())
  expect(rareSwitch()).toBeEnabled()
  await waitFor(() => expect(header('fields.vlan')).toBeNull())
  expect(header('fields.BIOS Password')).toBeNull()
  expect(header('card.brand')).toBeNull()
  expect(header('name')).toBeInTheDocument()
  expect(header('folder')).toBeInTheDocument()

  const rare = cell('item-2', 'rareFields')
  await waitFor(() => expect(rare).toHaveTextContent('{ "BIOS Password": , "vlan": "20" }'))
  expect(screen.queryByText(/s3cret/)).toBeNull()
  fireEvent.click(within(rare).getByRole('button', { name: 'Reveal fields.BIOS Password' }))
  await waitFor(() => expect(rare).toHaveTextContent('{ "BIOS Password": "s3cret", "vlan": "20" }'))
  // Hidden again after 30 seconds, like a masked cell.
  await act(() => vi.advanceTimersByTimeAsync(REVEAL_MS))
  await waitFor(() => expect(rare).toHaveTextContent('{ "BIOS Password": , "vlan": "20" }'))
  expect(cell('item-3', 'rareFields')).toHaveTextContent('{ "brand": "Visa" }')

  fireEvent.click(rareSwitch())
  await waitFor(() => expect(header('fields.vlan')).toBeInTheDocument())
  expect(header('card.brand')).toBeInTheDocument()
})

test('Hide rare field columns is on at load when N is above 0', async () => {
  renderGrid(rareGrid, { routes: { 'GET /api/settings/display': displayWith(1) } })
  await screen.findByText('Code forge')

  await waitFor(() => expect(rareSwitch()).toBeChecked())
  expect(header('rareFields')).toHaveTextContent(/^Rare fields ≤ 1/)
  await waitFor(() => expect(header('fields.vlan')).toBeNull())
  // The column chooser still lists a hidden rare column.
  fireEvent.click(screen.getByRole('button', { name: /^Columns/ }))
  expect(await screen.findByRole('checkbox', { name: 'fields.vlan' })).toBeInTheDocument()
})

test('a Rare fields cell is read-only and its menu has the Row group only', async () => {
  renderGrid(rareGrid, { routes: { 'GET /api/settings/display': displayWith(2) } })
  await screen.findByText('Code forge')
  await waitFor(() => expect(cell('item-2', 'rareFields')).toHaveTextContent('"vlan": "20"'))

  fireEvent.doubleClick(cell('item-2', 'rareFields'), { detail: 2 })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(cell('item-2', 'rareFields').querySelector('input')).toBeNull()
  fireEvent.contextMenu(cell('item-2', 'rareFields'))
  const menu = await screen.findByRole('menu')
  expect(within(menu).queryByRole('menuitem', { name: 'Edit value' })).toBeNull()
  expect(within(menu).getByRole('menuitem', { name: 'Add field' })).toBeInTheDocument()
})

test('the search tints a Rare fields cell whose shown value matches', async () => {
  renderGrid(rareGrid, { routes: { 'GET /api/settings/display': displayWith(2) } })
  await screen.findByText('Code forge')
  await waitFor(() => expect(cell('item-3', 'rareFields')).toHaveTextContent('"brand": "Visa"'))

  fireEvent.change(screen.getByRole('searchbox', { name: 'Search' }), { target: { value: 'visa' } })
  await waitFor(() => expect(cell('item-3', 'rareFields')).toHaveClass('bw-search-hit'))
})
