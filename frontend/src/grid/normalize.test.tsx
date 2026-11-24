import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { jsonResponse, type mockFetch } from '../test-utils.tsx'
import type { GridData } from '../api/grid.ts'
import { gridAnswer, gridColumn, renderGrid, type Routes } from './testSupport.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

const grid = gridAnswer(
  [
    'name',
    'type',
    'notes',
    'login.username',
    gridColumn('login.password', { secret: true }),
    gridColumn('login.uris', { multi: true }),
    gridColumn('fields.Password', { secret: true }),
    gridColumn('fields.Recovery codes', { secret: true }),
    'fields.User',
    'fields.user',
  ],
  [
    {
      id: 'item-1',
      cells: {
        name: 'Code forge',
        type: 'login',
        'login.username': 'user',
        'login.password': { masked: true },
        'login.uris': ['https://git.example.test'],
        'fields.User': 'user',
        'fields.user': 'kl',
        'fields.Password': { masked: true },
      },
    },
    {
      id: 'item-2',
      cells: { name: 'Laptop', type: 'note', 'fields.user': 'youth', 'fields.Password': { masked: true } },
    },
    {
      id: 'item-3',
      cells: { name: 'Router', type: 'note', 'fields.User': 'admin' },
    },
    {
      id: 'item-4',
      cells: { name: 'Code forge recovery codes', type: 'note', notes: 'old codes', 'fields.Recovery codes': { masked: true } },
    },
    {
      id: 'item-5',
      cells: { name: 'Git mirror', type: 'login', 'login.uris': ['https://git.example.test', 'https://mirror.example.test'] },
    },
  ],
)

const nearDuplicates = [{ key: 'user', columns: ['fields.User', 'fields.user'] }]

const ok = () => jsonResponse(201, { ids: [1] })

function routes(post: (init: RequestInit) => Response = ok, extra: Routes = {}): Routes {
  return {
    'GET /api/near-duplicates': () => jsonResponse(200, nearDuplicates),
    'POST /api/queue': post,
    ...extra,
  }
}

function header(columnId: string): HTMLElement {
  const found = document.querySelector<HTMLElement>(`.ag-header-cell[col-id="${CSS.escape(columnId)}"]`)
  if (!found) {
    throw new Error(`No header ${columnId}`)
  }
  return found
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

async function headerAction(columnId: string, action: string) {
  fireEvent.click(await screen.findByRole('button', { name: `Actions for ${columnId}` }))
  const menu = await screen.findByRole('menu')
  fireEvent.click(within(menu).getByRole('menuitem', { name: action }))
}

async function choose(within_: HTMLElement, label: string, option: string) {
  fireEvent.mouseDown(within(within_).getByRole('combobox', { name: label }))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}

test('rename posts renameField with every item that has the field and its revisionDate', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  await headerAction('fields.Password', 'Rename field…')
  const dialog = await screen.findByRole('dialog', { name: 'Rename field Password' })
  const input = within(dialog).getByLabelText('New name')
  expect(input).toHaveValue('Password')
  fireEvent.change(input, { target: { value: 'password' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Rename' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [
      {
        kind: 'renameField',
        from: 'Password',
        to: 'password',
        items: [
          { itemId: 'item-1', revisionDate: '2026-10-01T10:00:00Z' },
          { itemId: 'item-2', revisionDate: '2026-10-02T10:00:00Z' },
        ],
      },
    ],
  })
})

test('the header buttons do not sort the column', async () => {
  renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')
  const label = () => header('fields.User').querySelector('.ag-cell-label-container')!

  fireEvent.click(await screen.findByRole('button', { name: 'Actions for fields.User' }))
  fireEvent.keyDown(await screen.findByRole('menu'), { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
  fireEvent.click(within(header('fields.User')).getByRole('button', { name: 'Near duplicate of fields.user' }))

  expect(label()).toHaveClass('ag-header-cell-sorted-none')
  // The label itself still sorts.
  fireEvent.click(header('fields.User').querySelector('.ag-header-cell-label')!)
  await waitFor(() => expect(label()).toHaveClass('ag-header-cell-sorted-asc'))
})

test('a rename onto an existing field name becomes a merge', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  await headerAction('fields.User', 'Rename field…')
  const dialog = await screen.findByRole('dialog', { name: 'Rename field User' })
  fireEvent.change(within(dialog).getByLabelText('New name'), { target: { value: 'user' } })
  expect(within(dialog).getByRole('alert')).toHaveTextContent('merges fields.User into fields.user')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Merge' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toMatchObject({
    operations: [{ kind: 'mergeColumns', sources: ['fields.User'], target: 'fields.user' }],
  })
})

