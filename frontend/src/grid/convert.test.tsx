import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { jsonResponse, type mockFetch } from '../test-utils.tsx'
import { gridAnswer, gridColumn, renderGrid, type Routes } from './testSupport.tsx'
import type { Template, TemplateMatches } from '../api/templates.ts'
import { defaultMapping } from './matches.ts'

afterEach(() => {
  vi.unstubAllGlobals()
})

const computer: Template = {
  name: 'Computer',
  type: 2,
  coordinates: [
    { kind: 'field', name: 'Username', fieldType: 0 },
    { kind: 'field', name: 'Password', fieldType: 1 },
    { kind: 'field', name: 'BIOS Password', fieldType: 1 },
    { kind: 'field', name: 'disk encryption', fieldType: 1 },
  ],
}

const webLogin: Template = {
  name: 'Web login',
  type: 1,
  coordinates: [
    { kind: 'path', path: 'login.username' },
    { kind: 'path', path: 'login.password' },
    { kind: 'path', path: 'login.totp' },
  ],
}

const grid = gridAnswer(
  [
    'name',
    'type',
    'notes',
    'login.username',
    gridColumn('login.password', { secret: true }),
    gridColumn('fields.bios password', { secret: true }),
    gridColumn('fields.Disk Encryption ', { secret: true }),
  ],
  [
    {
      id: 'laptop',
      cells: {
        name: 'Laptop',
        type: 'login',
        notes: 'Bought 2024',
        'login.username': 'user',
        'login.password': { masked: true },
        'fields.bios password': { masked: true },
        'fields.Disk Encryption ': { masked: true },
      },
    },
    {
      id: 'desktop',
      cells: { name: 'Desktop', type: 'note' },
    },
    {
      id: 'code-forge',
      cells: { name: 'Code forge', type: 'login', 'login.username': 'user', 'login.password': { masked: true } },
    },
    {
      id: 'wiki',
      cells: { name: 'Wiki', type: 'login' },
    },
  ],
)

const matches: TemplateMatches = {
  laptop: {
    match: null,
    scores: [
      { template: 'Computer', score: 0, typeMatches: false, filled: 0, total: 5, missing: ['fields.Username', 'fields.Password', 'fields.BIOS Password', 'fields.disk encryption'] },
      { template: 'Web login', score: 3 / 4, typeMatches: true, filled: 3, total: 4, missing: ['login.totp'] },
    ],
  },
  desktop: {
    match: 'Computer',
    scores: [
      { template: 'Computer', score: 1, typeMatches: true, filled: 5, total: 5, missing: [] },
      { template: 'Web login', score: 0, typeMatches: false, filled: 0, total: 4, missing: ['login.username', 'login.password', 'login.totp'] },
    ],
  },
  'code-forge': {
    match: null,
    scores: [
      { template: 'Computer', score: 3 / 5, typeMatches: false, filled: 3, total: 5, missing: ['fields.disk encryption'] },
      { template: 'Web login', score: 3 / 5, typeMatches: true, filled: 3, total: 5, missing: ['login.totp', 'login.uris'] },
    ],
  },
  wiki: {
    match: null,
    scores: [
      { template: 'Computer', score: 0, typeMatches: false, filled: 0, total: 5, missing: ['fields.Username', 'fields.Password', 'fields.BIOS Password', 'fields.disk encryption'] },
      { template: 'Web login', score: 1 / 4, typeMatches: true, filled: 1, total: 4, missing: ['login.username', 'login.password', 'login.totp'] },
    ],
  },
}

