import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { jsonResponse, type mockFetch, textSecurity } from '../test-utils.tsx'
import {
  canGenerate,
  editBlocker,
  LINKED_FIELD_BLOCKER,
  organizationCollections,
  plainInSecretColumn,
  TOOL_NOTE_BLOCKER,
  unavailableReason,
} from './editing.ts'
import { BOOLEAN_FIELD_NOTE } from './dialogs/BooleanFieldDialog.tsx'
import { TEXT_FIELD_WARNING } from './dialogs/SecretDialog.tsx'
import { gridAnswer, gridColumn, renderGrid, type Routes } from './testSupport.tsx'
import type { GridColumn, GridData, GridRow } from '../api/grid.ts'

afterEach(() => {
  vi.unstubAllGlobals()
})

const grid = gridAnswer(
  [
    'name',
    'type',
    'folder',
    'organization',
    gridColumn('collections', { multi: true }),
    gridColumn('revisionDate', { readOnly: true }),
    'login.username',
    gridColumn('login.password', { secret: true }),
    gridColumn('login.uris', { multi: true }),
    gridColumn('fields.BIOS Password', { secret: true }),
  ],
  [
    {
      id: 'item-1',
      cells: {
        name: 'Code forge',
        type: 'login',
        folder: 'Work',
        'login.username': 'user',
        'login.password': { masked: true },
        'login.uris': ['https://git.example.org'],
      },
    },
    {
      id: 'item-2',
      cells: {
        name: 'Laptop',
        type: 'note',
        organization: 'Family',
        collections: ['Shared'],
        'fields.BIOS Password': { masked: true },
      },
    },
  ],
)

const ok = () => jsonResponse(201, { ids: [1] })

function routes(post: (init: RequestInit) => Response = ok, extra: Routes = {}): Routes {
  return {
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'POST /api/queue': post,
    ...extra,
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

function posted(fetchMock: ReturnType<typeof mockFetch>): unknown[] {
  return fetchMock.mock.calls
    .filter(([path, init]) => path === '/api/queue' && init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init?.body)))
}

function gridLoads(fetchMock: ReturnType<typeof mockFetch>): number {
  return fetchMock.mock.calls.filter(([path]) => path === '/api/grid').length
}

function queueLoads(fetchMock: ReturnType<typeof mockFetch>): number {
  return fetchMock.mock.calls.filter(([path]) => path === '/api/queue?status=pending').length
}

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

async function openMenu(itemId: string, columnId: string) {
  fireEvent.contextMenu(cell(itemId, columnId), { clientX: 10, clientY: 10 })
  return screen.findByRole('menu')
}

test('editing a text cell queues setLeaf with the row revisionDate and reloads the queue, not the grid', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  await editText('item-1', 'login.username', 'user.name')

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [
      {
        kind: 'setLeaf',
        itemId: 'item-1',
        revisionDate: '2026-10-01T10:00:00Z',
        column: 'login.username',
        value: 'user.name',
      },
    ],
  })
  await waitFor(() => expect(queueLoads(fetchMock)).toBeGreaterThan(1))
  expect(gridLoads(fetchMock)).toBe(1)
  expect(screen.queryByRole('alert')).toBeNull()
})

test('a 409 names the items that changed elsewhere and reloads the grid', async () => {
  const { fetchMock } = renderGrid(grid, {
    routes: routes(() =>
      jsonResponse(409, {
        detail: 'Changed elsewhere',
        conflicts: [{ itemId: 'item-1', expected: '2026-10-01T10:00:00Z', actual: '2026-10-05T10:00:00Z' }],
      }),
    ),
  })
  await screen.findByText('Code forge')

  await editText('item-1', 'login.username', 'user.name')

  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent(/changed elsewhere/)
  expect(within(alert).getByText('Code forge')).toBeInTheDocument()
  await waitFor(() => expect(gridLoads(fetchMock)).toBe(2))
})

test('a 422 shows the detail', async () => {
  renderGrid(grid, { routes: routes(() => jsonResponse(422, { detail: 'A secret value cannot move into notes.' })) })
  await screen.findByText('Code forge')

  await editText('item-1', 'login.username', 'user.name')

  expect(await screen.findByRole('alert')).toHaveTextContent('A secret value cannot move into notes.')
})

test('a 422 for a single operation leaves out which operation it was', async () => {
  renderGrid(grid, { routes: routes(() => jsonResponse(422, { detail: 'Operation 1 (setLeaf): fill fields.WPA key first' })) })
  await screen.findByText('Code forge')

  await editText('item-1', 'login.username', 'user.name')

  expect(await screen.findByRole('alert')).toHaveTextContent(/^Nothing was saved: fill fields\.WPA key first$/)
})

test('remove value posts removeLeaf', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  const menu = await openMenu('item-1', 'login.username')
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Remove value' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [{ kind: 'removeLeaf', itemId: 'item-1', revisionDate: '2026-10-01T10:00:00Z', column: 'login.username' }],
  })
})

test('a read-only cell offers no edit or remove', async () => {
  renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  const menu = await openMenu('item-1', 'revisionDate')

  expect(within(menu).queryByRole('menuitem', { name: 'Edit value' })).toBeNull()
  expect(within(menu).queryByRole('menuitem', { name: 'Remove value' })).toBeNull()
  expect(within(menu).getByRole('menuitem', { name: 'Move to trash' })).toBeInTheDocument()
})

// The menu's lines in order: subheaders, entries and the tool note line, without dividers.
function menuLines(menu: HTMLElement): string[] {
  return Array.from(menu.children, (child) => (child.textContent ?? '').trim()).filter((text) => text !== '')
}

test('the cell menu groups the cell entries under Cell and the row entries under Row', async () => {
  renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  const menu = await openMenu('item-1', 'login.username')

  expect(menuLines(menu)).toEqual([
    'Cell',
    'Edit value',
    'Remove value',
    'Row',
    'Add field',
    'Convert to template…',
    'Merge into item…',
    'Move to trash',
  ])
})

