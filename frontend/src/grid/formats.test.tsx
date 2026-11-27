import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, onTestFinished, test, vi } from 'vitest'
import { advanceClock, jsonResponse, type mockFetch, waitForPolls } from '../test-utils.tsx'
import { type CellMarks, noCellMarks } from './cellMarks.ts'
import { type CellFormat, cellFormats, FORMAT_CLASSES, liveFormatText } from './formats.ts'
import { columnDef } from './columns.tsx'
import { gridAnswer, gridColumn, renderGrid, type Routes } from './testSupport.tsx'
import type { ShownRow } from './queued.ts'
import type { FormatScore, Template, TemplateMatches, TemplateScore } from '../api/templates.ts'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const PIN_ID = '1f0c9a3e'
const NOTE_ID = '2a7b1c4d'

const grid = gridAnswer(
  [
    'name',
    'type',
    'notes',
    gridColumn('login.uris', { multi: true }),
    'fields.PIN',
    gridColumn('fields.BIOS password', { secret: true }),
    'fields.Other',
  ],
  [
    {
      id: 'full',
      cells: { name: 'Full', type: 'note', notes: 'text', 'fields.PIN': '1234', 'fields.BIOS password': { masked: true }, 'fields.Other': 'x' },
    },
    {
      id: 'near',
      cells: { name: 'Near', type: 'note', notes: 'text', 'fields.PIN': ' 1234', 'fields.BIOS password': { masked: true } },
    },
    {
      id: 'empty',
      cells: { name: 'Empty', type: 'note', notes: 'text', 'fields.BIOS password': { masked: true } },
    },
    { id: 'plain', cells: { name: 'Plain', type: 'note', 'fields.PIN': '1234' } },
  ],
)

const computer: Template = {
  name: 'Computer',
  type: 2,
  coordinates: [
    { kind: 'path', path: 'notes', datatype: NOTE_ID },
    { kind: 'field', name: 'PIN', fieldType: 0, datatype: PIN_ID },
    { kind: 'field', name: 'BIOS password', fieldType: 1, datatype: PIN_ID },
  ],
}

function format(column: string, score: number, rest: Partial<FormatScore> = {}): FormatScore {
  const off = score < 1 && rest.distance !== null
  return {
    column,
    datatype: 'PIN',
    score,
    distance: off ? 1 : score === 1 ? 0 : null,
    firstEdit: off ? 0 : null,
    expected: off ? 'a digit' : null,
    tooLong: false,
    ...rest,
  }
}

function score(filled: number, formats: FormatScore[], missing: string[] = []): TemplateScore {
  return { template: 'Computer', score: filled / 7, typeMatches: true, filled, total: 7, missing, formats }
}

const fullFormats = [format('notes', 1), format('fields.PIN', 1), format('fields.BIOS password', 1)]

const matches: TemplateMatches = {
  full: { match: 'Computer', scores: [score(7, fullFormats)] },
  near: {
    match: null,
    scores: [score(5.6, [format('notes', 1), format('fields.PIN', 0.8), format('fields.BIOS password', 0.8)])],
  },
  empty: {
    match: null,
    scores: [score(5, [format('notes', 1), format('fields.PIN', 0, { distance: null }), format('fields.BIOS password', 1)], ['fields.PIN'])],
  },
  plain: { match: null, scores: [] },
}

const ok = () => jsonResponse(201, { ids: [1] })

function routes(extra: Routes = {}): Routes {
  return {
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/templates': () => jsonResponse(200, { templates: [computer] }),
    'GET /api/template-matches': () => jsonResponse(200, matches),
    'POST /api/queue': ok,
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

function rowNames(): string[] {
  const rows = document.querySelectorAll<HTMLElement>('.ag-row')
  return Array.from(rows)
    .filter((row) => row.querySelector('[col-id="name"]') !== null)
    .sort((a, b) => Number(a.getAttribute('row-index')) - Number(b.getAttribute('row-index')))
    .map((row) => row.querySelector('[col-id="name"]')?.textContent ?? '')
}

async function gridShown() {
  await screen.findByText('Full')
  await waitFor(() => expect(cell('near', 'template')).toHaveTextContent('Computer 5.60/7'))
}

function checks(fetchMock: ReturnType<typeof mockFetch>, id: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([path]) => path === `/api/datatypes/${id}/check`)
    .map(([, init]) => JSON.parse(String(init?.body)))
}

function posted(fetchMock: ReturnType<typeof mockFetch>): unknown[] {
  return fetchMock.mock.calls
    .filter(([path, init]) => path === '/api/queue' && init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init?.body)))
}

