import { QueryClientProvider } from '@tanstack/react-query'
import { useTheme } from '@mui/material'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App from '../App.tsx'
import { jsonResponse, mockFetch, newQueryClient, renderWithQuery, waitForPolls } from '../test-utils.tsx'
import { ColorModeProvider } from '../theme/colorMode.tsx'
import { LAYOUT_STORAGE_KEY, LayoutProvider } from '../theme/layout.tsx'
import type { PassphraseOptions } from '../api/generator.ts'
import { DEFAULT_PASSWORD, optionsProblem } from '../shared/generatorRules.ts'
import { describeOptions } from '../grid/ownGenerator.ts'
import SettingsPage from './SettingsPage.tsx'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  localStorage.clear()
})

const WORDS: PassphraseOptions = { type: 'passphrase', words: 4, separator: '-', capitalize: false, includeNumber: false }

function bodies(fetchMock: ReturnType<typeof mockFetch>, method: string, path: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([url, init]) => String(url) === path && init?.method === method)
    .map(([, init]) => JSON.parse(String(init?.body)))
}

function renderPage(put: (init: RequestInit) => Response = () => jsonResponse(202, { entryId: 3 })) {
  const fetchMock = mockFetch({
    'GET /api/generator': () => jsonResponse(200, { default: DEFAULT_PASSWORD, items: {}, saving: false }),
    'POST /api/generator/example': () => jsonResponse(200, { value: 'example-value' }),
    'PUT /api/generator/default': put,
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'PUT /api/settings/display': (init) => jsonResponse(200, JSON.parse(String(init.body))),
  })
  renderWithQuery(<SettingsPage />)
  return fetchMock
}

// The select is off until the stored choice has loaded.
async function choose(label: string, option: RegExp) {
  const select = await screen.findByRole('combobox', { name: label })
  await waitFor(() => expect(select).not.toHaveAttribute('aria-disabled'))
  fireEvent.mouseDown(select)
  fireEvent.click(await screen.findByRole('option', { name: option }))
}

const TIME = String.raw`\d{2}:\d{2}:\d{2}`

test('Display lists each date format with the current time in it', async () => {
  mockFetch({
    'GET /api/generator': () => jsonResponse(200, { default: DEFAULT_PASSWORD, items: {}, saving: false }),
    'POST /api/generator/example': () => jsonResponse(200, { value: 'example-value' }),
    'GET /api/settings/display': () => jsonResponse(200, { dateFormat: 'europe', hideToolNote: false }),
  })
  renderWithQuery(<SettingsPage />)
  await screen.findByRole('heading', { name: 'Password generator' })
  const headings = screen.getAllByRole('heading', { level: 3 })
  expect(headings.map((heading) => heading.textContent)).toEqual(['Display', 'Password generator', 'Delete data'])
  const select = screen.getByRole('combobox', { name: 'Date and time format' })
  await waitFor(() => expect(select).toHaveTextContent(new RegExp(`^Europe \\(\\d{2}/\\d{2}/\\d{4} ${TIME}\\)$`)))
  fireEvent.mouseDown(select)
  const options = await screen.findAllByRole('option')
  const names = options.map((option) => option.textContent ?? '')
  expect(names).toHaveLength(5)
  expect(names[0]).toMatch(/^System \(.+\)$/)
  expect(names[1]).toMatch(new RegExp(`^ISO 8601 \\(\\d{4}-\\d{2}-\\d{2} ${TIME}\\)$`))
  expect(names[2]).toMatch(new RegExp(`^Europe \\(\\d{2}/\\d{2}/\\d{4} ${TIME}\\)$`))
  expect(names[3]).toMatch(new RegExp(`^Europe \\(dots\\) \\(\\d{2}\\.\\d{2}\\.\\d{4} ${TIME}\\)$`))
  expect(names[4]).toMatch(new RegExp(`^USA \\(\\d{1,2}/\\d{1,2}/\\d{4}, \\d{1,2}:\\d{2}:\\d{2} [AP]M\\)$`))
})