test('a cell without cell entries shows the Row group only', async () => {
  renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  const menu = await openMenu('item-1', 'revisionDate')

  expect(menuLines(menu)).toEqual(['Row', 'Add field', 'Convert to template…', 'Merge into item…', 'Move to trash'])
})

test('a right click on the template column opens the Row group only', async () => {
  renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  const menu = await openMenu('item-1', 'template')

  expect(menuLines(menu)).toEqual(['Row', 'Add field', 'Convert to template…', 'Merge into item…', 'Move to trash'])
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Move to trash' }))
  expect(await screen.findByRole('dialog', { name: 'Move to trash' })).toHaveTextContent('Code forge')
})

test.each([
  ['the ContextMenu key', { key: 'ContextMenu' }],
  ['Shift+F10', { key: 'F10', shiftKey: true }],
])('%s opens the same menu as a right click, also on the template column', async (_, key) => {
  renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.keyDown(cell('item-1', 'login.username'), key)
  const menu = await screen.findByRole('menu')
  expect(menuLines(menu)).toEqual([
    'Cell',
    'Edit value',
    'Remove value',
    'Row',
    'Add field',
    'Convert to template…',
    'Merge into item…',
    'Move to trash',
  ])
  fireEvent.keyDown(menu, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())

  fireEvent.keyDown(cell('item-1', 'template'), key)
  expect(menuLines(await screen.findByRole('menu'))).toEqual([
    'Row',
    'Add field',
    'Convert to template…',
    'Merge into item…',
    'Move to trash',
  ])
})

test('move to trash asks for confirmation first', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  const menu = await openMenu('item-1', 'name')
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Move to trash' }))

  const dialog = await screen.findByRole('dialog', { name: 'Move to trash' })
  expect(dialog).toHaveTextContent('Code forge')
  expect(posted(fetchMock)).toHaveLength(0)

  fireEvent.click(within(dialog).getByRole('button', { name: 'Move to trash' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [{ kind: 'trashItem', itemId: 'item-1', revisionDate: '2026-10-01T10:00:00Z' }],
  })
})