test('a cell gets the mark of its format in the template its row shows, and no other cell does', async () => {
  renderGrid(grid, { routes: routes() })
  await gridShown()

  await waitFor(() => expect(cell('full', 'fields.PIN')).toHaveClass(FORMAT_CLASSES.full))
  expect(cell('near', 'fields.PIN')).toHaveClass(FORMAT_CLASSES.partial)
  expect(cell('near', 'fields.PIN')).not.toHaveClass(FORMAT_CLASSES.full)
  expect(cell('empty', 'fields.PIN')).toHaveClass(FORMAT_CLASSES.none)
  expect(cell('near', 'fields.BIOS password')).toHaveClass(FORMAT_CLASSES.partial)

  // A column without a datatype, and a row without a template.
  for (const unmarked of [cell('full', 'fields.Other'), cell('full', 'name'), cell('plain', 'fields.PIN')]) {
    for (const name of Object.values(FORMAT_CLASSES)) {
      expect(unmarked).not.toHaveClass(name)
    }
  }
})

test('the hover text of a cell, with the numbers only for a secret and no value for an empty one', () => {
  const formats = cellFormats(matches, [computer], (column) => column === 'fields.BIOS password')
  expect(formats.get('near')?.get('fields.PIN')).toEqual({
    level: 'partial',
    hover: 'PIN: matches format 80% (1 change at position 1, expected a digit)',
    datatypeId: PIN_ID,
  })
  expect(formats.get('near')?.get('fields.BIOS password')?.hover).toBe('BIOS password: matches format 80% (1 change at position 1)')
  expect(formats.get('full')?.get('fields.PIN')?.hover).toBe('PIN: matches format')
  expect(formats.get('empty')?.get('fields.PIN')).toMatchObject({ level: 'none', hover: 'PIN: no value' })
  expect(formats.get('plain')).toBeUndefined()
  expect(formats.get('near')?.get('name')).toBeUndefined()
})

test('a value too long to check has no mark level but none, and says so', () => {
  const long = { match: null, scores: [score(5, [format('fields.PIN', 0, { distance: null, tooLong: true })])] }
  expect(cellFormats({ long }, [computer], () => false).get('long')?.get('fields.PIN')).toMatchObject({
    level: 'none',
    hover: 'PIN: too long to check',
  })
})

// AG Grid positions a popup or tooltip against the grid's offsetParent, which jsdom never sets.
function fakeOffsetParent() {
  const offsetParent = vi.spyOn(HTMLElement.prototype, 'offsetParent', 'get').mockReturnValue(document.body)
  onTestFinished(() => offsetParent.mockRestore())
}

// AG Grid hides a popup editor whose cell has an empty rect, which every rect is in jsdom.
function fakePopupLayout() {
  fakeOffsetParent()
  const rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(new DOMRect(1, 1, 100, 20))
  onTestFinished(() => rect.mockRestore())
}

// By selector: a role query walks the whole grid, too slow to wait on.
async function popupEditor<T extends HTMLElement>(tag: string): Promise<T> {
  return waitFor(() => {
    const found = document.querySelector<T>(`.ag-popup-editor ${tag}[aria-label="Input Editor"]`)
    if (!found) {
      throw new Error('No popup editor')
    }
    return found
  })
}

function liveText(): string | null | undefined {
  return document.querySelector('.ag-popup-editor [role="status"]')?.textContent
}

function columnOf(id: string) {
  return grid.columns.find((column) => column.id === id)!
}

