import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { IRowNode } from 'ag-grid-community'
import type { CustomCellRendererProps } from 'ag-grid-react'
import { afterEach, expect, test, vi } from 'vitest'
import { jsonResponse, mockFetch } from '../test-utils.tsx'
import { rowInView, SecretCell } from './cells.tsx'
import type { CellValue, GridRow } from '../api/grid.ts'
import { MASK } from '../shared/values.ts'

afterEach(() => {
  vi.unstubAllGlobals()
})

const ROW_HEIGHT = 42

// A stand-in for the grid: a scroll position, a viewport height and the bodyScroll listeners.
function fakeGrid() {
  const listeners = new Set<() => void>()
  const grid = {
    scrollTop: 0,
    height: 10 * ROW_HEIGHT,
    api: {
      getVerticalPixelRange: () => ({ top: grid.scrollTop, bottom: grid.scrollTop + grid.height }),
      addEventListener: (type: string, listener: () => void) => {
        if (type === 'bodyScroll') {
          listeners.add(listener)
        }
      },
      removeEventListener: (type: string, listener: () => void) => {
        if (type === 'bodyScroll') {
          listeners.delete(listener)
        }
      },
      isDestroyed: () => false,
    },
    listeners,
    scrollTo(top: number) {
      grid.scrollTop = top
      act(() => listeners.forEach((listener) => listener()))
    },
  }
  return grid
}

function rowNode(rowIndex: number | null): IRowNode {
  return {
    rowIndex,
    rowTop: rowIndex === null ? null : rowIndex * ROW_HEIGHT,
    rowHeight: ROW_HEIGHT,
  } as IRowNode
}

function renderSecret(grid: ReturnType<typeof fakeGrid>, rowIndex: number) {
  const row: GridRow = { id: 'item-1', revisionDate: '2026-10-01T10:00:00Z', cells: { 'login.password': { masked: true } } }
  const props = {
    data: row,
    value: { masked: true },
    column: { getColId: () => 'login.password' },
    api: grid.api,
    node: rowNode(rowIndex),
  } as unknown as CustomCellRendererProps<GridRow, CellValue>
  mockFetch({
    'GET /api/items/item-1/secret?column=login.password': () => jsonResponse(200, { value: 'hunter2' }),
  })
  render(<SecretCell {...props} />)
}

async function reveal() {
  fireEvent.click(screen.getByRole('button', { name: 'Reveal login.password' }))
  await waitFor(() => expect(screen.getByTestId('secret-value')).toHaveTextContent('hunter2'))
}

test('a row is in view while any part of it is inside the vertical pixel range', () => {
  const grid = fakeGrid()
  expect(rowInView(grid.api, rowNode(0))).toBe(true)
  expect(rowInView(grid.api, rowNode(9))).toBe(true)
  expect(rowInView(grid.api, rowNode(10))).toBe(false)
  grid.scrollTop = ROW_HEIGHT / 2
  expect(rowInView(grid.api, rowNode(0))).toBe(true)
  expect(rowInView(grid.api, rowNode(10))).toBe(true)
  grid.scrollTop = ROW_HEIGHT
  expect(rowInView(grid.api, rowNode(0))).toBe(false)
  expect(rowInView(grid.api, rowNode(null))).toBe(false)
})

test('a revealed value hides when its row scrolls out of view', async () => {
  const grid = fakeGrid()
  renderSecret(grid, 0)
  await reveal()

  grid.scrollTo(ROW_HEIGHT / 2)
  expect(screen.getByTestId('secret-value')).toHaveTextContent('hunter2')

  grid.scrollTo(20 * ROW_HEIGHT)
  expect(screen.getByTestId('secret-value')).toHaveTextContent(MASK)
  expect(screen.queryByText('hunter2')).toBeNull()

  grid.scrollTo(0)
  expect(screen.getByTestId('secret-value')).toHaveTextContent(MASK)
  expect(screen.getByRole('button', { name: 'Reveal login.password' })).toBeInTheDocument()
})

test('a masked cell listens to no scroll, a revealed one stops when it hides', async () => {
  const grid = fakeGrid()
  renderSecret(grid, 3)
  expect(grid.listeners.size).toBe(0)
  await reveal()
  expect(grid.listeners.size).toBe(1)

  fireEvent.click(screen.getByRole('button', { name: 'Hide login.password' }))
  expect(screen.getByTestId('secret-value')).toHaveTextContent(MASK)
  expect(grid.listeners.size).toBe(0)
})

test('an answer that arrives after the row left the view shows nothing', async () => {
  const grid = fakeGrid()
  let answer: (response: Response) => void = () => {}
  mockFetch({
    'GET /api/items/item-1/secret?column=login.password': () =>
      new Promise<Response>((resolve) => {
        answer = resolve
      }),
  })
  const row: GridRow = { id: 'item-1', revisionDate: '2026-10-01T10:00:00Z', cells: { 'login.password': { masked: true } } }
  const props = {
    data: row,
    value: { masked: true },
    column: { getColId: () => 'login.password' },
    api: grid.api,
    node: rowNode(0),
  } as unknown as CustomCellRendererProps<GridRow, CellValue>
  render(<SecretCell {...props} />)

  fireEvent.click(screen.getByRole('button', { name: 'Reveal login.password' }))
  grid.scrollTo(20 * ROW_HEIGHT)
  await act(async () => answer(jsonResponse(200, { value: 'hunter2' })))

  expect(screen.getByTestId('secret-value')).toHaveTextContent(MASK)
  expect(screen.queryByText('hunter2')).toBeNull()
  expect(grid.listeners.size).toBe(0)
})

test('an answer pending while the row scrolled out and back shows nothing', async () => {
  const grid = fakeGrid()
  let answer: (response: Response) => void = () => {}
  mockFetch({
    'GET /api/items/item-1/secret?column=login.password': () =>
      new Promise<Response>((resolve) => {
        answer = resolve
      }),
  })
  const row: GridRow = { id: 'item-1', revisionDate: '2026-10-01T10:00:00Z', cells: { 'login.password': { masked: true } } }
  const props = {
    data: row,
    value: { masked: true },
    column: { getColId: () => 'login.password' },
    api: grid.api,
    node: rowNode(0),
  } as unknown as CustomCellRendererProps<GridRow, CellValue>
  render(<SecretCell {...props} />)

  fireEvent.click(screen.getByRole('button', { name: 'Reveal login.password' }))
  expect(grid.listeners.size).toBe(1)
  grid.scrollTo(20 * ROW_HEIGHT)
  grid.scrollTo(0)
  await act(async () => answer(jsonResponse(200, { value: 'hunter2' })))

  expect(screen.getByTestId('secret-value')).toHaveTextContent(MASK)
  expect(screen.queryByText('hunter2')).toBeNull()
  expect(grid.listeners.size).toBe(0)
})
