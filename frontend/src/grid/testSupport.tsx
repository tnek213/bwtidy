import type { QueryClient } from '@tanstack/react-query'
import { columnGroup } from '../shared/coordinates.ts'
import type { GridColumn, GridData, GridRow } from '../api/grid.ts'
import { jsonResponse, mockFetch, newQueryClient, renderWithQuery } from '../test-utils.tsx'
import GridPage from './GridPage.tsx'

// A plain column of the grid answer: its label is its id and its group the id's prefix. The extra
// fields turn on secret, multi or readOnly (or set another group).
export function gridColumn(id: string, extra: Partial<GridColumn> = {}): GridColumn {
  return { id, label: id, group: columnGroup(id), secret: false, multi: false, readOnly: false, ...extra }
}

// A row without a revisionDate gets one from its place: 2026-10-01T10:00:00Z for the first row,
// 2026-10-02T10:00:00Z for the second, and so on.
type RowSpec = Omit<GridRow, 'revisionDate'> & { revisionDate?: string }

// The server's grid answer from a short description: a column given as an id is a plain one. The
// rest (toFill, folders and the like) goes in extra.
export function gridAnswer(
  columns: (string | GridColumn)[],
  rows: RowSpec[],
  extra: Omit<GridData, 'columns' | 'rows'> = {},
): GridData {
  return {
    columns: columns.map((column) => (typeof column === 'string' ? gridColumn(column) : column)),
    rows: rows.map((row, index) => ({
      revisionDate: `2026-10-${String(index + 1).padStart(2, '0')}T10:00:00Z`,
      ...row,
    })),
    ...extra,
  }
}

export type Routes = Parameters<typeof mockFetch>[0]

interface RenderGridOptions {
  // More routes for the fetch mock, or another answer for GET /api/grid.
  routes?: Routes
  queryClient?: QueryClient
}

// Mounts GridPage with the query client of test-utils and a fetch mock that answers GET /api/grid.
// The answer may be a function, to change it during the test. Every other request a test makes
// needs a route of its own.
export function renderGrid(answer: GridData | (() => GridData), { routes = {}, queryClient = newQueryClient() }: RenderGridOptions = {}) {
  const fetchMock = mockFetch({
    'GET /api/grid': () => jsonResponse(200, typeof answer === 'function' ? answer() : answer),
    ...routes,
  })
  renderWithQuery(<GridPage />, queryClient)
  return { fetchMock, queryClient }
}