const partial: CellFormat = { level: 'partial', hover: 'PIN: matches format 80%', datatypeId: PIN_ID }
const marked: CellMarks = { ...noCellMarks, formatOf: () => partial }

test('a cell keeps its value in the hover text, and queued and to-fill texts win', () => {
  const row = { ...grid.rows[1] } as ShownRow
  const tooltip = (column: string, marks: CellMarks = marked) =>
    columnDef(columnOf(column), false, marks).tooltip as (params: unknown) => string | undefined
  expect(tooltip('fields.PIN')({ data: row, valueFormatted: 'shown' })).toBe('shown\nPIN: matches format 80%')
  expect(tooltip('fields.BIOS password')({ data: row, valueFormatted: 'shown' })).toBe('PIN: matches format 80%')
  expect(tooltip('fields.PIN')({ data: row, valueFormatted: '' })).toBe('PIN: matches format 80%')
  const toFill = { ...marked, isToFillCell: () => true }
  expect(tooltip('fields.BIOS password', toFill)({ data: row, valueFormatted: '' })).toMatch(/^Fill this in/)
  // Cells without a datatype look as today.
  expect(tooltip('fields.PIN', noCellMarks)({ data: row, valueFormatted: 'shown' })).toBe('shown')
})

test('a queued value and a cell that cannot hold a value get no mark', () => {
  const rule = (column: string) =>
    columnDef(columnOf(column), false, marked).cellClassRules?.[FORMAT_CLASSES.partial] as (params: unknown) => boolean
  const row = { ...grid.rows[1] } as ShownRow
  expect(rule('fields.PIN')({ data: row })).toBe(true)
  const queued = { ...row, queued: { cells: new Map([['fields.PIN', { value: '1' }]]) } } as unknown as ShownRow
  expect(rule('fields.PIN')({ data: queued })).toBe(false)
  // A note has no login.uris.
  expect(rule('login.uris')({ data: row })).toBe(false)
})

test('the live text says how a value matches', () => {
  const off = { score: 0.8, firstEdit: 0, expected: 'a digit', tooLong: false }
  expect(liveFormatText(off)).toBe('matches format 80%, expected a digit at position 1')
  expect(liveFormatText({ ...off, score: 1, firstEdit: null, expected: null })).toBe('matches format')
  expect(liveFormatText({ ...off, score: 0, firstEdit: null, expected: null, tooLong: true })).toBe('too long to check')
})

test('the inline editor shows the result live, and a value off its format still saves', async () => {
  // The live check waits CHECK_DELAY_MS after each change, on the fake clock.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const { fetchMock } = renderGrid(grid, {
    routes: routes({
      [`POST /api/datatypes/${PIN_ID}/check`]: (init) =>
        jsonResponse(
          200,
          JSON.parse(String(init.body)).value === '1234'
            ? { score: 1, distance: 0, firstEdit: null, expected: null, tooLong: false }
            : { score: 0.8, distance: 1, firstEdit: 0, expected: 'a digit', tooLong: false },
        ),
    }),
  })
  await gridShown()
  fakePopupLayout()

  fireEvent.doubleClick(cell('full', 'fields.PIN'), { detail: 2 })
  const input = await popupEditor<HTMLInputElement>('input')
  expect(input.value).toBe('1234')
  await waitForPolls(() => expect(liveText()).toBe('matches format'))

  fireEvent.change(input, { target: { value: ' 1234' } })
  await waitForPolls(() => expect(liveText()).toBe('matches format 80%, expected a digit at position 1'))
  expect(checks(fetchMock, PIN_ID).at(-1)).toEqual({ value: ' 1234' })

  fireEvent.keyDown(input, { key: 'Enter' })
  await waitForPolls(() => expect(posted(fetchMock)).toHaveLength(1))
  expect(JSON.stringify(posted(fetchMock)[0])).toContain('" 1234"')
  await waitForPolls(() => expect(document.querySelector('.ag-popup-editor')).toBeNull())
})