test('picking a date format saves it at once', async () => {
  const fetchMock = renderPage()
  await choose('Date and time format', /^ISO 8601/)
  await waitFor(() => expect(bodies(fetchMock, 'PUT', '/api/settings/display')).toEqual([{ dateFormat: 'iso' }]))
  expect(screen.getByRole('combobox', { name: 'Date and time format' })).toHaveTextContent(/^ISO 8601/)
})

test('a refused date format says so and shows the stored one again', async () => {
  mockFetch({
    'GET /api/generator': () => jsonResponse(200, { default: DEFAULT_PASSWORD, items: {}, saving: false }),
    'POST /api/generator/example': () => jsonResponse(200, { value: 'example-value' }),
    'GET /api/settings/display': () => jsonResponse(200, { dateFormat: 'system', hideToolNote: false }),
    'PUT /api/settings/display': () => jsonResponse(500, { detail: 'disk full' }),
  })
  renderWithQuery(<SettingsPage />)
  await choose('Date and time format', /^USA/)
  expect(await screen.findByText('Nothing was saved: disk full')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('combobox', { name: 'Date and time format' })).toHaveTextContent(/^System/))
})

const HIDE_LABEL = "Hide bwtidy's own note"

test('the hide checkbox loads the stored choice and has a helper text', async () => {
  mockFetch({
    'GET /api/generator': () => jsonResponse(200, { default: DEFAULT_PASSWORD, items: {}, saving: false }),
    'POST /api/generator/example': () => jsonResponse(200, { value: 'example-value' }),
    'GET /api/settings/display': () => jsonResponse(200, { dateFormat: 'iso', hideToolNote: true }),
  })
  renderWithQuery(<SettingsPage />)
  const box = await screen.findByRole('checkbox', { name: HIDE_LABEL })
  await waitFor(() => expect(box).toBeChecked())
  expect(screen.getByText("The note keeps your templates and settings. Hidden only in the Vault grid.")).toBeInTheDocument()
  expect(screen.getByRole('combobox', { name: 'Date and time format' })).toHaveTextContent(/^ISO 8601/)
})

test('ticking the hide checkbox saves only hideToolNote at once', async () => {
  const fetchMock = renderPage()
  const box = await screen.findByRole('checkbox', { name: HIDE_LABEL })
  await waitFor(() => expect(box).not.toBeDisabled())
  expect(box).not.toBeChecked()
  fireEvent.click(box)
  await waitFor(() => expect(bodies(fetchMock, 'PUT', '/api/settings/display')).toEqual([{ hideToolNote: true }]))
  expect(screen.getByRole('checkbox', { name: HIDE_LABEL })).toBeChecked()
})

test('a refused hide choice says so and shows the stored one again', async () => {
  mockFetch({
    'GET /api/generator': () => jsonResponse(200, { default: DEFAULT_PASSWORD, items: {}, saving: false }),
    'POST /api/generator/example': () => jsonResponse(200, { value: 'example-value' }),
    'GET /api/settings/display': () => jsonResponse(200, { dateFormat: 'system', hideToolNote: false }),
    'PUT /api/settings/display': () => jsonResponse(500, { detail: 'disk full' }),
  })
  renderWithQuery(<SettingsPage />)
  const box = await screen.findByRole('checkbox', { name: HIDE_LABEL })
  await waitFor(() => expect(box).not.toBeDisabled())
  fireEvent.click(box)
  expect(await screen.findByText('Nothing was saved: disk full')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('checkbox', { name: HIDE_LABEL })).not.toBeChecked())
})