test('a merge conflict opens a choice per item and sends again with the resolutions', async () => {
  const answers = [
    () =>
      jsonResponse(409, {
        detail: 'Some items need a choice',
        mergeConflicts: [{ itemId: 'item-1', columns: ['fields.User', 'fields.user'] }],
      }),
    ok,
  ]
  const { fetchMock } = renderGrid(grid, { routes: routes(() => answers.shift()!()) })
  await screen.findByText('Code forge')

  await headerAction('fields.User', 'Merge into…')
  const mergeDialog = await screen.findByRole('dialog', { name: 'Merge columns' })
  await choose(mergeDialog, 'Target column', 'fields.user')
  fireEvent.click(within(mergeDialog).getByRole('button', { name: 'Merge' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  const first = {
    kind: 'mergeColumns',
    sources: ['fields.User'],
    target: 'fields.user',
    items: [
      { itemId: 'item-1', revisionDate: '2026-10-01T10:00:00Z' },
      { itemId: 'item-3', revisionDate: '2026-10-03T10:00:00Z' },
    ],
  }
  expect(posted(fetchMock)[0]).toEqual({ operations: [first] })

  const choices = await screen.findByRole('dialog', { name: 'Choose the values to keep' })
  const codeForge = within(choices).getByRole('radiogroup', { name: 'Code forge' })
  expect(within(codeForge).getByText('user')).toBeInTheDocument()
  expect(within(codeForge).getByText('kl')).toBeInTheDocument()
  const merge = within(choices).getByRole('button', { name: 'Merge' })
  expect(merge).toBeDisabled()
  fireEvent.click(within(codeForge).getByRole('radio', { name: 'fields.user' }))
  fireEvent.click(merge)

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(2))
  expect(posted(fetchMock)[1]).toEqual({ operations: [{ ...first, resolutions: { 'item-1': 'fields.user' } }] })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})

test('columns selected in the chooser merge, and secret choices stay masked until revealed', async () => {
  const answers = [
    () =>
      jsonResponse(409, {
        detail: 'Some items need a choice',
        mergeConflicts: [{ itemId: 'item-1', columns: ['login.password', 'fields.Password'] }],
      }),
    ok,
  ]
  const { fetchMock } = renderGrid(grid, {
    routes: routes(() => answers.shift()!(), {
      'GET /api/items/item-1/secret?column=fields.Password': () => jsonResponse(200, { value: 'hunter2' }),
    }),
  })
  await screen.findByText('Code forge')

  fireEvent.click(screen.getByRole('button', { name: 'Columns' }))
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Select fields.Password for merge' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select login.password for merge' }))
  fireEvent.click(screen.getByRole('button', { name: 'Merge selected columns' }))

  const mergeDialog = await screen.findByRole('dialog', { name: 'Merge columns' })
  await choose(mergeDialog, 'Target column', 'login.password')
  expect(within(mergeDialog).getByRole('alert')).toHaveTextContent('1 item is not of type Login and has no login.password')
  fireEvent.click(within(mergeDialog).getByRole('button', { name: 'Merge' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toMatchObject({
    operations: [{ kind: 'mergeColumns', sources: ['fields.Password'], target: 'login.password' }],
  })

  const choices = await screen.findByRole('dialog', { name: 'Choose the values to keep' })
  expect(within(choices).getAllByTestId('secret-value').map((value) => value.textContent)).toEqual([
    '•'.repeat(8),
    '•'.repeat(8),
  ])
  expect(screen.queryByText('hunter2')).toBeNull()
  fireEvent.click(within(choices).getByRole('button', { name: 'Reveal fields.Password' }))
  expect(await within(choices).findByText('hunter2')).toBeInTheDocument()

  fireEvent.click(within(choices).getByRole('radio', { name: 'fields.Password' }))
  fireEvent.click(within(choices).getByRole('button', { name: 'Merge' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(2))
  expect(posted(fetchMock)[1]).toMatchObject({ operations: [{ resolutions: { 'item-1': 'fields.Password' } }] })
})

test('near-duplicate columns get a marker naming the others and merge in one click', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  const marker = await within(header('fields.User')).findByRole('button', { name: 'Near duplicate of fields.user' })
  expect(within(header('fields.user')).getByRole('button', { name: 'Near duplicate of fields.User' })).toBeInTheDocument()
  expect(within(header('fields.Password')).queryByTestId('near-duplicate-marker')).toBeNull()

  fireEvent.click(marker)
  fireEvent.click(await screen.findByRole('button', { name: 'Merge these' }))
  const mergeDialog = await screen.findByRole('dialog', { name: 'Merge columns' })
  expect(within(mergeDialog).getByRole('combobox', { name: 'Target column' })).toHaveTextContent('fields.User')
  expect(mergeDialog).toHaveTextContent('1 of them has more than one value')
  fireEvent.click(within(mergeDialog).getByRole('button', { name: 'Merge' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toMatchObject({
    operations: [{ kind: 'mergeColumns', sources: ['fields.user'], target: 'fields.User' }],
  })
})

test('the grid works without near-duplicate hints when they cannot load', async () => {
  renderGrid(grid, { routes: routes(ok, { 'GET /api/near-duplicates': () => jsonResponse(500, { detail: 'boom' }) }) })
  await screen.findByText('Code forge')

  expect(await screen.findByRole('button', { name: 'Actions for fields.User' })).toBeInTheDocument()
  expect(document.querySelector('[data-testid="near-duplicate-marker"]')).toBeNull()
})

test('merge into item posts mergeItems with the mapping from target to source', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.contextMenu(cell('item-4', 'name'), { clientX: 10, clientY: 10 })
  const menu = await screen.findByRole('menu')
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Merge into item…' }))

  const dialog = await screen.findByRole('dialog', { name: 'Merge Code forge recovery codes into another item' })
  const target = within(dialog).getByRole('combobox', { name: 'Target item' })
  fireEvent.change(target, { target: { value: 'Code f' } })
  const options = await screen.findAllByRole('option')
  expect(options.map((option) => option.textContent)).toEqual(['Code forgeLogin'])
  fireEvent.click(options[0])

  const mapping = await within(dialog).findByRole('table', { name: 'Mapping' })
  expect(within(mapping).getByRole('combobox', { name: 'Target for notes' })).toHaveTextContent('notes')
  expect(within(mapping).getByRole('combobox', { name: 'Target for fields.Recovery codes' })).toHaveTextContent(
    'fields.Recovery codes',
  )
  expect(within(mapping).getByTestId('secret-value')).toHaveTextContent('•'.repeat(8))
  await choose(mapping, 'Target for notes', "Don't move")
  fireEvent.click(within(dialog).getByRole('button', { name: 'Merge' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [
      {
        kind: 'mergeItems',
        fromItemId: 'item-4',
        fromRevisionDate: '2026-10-04T10:00:00Z',
        toItemId: 'item-1',
        toRevisionDate: '2026-10-01T10:00:00Z',
        mapping: { 'fields.Recovery codes': 'fields.Recovery codes' },
        append: [],
      },
    ],
  })
})

// option is the target's accessible name in the list: its name and type.
async function openMergeItems(source: string, sourceName: string, search: string, option: string): Promise<HTMLElement> {
  fireEvent.contextMenu(cell(source, 'name'), { clientX: 10, clientY: 10 })
  fireEvent.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Merge into item…' }))
  const dialog = await screen.findByRole('dialog', { name: `Merge ${sourceName} into another item` })
  fireEvent.change(within(dialog).getByRole('combobox', { name: 'Target item' }), { target: { value: search } })
  fireEvent.click(await screen.findByRole('option', { name: option }))
  return dialog
}

test("merge into item shows the target's value of the chosen column, masked for a secret", async () => {
  renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')
  const dialog = await openMergeItems('item-4', 'Code forge recovery codes', 'Code f', 'Code forgeLogin')

  const mapping = await within(dialog).findByRole('table', { name: 'Mapping' })
  expect(within(mapping).getByRole('columnheader', { name: 'Code forge has' })).toBeInTheDocument()
  const notesRow = within(mapping).getByRole('combobox', { name: 'Target for notes' }).closest('tr') as HTMLTableRowElement
  expect(notesRow.cells[3]).toHaveTextContent(/^$/)

  await choose(mapping, 'Target for notes', 'login.username')
  expect(notesRow.cells[3]).toHaveTextContent('user')
  expect(within(notesRow).getByText('Replaces the value Code forge has.')).toBeInTheDocument()

  await choose(mapping, 'Target for fields.Recovery codes', 'login.password')
  const codesRow = within(mapping).getByRole('combobox', { name: 'Target for fields.Recovery codes' }).closest('tr') as HTMLTableRowElement
  expect(within(codesRow.cells[3]).getByTestId('secret-value')).toHaveTextContent('•'.repeat(8))

  await choose(mapping, 'Target for notes', "Don't move")
  expect(notesRow.cells[3]).toHaveTextContent(/^$/)
  expect(within(dialog).getByText(/Values you don't move stay in Code forge recovery codes, which goes to the trash/)).toBeInTheDocument()
})

test('a list into a list the target has defaults to adding to it and posts append', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')
  const dialog = await openMergeItems('item-5', 'Git mirror', 'Code f', 'Code forgeLogin')

  const mapping = await within(dialog).findByRole('table', { name: 'Mapping' })
  expect(within(mapping).getByRole('combobox', { name: 'Target for login.uris' })).toHaveTextContent('login.uris')
  const list = within(mapping).getByRole('radiogroup', { name: 'List login.uris' })
  expect(within(list).getByRole('radio', { name: 'Add to the list' })).toBeChecked()
  expect(within(mapping).queryByText('Replaces the value Code forge has.')).toBeNull()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Merge' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [
      {
        kind: 'mergeItems',
        fromItemId: 'item-5',
        fromRevisionDate: '2026-10-05T10:00:00Z',
        toItemId: 'item-1',
        toRevisionDate: '2026-10-01T10:00:00Z',
        mapping: { 'login.uris': 'login.uris' },
        append: ['login.uris'],
      },
    ],
  })
})

test('Replace sends the list without append', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')
  const dialog = await openMergeItems('item-5', 'Git mirror', 'Code f', 'Code forgeLogin')

  const mapping = await within(dialog).findByRole('table', { name: 'Mapping' })
  fireEvent.click(within(mapping).getByRole('radio', { name: 'Replace' }))
  expect(within(mapping).getByText('Replaces the value Code forge has.')).toBeInTheDocument()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Merge' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  const [operation] = (posted(fetchMock)[0] as { operations: Record<string, unknown>[] }).operations
  expect(operation.mapping).toEqual({ 'login.uris': 'login.uris' })
  expect(operation.append).toEqual([])
})

test('two values mapped into one target column block the merge', async () => {
  renderGrid(grid, { routes: routes() })
  await screen.findByText('Code forge')

  fireEvent.contextMenu(cell('item-4', 'name'), { clientX: 10, clientY: 10 })
  fireEvent.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Merge into item…' }))
  const dialog = await screen.findByRole('dialog')
  fireEvent.change(within(dialog).getByRole('combobox', { name: 'Target item' }), { target: { value: 'Laptop' } })
  fireEvent.click(await screen.findByRole('option', { name: /Laptop/ }))

  const mapping = await within(dialog).findByRole('table', { name: 'Mapping' })
  await choose(mapping, 'Target for notes', 'fields.Recovery codes')

  expect(within(mapping).getAllByText('Two values go into fields.Recovery codes.')).toHaveLength(2)
  expect(within(dialog).getByRole('button', { name: 'Merge' })).toBeDisabled()
})

test('a rename the server finds to be a merge names the items', async () => {
  renderGrid(grid, {
    routes: routes(() =>
      jsonResponse(409, {
        detail: 'Some items need a choice',
        mergeConflicts: [{ itemId: 'item-2', columns: ['fields.Password', 'fields.password'] }],
      }),
    ),
  })
  await screen.findByText('Code forge')

  await headerAction('fields.Password', 'Rename field…')
  const dialog = await screen.findByRole('dialog')
  fireEvent.change(within(dialog).getByLabelText('New name'), { target: { value: 'password' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Rename' }))

  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('already have a field with the new name')
  expect(within(alert).getByText('Laptop')).toBeInTheDocument()
})

// A tool note renamed to look like an ordinary item, with a field other items have too.
const withToolNote: GridData = {
  ...grid,
  toolNoteId: 'tool',
  rows: [
    ...grid.rows,
    {
      id: 'tool',
      revisionDate: '2026-10-28T10:00:00Z',
      cells: { name: 'Code forge notes', type: 'note', 'fields.Password': { masked: true } },
    },
  ],
}

test('the normalize dialogs leave the tool note out', async () => {
  const { fetchMock } = renderGrid(withToolNote, { routes: routes() })
  await screen.findByText('Code forge notes')

  await headerAction('fields.Password', 'Rename field…')
  const rename = await screen.findByRole('dialog', { name: 'Rename field Password' })
  fireEvent.change(within(rename).getByLabelText('New name'), { target: { value: 'password' } })
  fireEvent.click(within(rename).getByRole('button', { name: 'Rename' }))
  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  const [renamed] = posted(fetchMock) as { operations: { items: { itemId: string }[] }[] }[]
  expect(renamed.operations[0].items.map((item) => item.itemId)).toEqual(['item-1', 'item-2'])

  fireEvent.contextMenu(cell('item-4', 'name'), { clientX: 10, clientY: 10 })
  const menu = await screen.findByRole('menu')
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Merge into item…' }))
  const merge = await screen.findByRole('dialog', { name: 'Merge Code forge recovery codes into another item' })
  fireEvent.change(within(merge).getByRole('combobox', { name: 'Target item' }), { target: { value: 'Code f' } })
  const options = await screen.findAllByRole('option')
  expect(options.map((option) => option.textContent)).toEqual(['Code forgeLogin'])
})