test('notes with a datatype get the same text under a text area', async () => {
  // The live check waits CHECK_DELAY_MS after each change, on the fake clock.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  renderGrid(grid, {
    routes: routes({
      [`POST /api/datatypes/${NOTE_ID}/check`]: () =>
        jsonResponse(200, { score: 0.5, distance: 2, firstEdit: 3, expected: 'a letter', tooLong: false }),
    }),
  })
  await gridShown()
  fakePopupLayout()

  fireEvent.doubleClick(cell('full', 'notes'), { detail: 2 })
  await popupEditor<HTMLTextAreaElement>('textarea')
  await waitForPolls(() => expect(liveText()).toBe('matches format 50%, expected a letter at position 4'))
})

test('a cell without a datatype keeps the grid\'s own editor and sends no check', async () => {
  const { fetchMock } = renderGrid(grid, { routes: routes() })
  await gridShown()

  fireEvent.doubleClick(cell('full', 'fields.Other'), { detail: 2 })
  await waitFor(() => expect(cell('full', 'fields.Other').querySelector('input')).not.toBeNull())
  expect(document.querySelector('.ag-popup-editor')).toBeNull()
  expect(fetchMock.mock.calls.filter(([path]) => String(path).includes('/check'))).toEqual([])
})

test('the secret dialog shows the result live and never blocks the save', async () => {
  // The live check waits CHECK_DELAY_MS after each change, on the fake clock.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const { fetchMock } = renderGrid(grid, {
    routes: routes({
      [`POST /api/datatypes/${PIN_ID}/check`]: () =>
        jsonResponse(200, { score: 0.8, distance: 1, firstEdit: 0, expected: 'a digit', tooLong: false }),
    }),
  })
  await gridShown()

  fireEvent.doubleClick(cell('near', 'fields.BIOS password'), { detail: 2 })
  const dialog = await screen.findByRole('dialog')
  // An empty dialog has nothing to check yet.
  expect(within(dialog).queryByRole('status')).toBeNull()
  fireEvent.change(within(dialog).getByLabelText('New value'), { target: { value: ' 1234' } })

  expect((await waitForPolls(() => within(dialog).getByRole('status'))).textContent).toBe('matches format 80%, expected a digit at position 1')
  expect(checks(fetchMock, PIN_ID)).toEqual([{ value: ' 1234' }])
  const save = within(dialog).getByRole('button', { name: 'Save' })
  expect(save).toBeEnabled()
  fireEvent.click(save)
  await waitForPolls(() => expect(posted(fetchMock)).toHaveLength(1))
})

test('the secret dialog of a cell without a datatype sends no check', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const noDatatype: Template = { ...computer, coordinates: computer.coordinates.slice(0, 2) }
  const { fetchMock } = renderGrid(grid, {
    routes: routes({
      'GET /api/templates': () => jsonResponse(200, { templates: [noDatatype] }),
      'GET /api/template-matches': () =>
        jsonResponse(200, { near: { match: null, scores: [score(5.6, [format('fields.PIN', 0.8)])] } }),
    }),
  })
  await screen.findByText('Full')
  await waitFor(() => expect(cell('near', 'template')).toHaveTextContent('Computer 5.60/7'))

  fireEvent.doubleClick(cell('near', 'fields.BIOS password'), { detail: 2 })
  const dialog = await screen.findByRole('dialog')
  fireEvent.change(within(dialog).getByLabelText('New value'), { target: { value: 'x' } })
  await advanceClock(400)
  expect(fetchMock.mock.calls.filter(([path]) => String(path).includes('/check'))).toEqual([])
})