test('the page shows the default, and Save stays off until something changed', async () => {
  renderPage()
  expect(await screen.findByRole('heading', { name: 'Password generator' })).toBeInTheDocument()
  expect(screen.getByRole('radio', { name: 'Password' })).toBeChecked()
  expect(screen.getByRole('spinbutton', { name: 'Length' })).toHaveValue(24)
  for (const name of ['A-Z', 'a-z', '0-9', 'Special']) {
    expect(screen.getByRole('checkbox', { name })).toBeChecked()
  }
  expect(screen.getByRole('spinbutton', { name: 'Minimum numbers' })).toHaveValue(1)
  expect(screen.getByRole('spinbutton', { name: 'Minimum special' })).toHaveValue(1)
  expect(screen.getByRole('checkbox', { name: 'Avoid ambiguous characters' })).not.toBeChecked()
  expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
})

test('Save sends the options of a password', async () => {
  const fetchMock = renderPage()
  fireEvent.change(await screen.findByRole('spinbutton', { name: 'Length' }), { target: { value: '12' } })
  fireEvent.click(screen.getByRole('checkbox', { name: 'Special' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'Avoid ambiguous characters' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(bodies(fetchMock, 'PUT', '/api/generator/default')).toHaveLength(1))
  expect(bodies(fetchMock, 'PUT', '/api/generator/default')[0]).toEqual({
    ...DEFAULT_PASSWORD,
    length: 12,
    special: false,
    ambiguous: true,
  })
})

test('Passphrase shows its own options and Save sends them', async () => {
  const fetchMock = renderPage()
  fireEvent.click(await screen.findByRole('radio', { name: 'Passphrase' }))
  expect(screen.queryByRole('spinbutton', { name: 'Length' })).toBeNull()
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Number of words' }), { target: { value: '5' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Word separator' }), { target: { value: '' } })
  fireEvent.click(screen.getByRole('checkbox', { name: 'Capitalize' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'Include number' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(bodies(fetchMock, 'PUT', '/api/generator/default')).toHaveLength(1))
  expect(bodies(fetchMock, 'PUT', '/api/generator/default')[0]).toEqual({
    type: 'passphrase',
    words: 5,
    separator: '',
    capitalize: true,
    includeNumber: true,
  })
})

test('switching the type back keeps what was set for it', async () => {
  renderPage()
  fireEvent.change(await screen.findByRole('spinbutton', { name: 'Length' }), { target: { value: '30' } })
  fireEvent.click(screen.getByRole('radio', { name: 'Passphrase' }))
  fireEvent.click(screen.getByRole('radio', { name: 'Password' }))
  expect(screen.getByRole('spinbutton', { name: 'Length' })).toHaveValue(30)
})

// The example comes EXAMPLE_DELAY_MS after the last change, the tests move a fake clock to it.
test('options outside the limits say why, turn Save off and ask for no example', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const fetchMock = renderPage()
  const example = await screen.findByTestId('generator-example')
  await waitForPolls(() => within(example).getByText('example-value'))
  const asked = bodies(fetchMock, 'POST', '/api/generator/example').length
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Length' }), { target: { value: '4' } })
  expect(await screen.findByText('The length must be 5 to 128.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  expect(screen.queryByTestId('generator-example')).toBeNull()
  await act(() => vi.advanceTimersByTimeAsync(400))
  expect(bodies(fetchMock, 'POST', '/api/generator/example')).toHaveLength(asked)
})

test('the refresh button asks for another example with the options on the form', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const values = ['first-value', 'second-value']
  const fetchMock = mockFetch({
    'GET /api/generator': () => jsonResponse(200, { default: WORDS, items: {}, saving: false }),
    'POST /api/generator/example': () => jsonResponse(200, { value: values.shift() }),
  })
  renderWithQuery(<SettingsPage />)
  expect(await waitForPolls(() => screen.getByText('first-value'))).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Another example' }))
  expect(await waitForPolls(() => screen.getByText('second-value'))).toBeInTheDocument()
  expect(bodies(fetchMock, 'POST', '/api/generator/example')).toEqual([WORDS, WORDS])
})

test('an example that fails says so', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockFetch({
    'GET /api/generator': () => jsonResponse(200, { default: WORDS, items: {}, saving: false }),
    'POST /api/generator/example': () => jsonResponse(502, { detail: 'bw serve generated no example' }),
  })
  renderWithQuery(<SettingsPage />)
  expect(await waitForPolls(() => screen.getByText('Could not get an example'))).toBeInTheDocument()
})

