import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App from '../App.tsx'
import type { GridData } from '../api/grid.ts'
import { jsonResponse, mockFetch, renderWithQuery } from '../test-utils.tsx'
import type { Datatype } from '../api/datatypes.ts'
import type { Template } from '../api/templates.ts'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const computer: Template = {
  name: 'Computer',
  type: 2,
  coordinates: [
    { kind: 'field', name: 'user', fieldType: 0 },
    { kind: 'field', name: 'user password', fieldType: 1 },
  ],
}

const pin: Datatype = {
  id: '1f0c9a3e',
  name: 'PIN',
  pattern: '\\d{4,8}',
  description: '',
  ignoreCase: false,
  wholeValue: true,
  examples: ['1234'],
  counterExamples: [],
  states: 10,
  maxRepeat: 8,
  shortest: 4,
  broken: null,
}

const grid: GridData = {
  columns: [
    { id: 'name', label: 'name', group: 'item', secret: false, multi: false, readOnly: false },
    { id: 'type', label: 'type', group: 'item', secret: false, multi: false, readOnly: false },
  ],
  toolNoteId: 'tool-note',
  rows: [{ id: 'tool-note', revisionDate: '2026-10-20T08:00:00Z', cells: { name: 'bwtidy', type: 'note' } }],
  fields: [
    { name: 'user', fieldType: 0 },
    { name: 'user password', fieldType: 1 },
  ],
}

function renderApp() {
  mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false }),
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/grid': () => jsonResponse(200, grid),
    'GET /api/templates': () => jsonResponse(200, { templates: [computer], saving: false }),
    'GET /api/datatypes': () => jsonResponse(200, { datatypes: [pin], saving: false }),
    'GET /api/template-matches': () => jsonResponse(200, {}),
  })
  renderWithQuery(<App />)
}

test('the app bar button Types opens the page with the tabs Templates and Datatypes', async () => {
  renderApp()

  expect(screen.queryByRole('button', { name: 'Templates' })).not.toBeInTheDocument()
  fireEvent.click(await screen.findByRole('button', { name: 'Types' }))

  expect(await screen.findByRole('tab', { name: 'Templates' })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Datatypes' })).toBeInTheDocument()
  const table = await screen.findByRole('table', { name: 'Templates' })
  const row = within(table).getAllByRole('row')[1]
  expect(row).toHaveTextContent('Computer')
  expect(row).toHaveTextContent('Secure note')
  expect(row).toHaveTextContent('fields.user password')
})

test('the tabs switch between the Templates and the Datatypes', async () => {
  renderApp()
  fireEvent.click(await screen.findByRole('button', { name: 'Types' }))
  expect(await screen.findByRole('table', { name: 'Templates' })).toBeInTheDocument()

  fireEvent.click(screen.getByRole('tab', { name: 'Datatypes' }))

  const table = await screen.findByRole('table', { name: 'Datatypes' })
  expect(within(table).getAllByRole('row')[1]).toHaveTextContent('PIN')
  expect(screen.queryByRole('table', { name: 'Templates' })).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('tab', { name: 'Templates' }))
  expect(await screen.findByRole('table', { name: 'Templates' })).toBeInTheDocument()
})