test('every URI in the list dialog shows its own result', async () => {
  // The live check waits CHECK_DELAY_MS after each change, on the fake clock.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const site: Template = { name: 'Computer', type: 1, coordinates: [{ kind: 'path', path: 'login.uris', datatype: PIN_ID }] }
  const row = { id: 'site', revisionDate: '2026-10-05T10:00:00Z', cells: { name: 'Site', type: 'login', 'login.uris': ['a', 'b'] } }
  renderGrid(grid, {
    routes: routes({
      'GET /api/grid': () => jsonResponse(200, { ...grid, rows: [row] }),
      'GET /api/templates': () => jsonResponse(200, { templates: [site] }),
      'GET /api/template-matches': () => jsonResponse(200, { site: { match: null, scores: [score(5.6, [format('login.uris', 0.8)])] } }),
      [`POST /api/datatypes/${PIN_ID}/check`]: () =>
        jsonResponse(200, { score: 0.8, distance: 1, firstEdit: 0, expected: 'a digit', tooLong: false }),
    }),
  })
  await screen.findByText('Site')
  await waitForPolls(() => expect(cell('site', 'template')).toHaveTextContent('Computer 5.60/7'))

  fireEvent.doubleClick(cell('site', 'login.uris'), { detail: 2 })
  const dialog = await screen.findByRole('dialog')
  await waitForPolls(() => expect(within(dialog).getAllByRole('status')).toHaveLength(2))
})

test('a refused check shows nothing and blocks nothing', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  renderGrid(grid, { routes: routes({ [`POST /api/datatypes/${PIN_ID}/check`]: () => jsonResponse(404, { detail: 'No such datatype' }) }) })
  await gridShown()

  fireEvent.doubleClick(cell('near', 'fields.BIOS password'), { detail: 2 })
  const dialog = await screen.findByRole('dialog')
  fireEvent.change(within(dialog).getByLabelText('New value'), { target: { value: 'x' } })
  await advanceClock(400)
  expect(within(dialog).queryByRole('status')).toBeNull()
  expect(within(dialog).getByRole('button', { name: 'Save' })).toBeEnabled()
})

test('the Format filter keeps the rows off their format, or only the partial ones', async () => {
  renderGrid(grid, { routes: routes() })
  await gridShown()
  const names = () => rowNames().sort()
  expect(names()).toEqual(['Empty', 'Full', 'Near', 'Plain'])

  async function choose(option: string) {
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Format' }))
    fireEvent.click(await screen.findByRole('option', { name: option }))
  }

  await choose('Off their format')
  await waitFor(() => expect(names()).toEqual(['Empty', 'Near']))
  await choose('Partial')
  await waitFor(() => expect(names()).toEqual(['Near']))
  await choose('All')
  await waitFor(() => expect(names()).toEqual(['Empty', 'Full', 'Near', 'Plain']))
})

test('sorting the template column sorts by the score', async () => {
  // The labels sort the other way: "Computer 5.60/7" after "Computer 10.50/21", which is 0.5.
  const low = { match: null, scores: [{ ...score(10.5, []), total: 21, score: 0.5 }] }
  renderGrid(grid, { routes: routes({ 'GET /api/template-matches': () => jsonResponse(200, { ...matches, empty: low }) }) })
  await screen.findByText('Full')
  await waitFor(() => expect(cell('empty', 'template')).toHaveTextContent('Computer 10.50/21'))

  const header = document.querySelector<HTMLElement>('.ag-header-cell[col-id="template"] .ag-header-cell-label')!
  fireEvent.click(header)
  await waitFor(() => expect(rowNames()).toEqual(['Plain', 'Empty', 'Near', 'Full']))
  fireEvent.click(header)
  await waitFor(() => expect(rowNames()).toEqual(['Full', 'Near', 'Empty', 'Plain']))
})

test('hovering a near miss in the grid lists the format, without the expected text for a secret', async () => {
  renderGrid(grid, { routes: routes() })
  await gridShown()
  const template = cell('near', 'template')
  fakeOffsetParent()
  fireEvent.mouseOver(template)
  fireEvent.mouseEnter(template)
  fireEvent.mouseMove(template)
  const tooltip = await screen.findByText(/^Off format for Computer:/, {}, { timeout: 3000 })
  expect(tooltip.textContent).toBe(
    [
      'Off format for Computer:',
      '• PIN: matches format 80% (1 change at position 1, expected a digit)',
      '• BIOS password: matches format 80% (1 change at position 1)',
    ].join('\n'),
  )
})