test('a refused save keeps the form and says why', async () => {
  renderPage(() => jsonResponse(422, { detail: 'generator options length: too long' }))
  fireEvent.change(await screen.findByRole('spinbutton', { name: 'Length' }), { target: { value: '12' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  expect(await screen.findByText(/Nothing was saved: generator options length/)).toBeInTheDocument()
  expect(screen.getByRole('spinbutton', { name: 'Length' })).toHaveValue(12)
})

test('a save under way and stored settings that are not valid show', async () => {
  mockFetch({
    'GET /api/generator': () =>
      jsonResponse(200, { default: DEFAULT_PASSWORD, items: {}, saving: true, error: 'generator settings default.length: too small' }),
    'POST /api/generator/example': () => jsonResponse(200, { value: 'example-value' }),
  })
  renderWithQuery(<SettingsPage />)
  expect(await screen.findByText('Saving')).toBeInTheDocument()
  expect(screen.getByText(/next save replaces them: generator settings default.length/)).toBeInTheDocument()
})

test('More actions opens the Settings page', async () => {
  mockFetch({
    'GET /api/session': () =>
      jsonResponse(200, {
        state: 'unlocked',
        mcpEnabled: false,
        bwLogin: { loggedIn: true, server: 'https://vault.example.test', email: 'user@example.test' },
      }),
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
    'GET /api/health': () => jsonResponse(200, { status: 'ok', version: '261028 abc1234' }),
    'GET /api/generator': () => jsonResponse(200, { default: DEFAULT_PASSWORD, items: {}, saving: false }),
    'POST /api/generator/example': () => jsonResponse(200, { value: 'example-value' }),
  })
  renderWithQuery(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'More actions' }))
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Settings' }))
  expect(await screen.findByRole('heading', { name: 'Password generator' })).toBeInTheDocument()
})

function stubMatchMedia() {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
}

function ThemeProbe() {
  const theme = useTheme()
  return <p>spacing1: {theme.spacing(1)}</p>
}

test('picking Compact writes bwtidy.layout and shrinks the theme spacing to 5px', async () => {
  stubMatchMedia()
  mockFetch({
    'GET /api/generator': () => jsonResponse(200, { default: DEFAULT_PASSWORD, items: {}, saving: false }),
    'POST /api/generator/example': () => jsonResponse(200, { value: 'example-value' }),
    'GET /api/settings/display': () => jsonResponse(200, { dateFormat: 'system', hideToolNote: false, rareFieldsMax: 0 }),
  })
  render(
    <QueryClientProvider client={newQueryClient()}>
      <LayoutProvider>
        <ColorModeProvider>
          <ThemeProbe />
          <SettingsPage />
        </ColorModeProvider>
      </LayoutProvider>
    </QueryClientProvider>,
  )
  expect(screen.getByText('spacing1: 8px')).toBeInTheDocument()
  const select = await screen.findByRole('combobox', { name: 'Layout' })
  fireEvent.mouseDown(select)
  fireEvent.click(await screen.findByRole('option', { name: 'Compact' }))
  await waitFor(() => expect(screen.getByText('spacing1: 5px')).toBeInTheDocument())
  expect(localStorage.getItem(LAYOUT_STORAGE_KEY)).toBe('compact')
  expect(screen.getByText('Saved in this browser only.')).toBeInTheDocument()
})

test('describeOptions names the default in a few words', () => {
  expect(describeOptions(DEFAULT_PASSWORD)).toBe('24 characters')
  expect(describeOptions(WORDS)).toBe('4 words')
})

test.each([
  [{ ...DEFAULT_PASSWORD, length: 4 }, 'The length must be 5 to 128.'],
  [{ ...DEFAULT_PASSWORD, length: 129 }, 'The length must be 5 to 128.'],
  [{ ...DEFAULT_PASSWORD, length: Number.NaN }, 'The length must be 5 to 128.'],
  [{ ...DEFAULT_PASSWORD, uppercase: false, lowercase: false, number: false, special: false }, 'Pick at least one of A-Z, a-z, 0-9 and special.'],
  [{ ...DEFAULT_PASSWORD, minNumber: 10 }, 'A minimum must be 0 to 9.'],
  [{ ...DEFAULT_PASSWORD, length: 8, minNumber: 4, minSpecial: 4 }, 'The minimums need at least 10 characters.'],
  [{ ...WORDS, words: 2 }, 'The number of words must be 3 to 20.'],
  [{ ...WORDS, words: 21 }, 'The number of words must be 3 to 20.'],
  [{ ...WORDS, separator: '--' }, 'The separator must be one character or none.'],
])('optionsProblem refuses what the backend refuses', (options, problem) => {
  expect(optionsProblem(options)).toBe(problem)
})

test('optionsProblem takes what the backend takes', () => {
  expect(optionsProblem(DEFAULT_PASSWORD)).toBeNull()
  expect(optionsProblem({ ...DEFAULT_PASSWORD, length: 5, special: false, minSpecial: 9 })).toBeNull()
  expect(optionsProblem({ ...WORDS, separator: '' })).toBeNull()
})

const EMAIL = 'user@example.test'
const SERVER = 'https://vaultwarden'

const unlockedSession = {
  'GET /api/session': () =>
    jsonResponse(200, { state: 'unlocked', mcpEnabled: false, bwLogin: { loggedIn: true, server: SERVER, email: EMAIL } }),
}

function renderDeleteSection(deleteData: (init: RequestInit) => Response) {
  const fetchMock = mockFetch({
    ...unlockedSession,
    'GET /api/generator': () => jsonResponse(200, { default: DEFAULT_PASSWORD, items: {}, saving: false }),
    'POST /api/generator/example': () => jsonResponse(200, { value: 'example-value' }),
    'POST /api/account/delete-data': deleteData,
  })
  renderWithQuery(<SettingsPage />)
  return fetchMock
}

async function openDeleteDialog(name: string) {
  const section = await screen.findByRole('region', { name: 'Delete data' })
  const button = within(section).getByRole('button', { name })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
  return await screen.findByRole('dialog', { name })
}

test('Delete my data needs the email typed, then deletes and shows the counts', async () => {
  const fetchMock = renderDeleteSection(() => jsonResponse(200, { backups: 3, changeLog: 1, queue: 2, keptBackups: [] }))
  const dialog = await openDeleteDialog('Delete my data')
  expect(within(dialog).getByText('every backup, the newest one too')).toBeInTheDocument()
  const confirm = within(dialog).getByRole('button', { name: 'Delete my data' })
  const field = within(dialog).getByRole('textbox', { name: `Type ${EMAIL} to confirm` })
  expect(confirm).toBeDisabled()
  fireEvent.change(field, { target: { value: 'user@example' } })
  expect(confirm).toBeDisabled()
  fireEvent.change(field, { target: { value: 'User@Example.TEST' } })
  expect(confirm).not.toBeDisabled()
  // Nothing is pending, so there is no choice to make.
  expect(within(dialog).queryByRole('radio')).toBeNull()
  fireEvent.click(confirm)
  expect(await screen.findByText('Deleted 3 backups, 1 change log row, 2 queue entries.')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(bodies(fetchMock, 'POST', '/api/account/delete-data')).toEqual([{ pending: null }])
  const [, init] = fetchMock.mock.calls.find(([url]) => String(url) === '/api/account/delete-data') ?? []
  expect(init?.headers).toMatchObject({ 'X-Bwtidy-Request': '1' })
})

test('the pending choice shows only after a 409 that names pending entries', async () => {
  const answers = [
    jsonResponse(409, { pending: 2, mcpPending: 1 }),
    jsonResponse(200, { backups: 1, changeLog: 0, queue: 3, keptBackups: [] }),
  ]
  const fetchMock = renderDeleteSection(() => answers.shift() ?? jsonResponse(500))
  const dialog = await openDeleteDialog('Delete my data')
  expect(within(dialog).queryByRole('radio')).toBeNull()
  fireEvent.change(within(dialog).getByRole('textbox', { name: `Type ${EMAIL} to confirm` }), { target: { value: EMAIL } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete my data' }))
  const write = await within(dialog).findByRole('radio', {
    name: 'Write the 2 pending edits first. Your 1 MCP proposal cannot be written without approval and stay pending.',
  })
  expect(write).toBeChecked()
  fireEvent.click(within(dialog).getByRole('radio', { name: 'Drop them: 2 pending edits and 1 MCP proposal are deleted unwritten.' }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete my data' }))
  expect(await screen.findByText('Deleted 1 backup, 0 change log rows, 3 queue entries.')).toBeInTheDocument()
  expect(bodies(fetchMock, 'POST', '/api/account/delete-data')).toEqual([{ pending: null }, { pending: 'drop' }])
})

test('a refused deletion says why and keeps the dialog open', async () => {
  renderDeleteSection(() => jsonResponse(409, { detail: 'a write is running, try again' }))
  const dialog = await openDeleteDialog('Delete my data')
  fireEvent.change(within(dialog).getByRole('textbox', { name: `Type ${EMAIL} to confirm` }), { target: { value: EMAIL } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete my data' }))
  expect(await within(dialog).findByText('a write is running, try again')).toBeInTheDocument()
  expect(within(dialog).queryByRole('radio')).toBeNull()
})

test('Delete my account forgets the account and shows the account form', async () => {
  localStorage.setItem('bwtidy.account', JSON.stringify({ server: SERVER, email: EMAIL }))
  const fetchMock = mockFetch({
    ...unlockedSession,
    'GET /gateway/account': () => jsonResponse(200, { account: { server: SERVER, email: EMAIL }, running: true }),
    'POST /gateway/account': () => jsonResponse(200, { account: { server: SERVER, email: EMAIL }, running: true }),
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
    'GET /api/health': () => jsonResponse(200, { status: 'ok', version: '261028 abc1234' }),
    'GET /api/generator': () => jsonResponse(200, { default: DEFAULT_PASSWORD, items: {}, saving: false }),
    'POST /api/generator/example': () => jsonResponse(200, { value: 'example-value' }),
    'POST /api/account/delete': () => jsonResponse(204),
    'DELETE /gateway/account': () => jsonResponse(204),
  })
  renderWithQuery(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'More actions' }))
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Settings' }))
  const dialog = await openDeleteDialog('Delete my account')
  expect(within(dialog).getByText('the account itself')).toBeInTheDocument()
  const confirm = within(dialog).getByRole('button', { name: 'Delete my account' })
  expect(confirm).toBeDisabled()
  fireEvent.change(within(dialog).getByRole('textbox', { name: `Type ${EMAIL} to confirm` }), { target: { value: EMAIL } })
  fireEvent.click(confirm)

  expect(await screen.findByRole('button', { name: 'Continue' })).toBeInTheDocument()
  expect(bodies(fetchMock, 'POST', '/api/account/delete')).toEqual([{ pending: null }])
  expect(fetchMock).toHaveBeenCalledWith('/gateway/account', expect.objectContaining({ method: 'DELETE' }))
  expect(localStorage.getItem('bwtidy.account')).toBeNull()
})
