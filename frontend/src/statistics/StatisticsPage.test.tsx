import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { jsonResponse, mockFetch, renderWithQuery } from '../test-utils.tsx'
import type { Statistics } from '../api/statistics.ts'
import StatisticsPage from './StatisticsPage.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

const ANSWER: Statistics = {
  limits: { maxPatternLen: 500, maxRepeat: 100, maxStates: 120, maxDepth: 20, maxValueLen: 1000 },
  reloads: 3,
  datatypes: [
    {
      id: '0000aaaa',
      name: 'PIN',
      states: 11,
      maxRepeat: 8,
      shortest: 4,
      valuesScored: 42,
      valuesTooLong: 1,
      slowestMs: 2.5,
      lastReloadMs: 7.25,
    },
  ],
  rows: [
    {
      itemId: 'slow',
      item: 'Slow item',
      column: 'fields.PIN',
      datatype: 'PIN',
      states: 11,
      maxRepeat: 8,
      shortest: 4,
      length: 6,
      distance: 2,
      repeatsUsed: [6],
      steps: 90,
      ms: 2.5,
      tooLong: false,
    },
    {
      itemId: 'fast',
      item: 'Another item',
      column: 'fields.Code',
      datatype: 'PIN',
      states: 11,
      maxRepeat: 8,
      shortest: 4,
      length: 1200,
      distance: null,
      repeatsUsed: [],
      steps: 0,
      ms: 0.5,
      tooLong: true,
    },
  ],
  rowCount: 2,
}

function renderPage(answer: Statistics = ANSWER) {
  mockFetch({ 'GET /api/statistics': () => jsonResponse(200, answer) })
  renderWithQuery(<StatisticsPage />)
}

function rowTexts(): string[][] {
  const table = screen.getByRole('table', { name: 'Scored values' })
  return within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) =>
      within(row)
        .getAllByRole('cell')
        .map((cell) => cell.textContent ?? ''),
    )
}

test('the page shows the limits, the datatypes and that the numbers stay in memory', async () => {
  renderPage()
  expect(await screen.findByRole('heading', { name: 'Statistics' })).toBeInTheDocument()
  expect(screen.getByText(/kept in memory for this session only/)).toBeInTheDocument()

  const limits = screen.getByRole('table', { name: 'Limits' })
  expect(within(limits).getByRole('row', { name: 'Pattern length 500' })).toBeInTheDocument()
  expect(within(limits).getByRole('row', { name: 'Repeat count 100' })).toBeInTheDocument()
  expect(within(limits).getByRole('row', { name: 'States 120' })).toBeInTheDocument()
  expect(within(limits).getByRole('row', { name: 'Nesting depth 20' })).toBeInTheDocument()
  expect(within(limits).getByRole('row', { name: 'Value length 1000' })).toBeInTheDocument()

  const datatypes = screen.getByRole('table', { name: 'Datatypes' })
  expect(within(datatypes).getByRole('row', { name: 'PIN 11 8 4 42 1 2.5 7.25' })).toBeInTheDocument()
})

test('the rows come slowest first with the repeat used of the declared', async () => {
  renderPage()
  await screen.findByRole('table', { name: 'Scored values' })
  expect(rowTexts()).toEqual([
    ['Slow item', 'fields.PIN', 'PIN', '6', '2', '6 of 8', '90', '2.5', ''],
    ['Another item', 'fields.Code', 'PIN', '1200', '', '', '0', '0.5', 'Yes'],
  ])
})

test('a column header sorts the rows, a second click turns them around', async () => {
  renderPage()
  await screen.findByRole('table', { name: 'Scored values' })
  const item = screen.getByRole('button', { name: 'Item' })
  fireEvent.click(item)
  expect(rowTexts().map((row) => row[0])).toEqual(['Slow item', 'Another item'])
  expect(screen.getByRole('columnheader', { name: 'Item' })).toHaveAttribute('aria-sort', 'descending')
  fireEvent.click(item)
  expect(rowTexts().map((row) => row[0])).toEqual(['Another item', 'Slow item'])
  expect(screen.getByRole('columnheader', { name: 'Item' })).toHaveAttribute('aria-sort', 'ascending')
})

test('a missing distance sorts below every number', async () => {
  renderPage()
  await screen.findByRole('table', { name: 'Scored values' })
  fireEvent.click(screen.getByRole('button', { name: 'Distance' }))
  expect(rowTexts().map((row) => row[0])).toEqual(['Slow item', 'Another item'])
})

test('it says when the rows are only the slowest ones', async () => {
  renderPage({ ...ANSWER, rowCount: 700 })
  expect(await screen.findByText('The 2 slowest of 700.')).toBeInTheDocument()
})

test('nothing scored yet says so', async () => {
  renderPage({ ...ANSWER, datatypes: [], rows: [], rowCount: 0 })
  expect(await screen.findByText('No datatypes yet.')).toBeInTheDocument()
  expect(screen.getByText('No value has been scored against a datatype yet.')).toBeInTheDocument()
  expect(screen.queryByRole('table', { name: 'Scored values' })).not.toBeInTheDocument()
})

test('a failed load says so', async () => {
  mockFetch({ 'GET /api/statistics': () => jsonResponse(500, { detail: 'Boom' }) })
  renderWithQuery(<StatisticsPage />)
  expect(await screen.findByText(/Could not load the statistics/)).toBeInTheDocument()
})
