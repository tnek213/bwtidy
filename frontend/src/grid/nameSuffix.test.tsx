import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { NAME_SUFFIXES, outOfViewColumns, shownSuffixes, type ViewApi } from './nameSuffix.tsx'
import type { ShownRow } from './queued.ts'

const ids = (suffixes: { columnId: string }[]) => suffixes.map((suffix) => suffix.columnId)

test('a suffix shows only while its column is out of view, in registry order', () => {
  expect(ids(shownSuffixes(new Set()))).toEqual([])
  expect(ids(shownSuffixes(new Set(['type'])))).toEqual(['type'])
  expect(ids(shownSuffixes(new Set(['template'])))).toEqual(['template'])
  expect(ids(shownSuffixes(new Set(['template', 'type', 'folder'])))).toEqual(['type', 'template'])
})

interface FakeColumn {
  visible?: boolean
  pinned?: 'left' | null
  left: number
  width: number
}

function fakeApi(columns: Record<string, FakeColumn>, range = { left: 0, right: 1000 }): ViewApi {
  return {
    getHorizontalPixelRange: () => range,
    getColumn: (id) => {
      const column = columns[id]
      return column
        ? {
            isVisible: () => column.visible ?? true,
            getPinned: () => column.pinned ?? null,
            getLeft: () => column.left,
            getActualWidth: () => column.width,
          }
        : null
    },
  }
}

test('a hidden or missing column is out of view, a pinned one never', () => {
  const api = fakeApi({ type: { visible: false, left: 0, width: 100 }, template: { pinned: 'left', left: 0, width: 180 } })
  expect(outOfViewColumns(api)).toEqual(new Set(['type']))
  expect(outOfViewColumns(fakeApi({}))).toEqual(new Set(['type', 'template']))
})

test('a column less than 90% inside the scrolled viewport is out of view', () => {
  const columns = { type: { left: 0, width: 100 }, template: { pinned: 'left' as const, left: 0, width: 180 } }
  expect(outOfViewColumns(fakeApi(columns, { left: 0, right: 800 }))).toEqual(new Set())
  expect(outOfViewColumns(fakeApi(columns, { left: 10, right: 810 }))).toEqual(new Set())
  expect(outOfViewColumns(fakeApi(columns, { left: 11, right: 811 }))).toEqual(new Set(['type']))
  expect(outOfViewColumns(fakeApi({ ...columns, type: { left: 750, width: 100 } }, { left: 0, right: 840 }))).toEqual(
    new Set(),
  )
  expect(outOfViewColumns(fakeApi({ ...columns, type: { left: 750, width: 100 } }, { left: 0, right: 839 }))).toEqual(
    new Set(['type']),
  )
})

test('without a layout every visible column is in view', () => {
  const columns = { type: { left: 500, width: 100 }, template: { pinned: 'left' as const, left: 0, width: 180 } }
  expect(outOfViewColumns(fakeApi(columns, { left: 0, right: 0 }))).toEqual(new Set())
})

function renderSuffix(columnId: string, row: ShownRow, matches = {}) {
  const suffix = NAME_SUFFIXES.find((entry) => entry.columnId === columnId)!
  return render(<>{suffix.render(row, { matches })}</>)
}

test('the type suffix is an icon named by the type label', () => {
  renderSuffix('type', { id: 'a', revisionDate: '', cells: { name: 'Code forge', type: 'login' } })
  expect(screen.getByRole('img', { name: 'Login' })).toBeInTheDocument()
})

test('the template suffix names the match, or the near miss with its fill, or nothing', () => {
  const row: ShownRow = { id: 'a', revisionDate: '', cells: { name: 'Laptop', type: 'note' } }
  const near = { template: 'Computer', score: 0.75, typeMatches: true, filled: 3, total: 4, missing: ['fields.BIOS'] }
  const { unmount } = renderSuffix('template', row, { a: { match: 'Disk', scores: [] } })
  expect(screen.getByText('Disk')).toBeInTheDocument()
  unmount()
  const second = renderSuffix('template', row, { a: { match: null, scores: [near] } })
  expect(screen.getByText('Computer 3/4')).toBeInTheDocument()
  second.unmount()
  const { container } = renderSuffix('template', row, {})
  expect(container).toBeEmptyDOMElement()
})