test('cancelling the trash confirmation posts nothing', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  const menu = await openMenu('item-1', 'name')
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Move to trash' }))
  const dialog = await screen.findByRole('dialog', { name: 'Move to trash' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(posted(fetchMock)).toHaveLength(0)
})

function tick(itemId: string): HTMLInputElement {
  const box = document.querySelector<HTMLInputElement>(`.ag-row[row-id="${CSS.escape(itemId)}"] .ag-selection-checkbox input`)
  if (!box) {
    throw new Error(`No checkbox on ${itemId}`)
  }
  return box
}

test('ticked rows move to the trash with one confirmation and one entry each', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')
  expect(screen.queryByText(/ selected$/)).toBeNull()

  fireEvent.click(tick('item-1'))
  fireEvent.click(tick('item-2'))

  expect(await screen.findByText('2 selected')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Move to trash' }))
  const dialog = await screen.findByRole('dialog', { name: 'Move 2 items to the trash?' })
  expect(dialog).toHaveTextContent('Code forge')
  expect(dialog).toHaveTextContent('Laptop')
  expect(dialog).toHaveTextContent('They can be restored from the trash in Bitwarden or from the Change log.')
  expect(posted(fetchMock)).toHaveLength(0)

  fireEvent.click(within(dialog).getByRole('button', { name: 'Move to trash' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [
      { kind: 'trashItem', itemId: 'item-1', revisionDate: '2026-10-01T10:00:00Z' },
      { kind: 'trashItem', itemId: 'item-2', revisionDate: '2026-10-02T10:00:00Z' },
    ],
  })
  await waitFor(() => expect(screen.queryByText('2 selected')).toBeNull())
  expect(tick('item-1').checked).toBe(false)
})

test('Clear empties the selection and a click on a cell selects nothing', async () => {
  renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.click(cell('item-1', 'login.username'))
  expect(screen.queryByText(/ selected$/)).toBeNull()

  fireEvent.click(tick('item-1'))
  expect(await screen.findByText('1 selected')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Clear' }))

  await waitFor(() => expect(screen.queryByText('1 selected')).toBeNull())
  expect(tick('item-1').checked).toBe(false)
})

test('the tool note row has no checkbox', async () => {
  renderWithToolNote()
  await screen.findByText('bwtidyK')

  // AG Grid keeps the disabled checkbox in the DOM and hides it with a class.
  const wrapper = document.querySelector('.ag-row[row-id="tool"] .ag-selection-checkbox')
  expect(wrapper === null || wrapper.classList.contains('ag-hidden')).toBe(true)
  expect(tick('item-1')).toBeInTheDocument()
})

test('the secret edit dialog never prefills the value', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-1', 'login.password'))

  const dialog = await screen.findByRole('dialog')
  const input = within(dialog).getByLabelText('New value')
  expect(input).toHaveValue('')
  // Masked with CSS, not a password input, and the password managers' opt-out is on it.
  expect(input).toHaveAttribute('type', 'text')
  expect(input).toHaveAttribute('data-bwignore', 'true')
  expect(textSecurity(input)).toBe('disc')
  // No secret was fetched to fill the dialog.
  expect(fetchMock.mock.calls.some(([path]) => String(path).includes('/secret'))).toBe(false)

  fireEvent.change(input, { target: { value: 'n3w-s3cret' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [
      { kind: 'setLeaf', itemId: 'item-1', revisionDate: '2026-10-01T10:00:00Z', column: 'login.password', value: 'n3w-s3cret' },
    ],
  })
})

test('a new value in a secret field column becomes a hidden field', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  // Code forge is a login without the BIOS Password field.
  fireEvent.doubleClick(cell('item-1', 'fields.BIOS Password'))
  const dialog = await screen.findByRole('dialog')
  fireEvent.change(within(dialog).getByLabelText('New value'), { target: { value: 'bios' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toMatchObject({ operations: [{ column: 'fields.BIOS Password', fieldType: 1 }] })
})

describe('the secret dialog of an item whose field of that name is text', () => {
  const STORED_AS_SECRET = 'The value is stored as a secret.'
  const withTextFields: GridData = {
    ...grid,
    rows: [
      ...grid.rows,
      {
        id: 'item-3',
        revisionDate: '2026-10-03T10:00:00Z',
        cells: { name: 'Empty text', type: 'note' },
        emptyTextFields: ['fields.BIOS Password'],
      },
      {
        id: 'item-4',
        revisionDate: '2026-10-04T10:00:00Z',
        cells: { name: 'Filled text', type: 'note', 'fields.BIOS Password': 'in the open' },
      },
    ],
  }

  function renderTextFields() {
    return renderGrid(withTextFields, { routes: routes() }).fetchMock
  }

  test.each(['item-3', 'item-4'])('%s warns and sends no field type', async (itemId) => {
    const fetchMock = renderTextFields()
    await screen.findByText('Code forge')

    fireEvent.doubleClick(cell(itemId, 'fields.BIOS Password'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('alert')).toHaveTextContent(TEXT_FIELD_WARNING)
    expect(within(dialog).queryByText(STORED_AS_SECRET)).toBeNull()
    expect(within(dialog).queryByText(/which is not shown here/)).toBeNull()
    fireEvent.change(within(dialog).getByLabelText('New value'), { target: { value: 'typed' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
    const [{ operations }] = posted(fetchMock) as [{ operations: [Record<string, unknown>] }]
    expect(operations[0]).toMatchObject({ kind: 'setLeaf', itemId, column: 'fields.BIOS Password', value: 'typed' })
    expect(operations[0]).not.toHaveProperty('fieldType')
  })

  test('a new field still says it is a secret and is sent as hidden', async () => {
    const fetchMock = renderTextFields()
    await screen.findByText('Code forge')

    fireEvent.doubleClick(cell('item-1', 'fields.BIOS Password'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(STORED_AS_SECRET)).toBeInTheDocument()
    expect(within(dialog).queryByRole('alert')).toBeNull()
    fireEvent.change(within(dialog).getByLabelText('New value'), { target: { value: 'typed' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
    expect(posted(fetchMock)[0]).toMatchObject({ operations: [{ column: 'fields.BIOS Password', fieldType: 1 }] })
  })

  test('a field that a queued Add field creates as text warns and sends no field type', async () => {
    const addField = {
      id: 7,
      createdAt: '2026-11-06T10:00:00Z',
      source: 'ui',
      summary: 'Set fields.BIOS Password of "Code forge"',
      status: 'pending',
      decidedAt: null,
      operation: {
        kind: 'setLeaf',
        itemId: 'item-1',
        revisionDate: '2026-10-01T10:00:00Z',
        column: 'fields.BIOS Password',
        value: 'plain start',
        fieldType: 0,
      },
      diff: [{ itemId: 'item-1', itemName: 'Code forge', column: 'fields.BIOS Password', before: null, after: 'plain start' }],
      diffError: null,
      result: null,
    }
    const { fetchMock } = renderGrid(withTextFields, {
      routes: routes(ok, { 'GET /api/queue?status=pending': () => jsonResponse(200, [addField]) }),
    })
    await waitFor(() => expect(cell('item-1', 'fields.BIOS Password')).toHaveClass('bw-queued-cell'))

    fireEvent.doubleClick(cell('item-1', 'fields.BIOS Password'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('alert')).toHaveTextContent(TEXT_FIELD_WARNING)
    expect(within(dialog).queryByText(STORED_AS_SECRET)).toBeNull()
    fireEvent.change(within(dialog).getByLabelText('New value'), { target: { value: 'typed' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
    const [{ operations }] = posted(fetchMock) as [{ operations: [Record<string, unknown>] }]
    expect(operations[0]).toMatchObject({ kind: 'setLeaf', itemId: 'item-1', column: 'fields.BIOS Password' })
    expect(operations[0]).not.toHaveProperty('fieldType')
  })

  test.each([
    ['an empty boolean', undefined, true],
    ['a boolean with a value', 'false', true],
    ['a true boolean', 'true', false],
  ])('%s gets a checkbox and the boolean note, never the secret text', async (_, current, sent) => {
    const withBoolean: GridData = {
      ...grid,
      rows: [
        ...grid.rows,
        {
          id: 'item-5',
          revisionDate: '2026-10-05T10:00:00Z',
          cells: { name: 'Boolean', type: 'note', ...(current === undefined ? {} : { 'fields.BIOS Password': current }) },
          booleanFields: ['fields.BIOS Password'],
        },
      ],
    }
    const { fetchMock } = renderGrid(withBoolean, { routes: routes() })
    await screen.findByText('Code forge')

    fireEvent.doubleClick(cell('item-5', 'fields.BIOS Password'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('alert')).toHaveTextContent(BOOLEAN_FIELD_NOTE)
    expect(within(dialog).queryByText(STORED_AS_SECRET)).toBeNull()
    expect(within(dialog).queryByText(TEXT_FIELD_WARNING)).toBeNull()
    expect(within(dialog).queryByLabelText('New value')).toBeNull()
    const checkbox = within(dialog).getByRole('checkbox', { name: 'True' })
    expect(checkbox).toHaveProperty('checked', current === 'true')
    fireEvent.click(checkbox)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
    const [{ operations }] = posted(fetchMock) as [{ operations: [Record<string, unknown>] }]
    expect(operations[0]).toMatchObject({ kind: 'setLeaf', itemId: 'item-5', column: 'fields.BIOS Password', value: sent })
    expect(operations[0]).not.toHaveProperty('fieldType')
  })

  test('a linked field opens no dialog and says why', async () => {
    const withLinked: GridData = {
      ...grid,
      rows: [
        ...grid.rows,
        {
          id: 'item-6',
          revisionDate: '2026-10-06T10:00:00Z',
          cells: { name: 'Linked', type: 'note' },
          linkedFields: ['fields.BIOS Password'],
        },
      ],
    }
    const { fetchMock } = renderGrid(withLinked, { routes: routes() })
    await screen.findByText('Code forge')

    fireEvent.doubleClick(cell('item-6', 'fields.BIOS Password'))
    expect(await screen.findByText(LINKED_FIELD_BLOCKER)).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(posted(fetchMock)).toHaveLength(0)
  })

  test('a hidden field with a value keeps its own text', async () => {
    renderTextFields()
    await screen.findByText('Code forge')

    fireEvent.doubleClick(cell('item-2', 'fields.BIOS Password'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/which is not shown here/)).toBeInTheDocument()
    expect(within(dialog).queryByRole('alert')).toBeNull()
  })
})

test('the uri list editor posts the whole list', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-1', 'login.uris'))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByLabelText('URI 1')).toHaveValue('https://git.example.org')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add URI' }))
  fireEvent.change(within(dialog).getByLabelText('URI 2'), { target: { value: 'https://code.example.org' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toMatchObject({
    operations: [{ kind: 'setLeaf', column: 'login.uris', value: ['https://git.example.org', 'https://code.example.org'] }],
  })
})

test('a new folder is chosen in the folder dialog', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-1', 'folder'))
  const dialog = await screen.findByRole('dialog')
  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Folder' }))
  fireEvent.click(await screen.findByRole('option', { name: 'New folder' }))
  fireEvent.change(within(dialog).getByLabelText('New folder name'), { target: { value: 'Servers' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [
      {
        kind: 'moveToFolder',
        itemIds: ['item-1'],
        revisionDates: { 'item-1': '2026-10-01T10:00:00Z' },
        folderName: 'Servers',
      },
    ],
  })
})

describe('an edit of what other clients change without a new revisionDate sends what the row showed', () => {
  const favorite = gridColumn('favorite')
  const data: GridData = {
    ...grid,
    columns: [...grid.columns, favorite],
    organizations: [{ name: 'Family', collections: ['Home', 'Shared'] }],
    rows: [
      { ...grid.rows[0], folderId: 'f1', collectionIds: [], cells: { ...grid.rows[0].cells, favorite: true } },
      { ...grid.rows[1], folderId: null, collectionIds: ['c2'], cells: { ...grid.rows[1].cells, favorite: false } },
    ],
  }

  test('the folder dialog sends the folder id', async () => {
    const { fetchMock } = renderGrid(data, { routes: routes() })
    await screen.findByText('Code forge')

    fireEvent.doubleClick(cell('item-2', 'folder'))
    const dialog = await screen.findByRole('dialog')
    fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Folder' }))
    fireEvent.click(await screen.findByRole('option', { name: 'Work' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
    expect(posted(fetchMock)[0]).toEqual({
      operations: [
        {
          kind: 'moveToFolder',
          itemIds: ['item-2'],
          revisionDates: { 'item-2': '2026-10-02T10:00:00Z' },
          folderName: 'Work',
          shownFolderIds: { 'item-2': null },
        },
      ],
    })
  })

  test('removing the folder sends the folder id', async () => {
    const { fetchMock } = renderGrid(data, { routes: routes() })
    await screen.findByText('Code forge')

    const menu = await openMenu('item-1', 'folder')
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Remove value' }))

    await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
    expect(posted(fetchMock)[0]).toEqual({
      operations: [
        {
          kind: 'moveToFolder',
          itemIds: ['item-1'],
          revisionDates: { 'item-1': '2026-10-01T10:00:00Z' },
          folderName: '',
          shownFolderIds: { 'item-1': 'f1' },
        },
      ],
    })
  })

  test('the collections dialog sends the collection ids', async () => {
    const { fetchMock } = renderGrid(data, { routes: routes() })
    await screen.findByText('Code forge')

    fireEvent.doubleClick(cell('item-2', 'collections'))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Home' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
    expect(posted(fetchMock)[0]).toEqual({
      operations: [
        {
          kind: 'setCollections',
          itemId: 'item-2',
          revisionDate: '2026-10-02T10:00:00Z',
          collectionNames: ['Home', 'Shared'],
          shownCollectionIds: ['c2'],
        },
      ],
    })
  })

  test('removing the favorite flag sends the flag', async () => {
    const { fetchMock } = renderGrid(data, { routes: routes() })
    await screen.findByText('Code forge')

    const menu = await openMenu('item-1', 'favorite')
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Remove value' }))

    await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
    expect(posted(fetchMock)[0]).toEqual({
      operations: [
        {
          kind: 'setLeaf',
          itemId: 'item-1',
          revisionDate: '2026-10-01T10:00:00Z',
          column: 'favorite',
          value: false,
          shownFavorite: true,
        },
      ],
    })
  })

  test('ticking the favorite flag sends the flag', async () => {
    const { fetchMock } = renderGrid(data, { routes: routes() })
    await screen.findByText('Laptop')

    fireEvent.doubleClick(cell('item-2', 'favorite'), { detail: 2 })
    const checkbox = await waitFor(() => {
      const found = cell('item-2', 'favorite').querySelector<HTMLInputElement>('input[type="checkbox"]')
      if (!found) {
        throw new Error('No editor')
      }
      return found
    })
    fireEvent.click(checkbox)
    fireEvent.keyDown(checkbox, { key: 'Enter' })

    await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
    expect(posted(fetchMock)[0]).toEqual({
      operations: [
        {
          kind: 'setLeaf',
          itemId: 'item-2',
          revisionDate: '2026-10-02T10:00:00Z',
          column: 'favorite',
          value: true,
          shownFavorite: false,
        },
      ],
    })
  })
})

test('a dialog closes at once and the next edit does not wait for the first', async () => {
  // The queue never answers here, nothing may wait for it.
  const { fetchMock } = renderGrid(grid, { routes: routes(() => new Promise<Response>(() => {}) as unknown as Response) })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-1', 'folder'))
  const dialog = await screen.findByRole('dialog')
  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Folder' }))
  fireEvent.click(await screen.findByRole('option', { name: 'New folder' }))
  fireEvent.change(within(dialog).getByLabelText('New folder name'), { target: { value: 'Servers' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(screen.queryByRole('progressbar')).toBeNull()

  await editText('item-1', 'login.username', 'user.name')
  await waitFor(() => expect(posted(fetchMock)).toHaveLength(2))
})

test('the folder label stays above the text for an item without a folder', async () => {
  renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-2', 'folder'))
  const dialog = await screen.findByRole('dialog')
  const select = within(dialog).getByRole('combobox', { name: 'Folder' })
  expect(select).toHaveTextContent('No folder')
  expect(dialog.querySelector('label')).toHaveAttribute('data-shrink', 'true')
})

test('a personal item moves into an organization with collections', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-1', 'organization'))
  const dialog = await screen.findByRole('dialog')
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Shared' }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Move' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [
      {
        kind: 'moveToOrganization',
        itemId: 'item-1',
        revisionDate: '2026-10-01T10:00:00Z',
        organizationName: 'Family',
        collectionNames: ['Shared'],
      },
    ],
  })
})

test('the organization dialog offers an organization no item is in, with its collections', async () => {
  renderGrid({ ...grid, organizations: [{ name: 'Book club', collections: ['Novels'] }] }, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-1', 'organization'))
  const dialog = await screen.findByRole('dialog')
  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Organization' }))
  fireEvent.click(await screen.findByRole('option', { name: 'Book club' }))
  expect(within(dialog).getByRole('checkbox', { name: 'Novels' })).toBeInTheDocument()
})

const NO_COLLECTIONS = 'has no collections, create one in Bitwarden first'

test('the organization dialog offers an organization with no collections as a disabled option with the reason', async () => {
  renderGrid({ ...grid, organizations: [{ name: 'Book club', collections: [] }] }, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-1', 'organization'))
  const dialog = await screen.findByRole('dialog')
  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Organization' }))
  const empty = await screen.findByRole('option', { name: `Book club ${NO_COLLECTIONS}` })
  expect(empty).toHaveAttribute('aria-disabled', 'true')
  expect(screen.getByRole('option', { name: 'Family' })).not.toHaveAttribute('aria-disabled')
})

test('the organization dialog does not preselect a sole organization with no collections', async () => {
  const data: GridData = { ...grid, rows: [grid.rows[0]], organizations: [{ name: 'Book club', collections: [] }] }
  renderGrid(data, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-1', 'organization'))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).queryByRole('combobox')).toBeNull()
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'No organization of the vault has a collection, create one in Bitwarden first.',
  )
  expect(within(dialog).getByRole('button', { name: 'Move' })).toBeDisabled()
})

test('the organization dialog preselects a sole organization that has collections', async () => {
  const data: GridData = { ...grid, rows: [grid.rows[0]], organizations: [{ name: 'Book club', collections: ['Novels'] }] }
  renderGrid(data, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-1', 'organization'))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByRole('combobox', { name: 'Organization' })).toHaveTextContent('Book club')
  expect(within(dialog).getByRole('checkbox', { name: 'Novels' })).toBeInTheDocument()
})

test('the collections dialog disables Save with no collection ticked and has no admins warning', async () => {
  const data: GridData = { ...grid, organizations: [{ name: 'Family', collections: ['Home', 'Shared'] }] }
  renderGrid(data, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-2', 'collections'))
  const dialog = await screen.findByRole('dialog')
  const save = within(dialog).getByRole('button', { name: 'Save' })
  expect(within(dialog).queryByText('Pick at least one collection.')).toBeNull()

  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Shared' }))
  expect(within(dialog).getByText('Pick at least one collection.')).toBeInTheDocument()
  expect(within(dialog).queryByText(/admins/)).toBeNull()
  expect(save).toBeDisabled()

  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Home' }))
  expect(within(dialog).queryByText('Pick at least one collection.')).toBeNull()
  expect(save).toBeEnabled()
})

test('the collections dialog of an organization with no collections says so', async () => {
  const data: GridData = {
    ...grid,
    rows: [{ ...grid.rows[1], cells: { ...grid.rows[1].cells, organization: 'Book club', collections: [] } }],
    organizations: [{ name: 'Book club', collections: [] }],
  }
  renderGrid(data, { routes: routes() })
  await screen.findByText('Laptop')

  fireEvent.doubleClick(cell('item-2', 'collections'))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByRole('alert')).toHaveTextContent(`Book club ${NO_COLLECTIONS}.`)
  expect(within(dialog).queryByRole('checkbox')).toBeNull()
  expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled()
})

test('organizationCollections offers what the listing holds and what the rows use', () => {
  const rows = [
    rowWith({ organization: 'Family', collections: ['Shared'] }),
    rowWith({ organization: 'unlisted-organization-id', collections: ['unlisted-collection-id'] }),
  ]
  const listing = [{ name: 'Family', collections: ['Home', 'Shared'] }]
  expect([...organizationCollections(rows, listing)]).toEqual([
    ['Family', ['Home', 'Shared']],
    ['unlisted-organization-id', ['unlisted-collection-id']],
  ])
})

test('the organization dialog says a collection is needed until one is ticked', async () => {
  renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-1', 'organization'))
  const dialog = await screen.findByRole('dialog')
  const hint = 'Pick at least one collection. Bitwarden needs one to move an item into an organization.'
  expect(within(dialog).getByText(hint)).toBeInTheDocument()
  expect(within(dialog).getByRole('button', { name: 'Move' })).toBeDisabled()

  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Shared' }))
  expect(within(dialog).queryByText(hint)).toBeNull()
  expect(within(dialog).getByRole('button', { name: 'Move' })).toBeEnabled()
})

test('the organization dialog of an organization item offers only the personal vault and warns first', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.doubleClick(cell('item-2', 'organization'))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByRole('heading')).toHaveTextContent('Move Laptop out of Family')
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'bwtidy makes a personal copy and moves this item to the trash. Attachments are copied, passkeys and password history are lost. Everyone who shares its collections loses it.',
  )
  expect(within(dialog).queryByRole('checkbox')).toBeNull()
  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Move to' }))
  const options = await screen.findAllByRole('option')
  expect(options.map((option) => option.textContent)).toEqual(['Personal vault'])
  fireEvent.click(options[0])
  expect(posted(fetchMock)).toHaveLength(0)

  fireEvent.click(within(dialog).getByRole('button', { name: 'Move' }))
  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [{ kind: 'moveToPersonal', itemId: 'item-2', revisionDate: '2026-10-02T10:00:00Z' }],
  })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})

test('a queued move to the personal vault shows the organization cell empty until the commit', async () => {
  let queued = false
  const entry = {
    id: 5,
    createdAt: '2026-10-29T10:00:00Z',
    source: 'ui',
    summary: 'Move "Laptop" to the personal vault',
    status: 'pending',
    decidedAt: null,
    operation: { kind: 'moveToPersonal', itemId: 'item-2', revisionDate: '2026-10-02T10:00:00Z' },
    diff: [
      { itemId: 'item-2', itemName: 'Laptop', column: 'organization', before: 'Family', after: null },
      { itemId: 'item-2', itemName: 'Laptop', column: 'collections', before: ['Shared'], after: null },
    ],
    diffError: null,
    result: null,
  }
  renderGrid(grid, {
    routes: routes(
      () => {
        queued = true
        return jsonResponse(201, { ids: [5] })
      },
      { 'GET /api/queue?status=pending': () => jsonResponse(200, queued ? [entry] : []) },
    ),
  })
  await screen.findByText('Code forge')
  expect(cell('item-2', 'organization')).toHaveTextContent('Family')

  fireEvent.doubleClick(cell('item-2', 'organization'))
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Move' }))

  await waitFor(() => expect(cell('item-2', 'organization')).toHaveClass('bw-queued-cell'))
  expect(cell('item-2', 'organization')).toHaveTextContent('')
  expect(cell('item-2', 'collections')).toHaveTextContent('')
})

test('add field posts setLeaf with the field type', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  const menu = await openMenu('item-2', 'name')
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Add field' }))
  const dialog = await screen.findByRole('dialog')
  fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Recovery key' } })
  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Type' }))
  fireEvent.click(await screen.findByRole('option', { name: 'Hidden' }))
  const value = within(dialog).getByLabelText('Value')
  // A hidden field's value is a text input masked with CSS, not a password input.
  expect(value).toHaveAttribute('type', 'text')
  expect(textSecurity(value)).toBe('disc')
  fireEvent.change(value, { target: { value: 'abc-123' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [
      {
        kind: 'setLeaf',
        itemId: 'item-2',
        revisionDate: '2026-10-02T10:00:00Z',
        column: 'fields.Recovery key',
        value: 'abc-123',
        fieldType: 1,
      },
    ],
  })
})

describe('add field on an item that has an empty field of that name', () => {
  const withEmptyFields: GridData = {
    ...grid,
    rows: [
      grid.rows[0],
      {
        ...grid.rows[1],
        emptyTextFields: ['fields.Plain'],
        emptySecrets: ['fields.Recovery key'],
        booleanFields: ['fields.Enabled'],
        linkedFields: ['fields.Linked'],
      },
    ],
  }

  async function openAddField(name: string) {
    const { fetchMock } = renderGrid(withEmptyFields, { routes: routes() })
    await screen.findByText('Code forge')
    const menu = await openMenu('item-2', 'name')
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Add field' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: name } })
    return { fetchMock, dialog }
  }

  test.each([
    ['Plain', 'The item has an empty text field of this name, the value goes into it as plain text.', 0, ''],
    ['Recovery key', 'The item has an empty hidden field of this name, the value goes into it as a secret.', 1, 'disc'],
  ])('%s fixes the type to the field\'s own', async (name, helper, fieldType, mask) => {
    const { fetchMock, dialog } = await openAddField(name)
    expect(within(dialog).getByText(helper)).toBeInTheDocument()
    expect(within(dialog).getByRole('combobox', { name: 'Type' })).toHaveAttribute('aria-disabled', 'true')
    const value = within(dialog).getByLabelText('Value')
    expect(value).toHaveAttribute('type', 'text')
    expect(textSecurity(value)).toBe(mask)
    fireEvent.change(value, { target: { value: 'abc' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }))

    await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
    expect(posted(fetchMock)[0]).toMatchObject({ operations: [{ column: `fields.${name}`, fieldType }] })
  })

  test.each([
    ['Enabled', 'The item has a boolean field of this name, it takes true or false and no typed value.'],
    ['Linked', 'The item has a linked field of this name, it has no value of its own.'],
  ])('%s cannot take a typed value', async (name, helper) => {
    const { fetchMock, dialog } = await openAddField(name)
    expect(within(dialog).getByText(helper)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Add' })).toBeDisabled()
    expect(posted(fetchMock)).toHaveLength(0)
  })
})

function rowWith(cells: GridRow['cells']): GridRow {
  return { id: 'item-9', revisionDate: '2026-10-01T10:00:00Z', cells: { name: 'Wifi', type: 'note', ...cells } }
}

test('a second occurrence waits for the first', () => {
  const second = gridColumn('fields.WPA key#2')
  expect(editBlocker(second, rowWith({}))).toBe('Fill WPA key first.')
  expect(editBlocker(second, rowWith({ 'fields.WPA key': 'x' }))).toBeNull()
  expect(editBlocker(gridColumn('fields.WPA key'), rowWith({}))).toBeNull()
})

test('a third occurrence waits for the second', () => {
  const third = gridColumn('fields.WPA key#3')
  expect(editBlocker(third, rowWith({ 'fields.WPA key': 'x' }))).toBe('Fill WPA key#2 first.')
  expect(editBlocker(third, rowWith({ 'fields.WPA key': 'x', 'fields.WPA key#2': 'y' }))).toBeNull()
})

test('an occurrence that has a value is never blocked, even after an empty one', () => {
  expect(editBlocker(gridColumn('fields.Empty#2'), rowWith({ 'fields.Empty#2': 'x' }))).toBeNull()
  expect(editBlocker(gridColumn('fields.key#2#2'), rowWith({}))).toBe('Fill key#2#1 first.')
})

test('a leaf of a type group cannot hold a value on an item of another type', () => {
  const username = gridColumn('login.username')
  expect(unavailableReason(username, rowWith({}))).toBe('Only login items have login.username.')
  expect(unavailableReason(gridColumn('card.number', { secret: true }), rowWith({ type: 'login' }))).toBe(
    'Only card items have card.number.',
  )
  expect(unavailableReason(username, rowWith({ type: 'login' }))).toBeNull()
  expect(editBlocker(username, rowWith({}))).toBe('Only login items have login.username.')
})

test('collections cannot hold a value on a personal item', () => {
  const collections = gridColumn('collections', { multi: true })
  const reason = 'Only organization items have collections. Move the item to an organization first.'
  expect(unavailableReason(collections, rowWith({}))).toBe(reason)
  expect(unavailableReason(collections, rowWith({ organization: 'Family' }))).toBeNull()
  expect(editBlocker(collections, rowWith({}))).toBe(reason)
})

test('a waiting occurrence cannot hold a value yet', () => {
  expect(unavailableReason(gridColumn('fields.WPA key#2'), rowWith({}))).toBe('Fill WPA key first.')
  expect(unavailableReason(gridColumn('fields.WPA key#2'), rowWith({ 'fields.WPA key': 'x' }))).toBeNull()
})

test('read-only columns, the organization, the tool note and free cells can hold a value', () => {
  for (const id of ['attachments', 'passkeys', 'revisionDate']) {
    expect(unavailableReason(gridColumn(id, { readOnly: true }), rowWith({ [id]: 1 }))).toBeNull()
  }
  expect(unavailableReason(gridColumn('type'), rowWith({}))).toBeNull()
  const organization = gridColumn('organization')
  expect(unavailableReason(organization, rowWith({ organization: 'Family' }))).toBeNull()
  expect(editBlocker(organization, rowWith({ organization: 'Family' }))).toBeNull()
  expect(unavailableReason(gridColumn('notes'), rowWith({}))).toBeNull()
  expect(unavailableReason(gridColumn('fields.PIN'), rowWith({}))).toBeNull()
  const toolNote = { ...rowWith({}), toolNote: true }
  expect(unavailableReason(gridColumn('name'), toolNote)).toBeNull()
  expect(editBlocker(gridColumn('name'), toolNote)).toBe(TOOL_NOTE_BLOCKER)
})

test('a waiting cell is grey, explains itself and opens no editor', async () => {
  const wifi: GridData = {
    columns: [
      grid.columns[0],
      grid.columns[1],
      gridColumn('fields.WPA key'),
      gridColumn('fields.WPA key#2'),
    ],
    rows: [
      { id: 'item-1', revisionDate: '2026-10-01T10:00:00Z', cells: { name: 'Home', type: 'note' } },
      { id: 'item-2', revisionDate: '2026-10-01T10:00:00Z', cells: { name: 'Cabin', type: 'note', 'fields.WPA key': 'x' } },
    ],
  }
  const { fetchMock } = renderGrid(wifi, { routes: { 'POST /api/queue': ok } })
  await screen.findByText('Home')

  expect(cell('item-1', 'fields.WPA key#2')).toHaveClass('bw-unavailable-cell')
  expect(cell('item-2', 'fields.WPA key#2')).not.toHaveClass('bw-unavailable-cell')
  expect(cell('item-1', 'fields.WPA key')).not.toHaveClass('bw-unavailable-cell')

  fireEvent.doubleClick(cell('item-1', 'fields.WPA key#2'), { detail: 2 })
  fireEvent.keyDown(cell('item-1', 'fields.WPA key#2'), { key: 'Enter' })

  await new Promise((resolve) => setTimeout(resolve, 50))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(cell('item-1', 'fields.WPA key#2').querySelector('input')).toBeNull()
  expect(posted(fetchMock)).toHaveLength(0)
})

// The tool note is found by the id the backend sends, whatever its name.
const withToolNote: GridData = {
  ...grid,
  toolNoteId: 'tool',
  rows: [
    ...grid.rows,
    {
      id: 'tool',
      revisionDate: '2026-10-28T10:00:00Z',
      cells: { name: 'bwtidyK', type: 'note', notes: '{"version": 1, "templates": []}' },
    },
  ],
}

function renderWithToolNote() {
  return renderGrid(withToolNote, { routes: { 'POST /api/queue': ok } }).fetchMock
}

test('the tool note row is read-only, found by id after a rename', async () => {
  const fetchMock = renderWithToolNote()
  await screen.findByText('bwtidyK')

  fireEvent.doubleClick(cell('tool', 'name'), { detail: 2 })

  expect(await screen.findByRole('alert')).toHaveTextContent(
    "bwtidy's own note. Templates are edited on the Types page.",
  )
  expect(cell('tool', 'name').querySelector('input')).toBeNull()
  expect(posted(fetchMock)).toHaveLength(0)
})

test('the cell menu offers no action on the tool note', async () => {
  const fetchMock = renderWithToolNote()
  await screen.findByText('bwtidyK')

  const menu = await openMenu('tool', 'name')

  expect(within(menu).queryByRole('menuitem', { name: 'Edit value' })).toBeNull()
  expect(within(menu).queryByRole('menuitem', { name: 'Remove value' })).toBeNull()
  expect(within(menu).queryByRole('menuitem', { name: 'Generate new value' })).toBeNull()
  expect(menu).toHaveTextContent("bwtidy's own note. Templates are edited on the Types page.")
  for (const name of ['Add field', 'Merge into item…', 'Move to trash']) {
    expect(within(menu).getByRole('menuitem', { name })).toHaveAttribute('aria-disabled', 'true')
  }
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Move to trash' }))
  expect(screen.queryByRole('dialog', { name: 'Move to trash' })).toBeNull()
  expect(posted(fetchMock)).toHaveLength(0)
})

const GENERATE = 'Generate new value'
const GENERATOR_SETTINGS = 'Generator settings for this item'

test.each([
  ['item-1', 'login.password'],
  ['item-2', 'fields.BIOS Password'],
])('Generate new value on %s %s asks first and queues a generateLeaf', async (itemId, column) => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.click(within(await openMenu(itemId, column)).getByRole('menuitem', { name: GENERATE }))
  const dialog = await screen.findByRole('dialog', { name: /^Generate new value for/ })
  expect(dialog).toHaveTextContent("Replace the value with a generated one? The old value stays in the item's password history.")
  expect(posted(fetchMock)).toHaveLength(0)
  fireEvent.click(within(dialog).getByRole('button', { name: 'Generate' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  const revisionDate = grid.rows.find((row) => row.id === itemId)?.revisionDate
  expect(posted(fetchMock)[0]).toEqual({ operations: [{ kind: 'generateLeaf', itemId, revisionDate, column }] })
})

test('Cancel in the Generate dialog queues nothing', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.click(within(await openMenu('item-1', 'login.password')).getByRole('menuitem', { name: GENERATE }))
  const dialog = await screen.findByRole('dialog', { name: /^Generate new value for/ })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(posted(fetchMock)).toHaveLength(0)
})

test('Generator settings for this item opens the dialog of the item', async () => {
  renderGrid(grid, {
    routes: {
      'GET /api/queue?status=pending': () => jsonResponse(200, []),
      'GET /api/generator': () =>
        jsonResponse(200, {
          default: {
            type: 'password',
            length: 24,
            uppercase: true,
            lowercase: true,
            number: true,
            special: true,
            minNumber: 1,
            minSpecial: 1,
            ambiguous: false,
          },
          items: {},
          saving: false,
        }),
    },
  })
    await screen.findByText('Code forge')

  fireEvent.click(within(await openMenu('item-1', 'login.password')).getByRole('menuitem', { name: GENERATOR_SETTINGS }))

  const dialog = await screen.findByRole('dialog', { name: 'Generator settings for Code forge' })
  expect(await within(dialog).findByRole('switch', { name: 'Use the default' })).toBeChecked()
})

test('the generator entries are on no other cell', async () => {
  renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  for (const [itemId, column] of [
    ['item-1', 'login.username'],
    ['item-1', 'name'],
    // A login password column on a note cannot hold a value.
    ['item-2', 'login.password'],
  ]) {
    const menu = await openMenu(itemId, column)
    expect(within(menu).queryByRole('menuitem', { name: GENERATE })).toBeNull()
    expect(within(menu).queryByRole('menuitem', { name: GENERATOR_SETTINGS })).toBeNull()
    fireEvent.keyDown(menu, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
  }
})

test('canGenerate takes a hidden field and leaves a text value in the same column', () => {
  const hidden = gridColumn('fields.Key', { secret: true })
  const row = (cells: GridRow['cells']): GridRow => ({ id: 'x', revisionDate: 'r', cells: { name: 'X', type: 'login', ...cells } })
  expect(canGenerate(hidden, row({ 'fields.Key': { masked: true } }))).toBe(true)
  expect(canGenerate(hidden, row({}))).toBe(true)
  expect(canGenerate(hidden, row({ 'fields.Key': 'plain text' }))).toBe(false)
  expect(canGenerate(hidden, { ...row({}), emptyTextFields: ['fields.Key'] })).toBe(false)
  expect(canGenerate(hidden, { ...row({}), booleanFields: ['fields.Key'] })).toBe(false)
  expect(canGenerate(hidden, { ...row({ 'fields.Key': 'true' }), booleanFields: ['fields.Key'] })).toBe(false)
  expect(canGenerate(hidden, { ...row({}), linkedFields: ['fields.Key'] })).toBe(false)
  expect(canGenerate(hidden, { ...row({}), emptySecrets: ['fields.Key'] })).toBe(true)
  const text: GridColumn = { ...hidden, secret: false }
  expect(canGenerate(text, row({}))).toBe(false)
  const totp: GridColumn = { ...hidden, id: 'login.totp', group: 'login' }
  expect(canGenerate(totp, row({}))).toBe(false)
})

test('plainInSecretColumn takes a text value of the item in a secret column and nothing else', () => {
  const hidden = gridColumn('fields.Key', { secret: true })
  const row = (cells: GridRow['cells'], extra: Partial<GridRow> = {}): GridRow => ({
    id: 'x',
    revisionDate: 'r',
    cells: { name: 'X', type: 'login', ...cells },
    ...extra,
  })
  expect(plainInSecretColumn(hidden, row({ 'fields.Key': 'plain text' }))).toBe(true)
  expect(plainInSecretColumn(hidden, row({ 'fields.Key': { masked: true } }))).toBe(false)
  expect(plainInSecretColumn(hidden, row({}))).toBe(false)
  // An empty text field shows nothing, so it has nothing to mark.
  expect(plainInSecretColumn(hidden, row({}, { emptyTextFields: ['fields.Key'] }))).toBe(false)
  expect(plainInSecretColumn({ ...hidden, secret: false }, row({ 'fields.Key': 'plain text' }))).toBe(false)
  const change = { entryId: 1, summary: 's', before: null, after: 'typed', fromUi: true, committing: false }
  const queued = (value: GridRow['cells'][string]) => ({ cells: new Map([['fields.Key', { value, changes: [change] }]]), trash: [] })
  expect(plainInSecretColumn(hidden, { ...row({}, { emptyTextFields: ['fields.Key'] }), queued: queued('typed') })).toBe(true)
  // A generated value of a hidden field shows as text until the commit, it is no text field.
  expect(plainInSecretColumn(hidden, { ...row({ 'fields.Key': { masked: true } }), queued: queued('(generated)') })).toBe(false)
})

test('another item named bwtidy is an ordinary item', async () => {
  const named: GridData = {
    ...grid,
    toolNoteId: 'tool',
    rows: [...grid.rows, { id: 'other', revisionDate: '2026-10-28T10:00:00Z', cells: { name: 'bwtidy', type: 'note' } }],
  }
  renderGrid(named, { routes: { 'POST /api/queue': ok } })
  await screen.findByText('bwtidy')

  const menu = await openMenu('other', 'name')

  expect(within(menu).getByRole('menuitem', { name: 'Edit value' })).toBeInTheDocument()
  expect(within(menu).getByRole('menuitem', { name: 'Move to trash' })).not.toHaveAttribute('aria-disabled')
})
