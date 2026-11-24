import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { GridRow } from '../api/grid.ts'
import { jsonResponse, mockFetch, renderWithQuery } from '../test-utils.tsx'
import type { PasswordOptions } from '../api/generator.ts'
import { DEFAULT_PASSWORD } from '../shared/generatorRules.ts'
import ItemGeneratorDialog from './ItemGeneratorDialog.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

const ROUTE = '/api/generator/items/item-1'
const row: GridRow = { id: 'item-1', revisionDate: '2026-10-01T10:00:00Z', cells: { name: 'Code forge', type: 'login' } }
const SHORT: PasswordOptions = { ...DEFAULT_PASSWORD, length: 12, special: false }

function open(items: Record<string, PasswordOptions> = {}, put: () => Response = () => jsonResponse(202, { entryId: 1 })) {
  const fetchMock = mockFetch({
    'GET /api/generator': () => jsonResponse(200, { default: DEFAULT_PASSWORD, items, saving: false }),
    'POST /api/generator/example': () => jsonResponse(200, { value: 'example-value' }),
    [`PUT ${ROUTE}`]: put,
    [`DELETE ${ROUTE}`]: () => jsonResponse(202, { entryId: 2 }),
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
  })
  const onClose = vi.fn()
  renderWithQuery(<ItemGeneratorDialog row={row} onClose={onClose} />)
  return { fetchMock, onClose }
}

function calls(fetchMock: ReturnType<typeof mockFetch>, method: string): RequestInit[] {
  return fetchMock.mock.calls.filter(([url, init]) => String(url) === ROUTE && init?.method === method).map(([, init]) => init ?? {})
}

test('without a setting of its own the switch is on and the form hidden', async () => {
  open()
  expect(await screen.findByText(/follows the default: 24 characters/)).toBeInTheDocument()
  expect(screen.getByRole('dialog', { name: 'Generator settings for Code forge' })).toBeInTheDocument()
  expect(screen.getByRole('switch', { name: 'Use the default' })).toBeChecked()
  expect(screen.queryByRole('spinbutton', { name: 'Length' })).toBeNull()
})

test('turning the switch off starts from the default and Save sends the options', async () => {
  const { fetchMock, onClose } = open()
  await screen.findByText(/follows the default/)
  fireEvent.click(screen.getByRole('switch', { name: 'Use the default' }))
  const length = screen.getByRole('spinbutton', { name: 'Length' })
  expect(length).toHaveValue(24)
  fireEvent.change(length, { target: { value: '12' } })
  fireEvent.click(screen.getByRole('checkbox', { name: 'Special' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(onClose).toHaveBeenCalled())
  const [put] = calls(fetchMock, 'PUT')
  expect(JSON.parse(String(put.body))).toEqual(SHORT)
})

test('an own setting shows with the switch off, and Use the default deletes it', async () => {
  const { fetchMock, onClose } = open({ 'item-1': SHORT })
  expect(await screen.findByRole('spinbutton', { name: 'Length' })).toHaveValue(12)
  expect(screen.getByRole('switch', { name: 'Use the default' })).not.toBeChecked()
  expect(screen.getByRole('checkbox', { name: 'Special' })).not.toBeChecked()
  fireEvent.click(screen.getByRole('switch', { name: 'Use the default' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(calls(fetchMock, 'DELETE')).toHaveLength(1)
  expect(calls(fetchMock, 'PUT')).toHaveLength(0)
})

test('options outside the limits turn Save off', async () => {
  open({ 'item-1': SHORT })
  fireEvent.change(await screen.findByRole('spinbutton', { name: 'Length' }), { target: { value: '200' } })
  expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
})

test('a refusal stays in the dialog', async () => {
  const { onClose } = open({ 'item-1': SHORT }, () => jsonResponse(404, { detail: 'No such item in the vault' }))
  fireEvent.change(await screen.findByRole('spinbutton', { name: 'Length' }), { target: { value: '13' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  expect(await screen.findByText('Nothing was saved: No such item in the vault')).toBeInTheDocument()
  expect(onClose).not.toHaveBeenCalled()
})