const routes: Routes = {
  'GET /api/templates': () => jsonResponse(200, { templates: [computer, webLogin] }),
  'GET /api/template-matches': () => jsonResponse(200, matches),
  'POST /api/queue': () => jsonResponse(201, { ids: [1] }),
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

async function openConvert(itemId: string): Promise<HTMLElement> {
  fireEvent.contextMenu(cell(itemId, 'name'), { clientX: 10, clientY: 10 })
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Convert to template…' }))
  return screen.findByRole('dialog')
}

async function chooseTemplate(dialog: HTMLElement, name: RegExp) {
  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Template' }))
  fireEvent.click(await screen.findByRole('option', { name }))
}

function mappingSelect(dialog: HTMLElement, target: string): HTMLElement {
  return within(dialog).getByRole('combobox', { name: `Value for ${target}` })
}

async function waitForMatches() {
  await waitFor(() => expect(cell('desktop', 'template')).toHaveTextContent('Computer'))
}

test('the template column shows the match or the best near miss with its fill', async () => {
  renderGrid(grid, { routes })
  await screen.findByText('Laptop')
  await waitForMatches()

  expect(cell('laptop', 'template')).toHaveTextContent('Web login 3/4')
  // On a tie the matching type wins, the type counts in the fill and is never named.
  expect(cell('code-forge', 'template')).toHaveTextContent(/^Web login 3\/5$/)
  // The type alone is no near miss.
  expect(cell('wiki', 'template')).toHaveTextContent(/^$/)
  expect(cell('laptop', 'template').closest('.ag-grid-pinned-left-cells')).not.toBeNull()
  const headers = [...document.querySelectorAll('.ag-grid-pinned-left-cells .ag-header-cell')].map((header) =>
    header.getAttribute('col-id'),
  )
  expect(headers).toEqual(['ag-Grid-SelectionColumn', 'name', 'template'])
})

test('the template filter shows the matches of one template', async () => {
  renderGrid(grid, { routes })
  await screen.findByText('Laptop')
  await waitForMatches()

  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Template' }))
  fireEvent.click(await screen.findByRole('option', { name: 'Computer' }))

  await waitFor(() => expect(screen.queryByText('Laptop')).not.toBeInTheDocument())
  expect(screen.getByText('Desktop')).toBeInTheDocument()
  expect(screen.queryByText('Code forge')).not.toBeInTheDocument()
})

test('the near miss filter follows the best near miss from 2 matched', async () => {
  renderGrid(grid, { routes })
  await screen.findByText('Laptop')
  await waitForMatches()

  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Template' }))
  fireEvent.click(await screen.findByRole('option', { name: 'Near misses of Web login' }))

  await waitFor(() => expect(screen.queryByText('Desktop')).not.toBeInTheDocument())
  expect(screen.getByText('Laptop')).toBeInTheDocument()
  expect(screen.getByText('Code forge')).toBeInTheDocument()
  expect(screen.queryByText('Wiki')).not.toBeInTheDocument()

  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Template' }))
  fireEvent.click(await screen.findByRole('option', { name: 'Near misses of Computer' }))

  await waitFor(() => expect(screen.queryByText('Laptop')).not.toBeInTheDocument())
  expect(screen.queryByText('Code forge')).not.toBeInTheDocument()
  expect(screen.queryByText('Desktop')).not.toBeInTheDocument()
})

test('the convert dialog shows the fill of every template', async () => {
  renderGrid(grid, { routes })
  await screen.findByText('Laptop')
  await waitForMatches()

  const dialog = await openConvert('laptop')
  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Template' }))
  const listbox = await screen.findByRole('listbox')
  expect([...listbox.querySelectorAll('[role="option"]')].map((option) => option.textContent)).toEqual([
    'Computer, Secure note (0/5 filled)',
    'Web login, Login (3/4 filled)',
  ])
})

test('the default mapping pairs names equal after case folding', () => {
  const sources = [
    { column: 'notes', secret: false },
    { column: 'login.username', secret: false },
    { column: 'login.password', secret: true },
    { column: 'fields.bios password', secret: true },
    { column: 'fields.Disk Encryption ', secret: true },
  ]
  expect(defaultMapping(computer, sources)).toEqual({
    'fields.Username': 'login.username',
    'fields.Password': 'login.password',
    'fields.BIOS Password': 'fields.bios password',
    'fields.disk encryption': 'fields.Disk Encryption ',
  })
  expect(defaultMapping(webLogin, sources)).toEqual({
    'login.username': 'login.username',
    'login.password': 'login.password',
  })
})

test('converting to a template of another type warns and posts applyTemplate with the mapping', async () => {
  const { fetchMock } = renderGrid(grid, { routes })
  await screen.findByText('Laptop')
  await waitForMatches()

  const dialog = await openConvert('laptop')
  await chooseTemplate(dialog, /^Computer/)

  expect(mappingSelect(dialog, 'fields.Username')).toHaveTextContent('login.username: user')
  expect(mappingSelect(dialog, 'fields.BIOS Password')).toHaveTextContent('fields.bios password: ••••••••')
  expect(within(dialog).getByRole('alert')).toHaveTextContent(/old item goes to the trash/)
  expect(dialog).not.toHaveTextContent('Bought 2024')

  // Leave the disk encryption field empty, it is created without a value.
  fireEvent.mouseDown(mappingSelect(dialog, 'fields.disk encryption'))
  fireEvent.click(await screen.findByRole('option', { name: 'Leave empty' }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Convert' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [
      {
        kind: 'applyTemplate',
        itemId: 'laptop',
        revisionDate: '2026-10-01T10:00:00Z',
        template: 'Computer',
        mapping: {
          'fields.Username': 'login.username',
          'fields.Password': 'login.password',
          'fields.BIOS Password': 'fields.bios password',
        },
      },
    ],
  })
})

test('a template of the same type gives no type change warning', async () => {
  const { fetchMock } = renderGrid(grid, { routes })
  await screen.findByText('Laptop')
  await waitForMatches()

  const dialog = await openConvert('code-forge')
  await chooseTemplate(dialog, /^Web login/)

  expect(within(dialog).queryByText(/old item goes to the trash/)).not.toBeInTheDocument()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Apply template' }))

  await waitFor(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(posted(fetchMock)[0]).toEqual({
    operations: [
      { kind: 'applyTemplate', itemId: 'code-forge', revisionDate: '2026-10-03T10:00:00Z', template: 'Web login', mapping: {} },
    ],
  })
})

test('a secret value cannot be mapped into a text coordinate', async () => {
  const { fetchMock } = renderGrid(grid, { routes })
  await screen.findByText('Laptop')
  await waitForMatches()

  const dialog = await openConvert('laptop')
  await chooseTemplate(dialog, /^Computer/)
  fireEvent.mouseDown(mappingSelect(dialog, 'fields.Username'))
  fireEvent.click(await screen.findByRole('option', { name: /^login\.password/ }))

  expect(await within(dialog).findAllByText(/cannot go into a coordinate that is not secret/)).toHaveLength(1)
  expect(within(dialog).getByRole('button', { name: 'Convert' })).toBeDisabled()
  expect(posted(fetchMock)).toHaveLength(0)
})
