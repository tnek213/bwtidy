import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App.tsx'
import { CURRENT_NAV_CLASS } from './appbar/NavButton.tsx'
import { setReloadPage } from './shared/account.ts'
import { COLOR_MODE_COOKIE, ColorModeProvider } from './theme/colorMode.tsx'
import { jsonResponse, mockFetch, renderWithQuery } from './test-utils.tsx'

const TITLE_NAME = 'bwtidy, go to the Vault'
const BOX = 'Also log out of Bitwarden (the next login asks for the new device code again)'
const SERVER = 'https://vaultwarden'
const EMAIL = 'user@example.test'

let reload = vi.fn()

beforeEach(() => {
  localStorage.clear()
  reload = vi.fn()
  setReloadPage(reload)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// The gateway with the browser's account open and its worker running.
const gatewayRoutes = {
  'GET /gateway/account': () => jsonResponse(200, { account: { server: SERVER, email: EMAIL }, running: true }),
  'POST /gateway/account': () => jsonResponse(200, { account: { server: SERVER, email: EMAIL }, running: true }),
}

function posts(fetchMock: ReturnType<typeof mockFetch>, path: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([input, init]) => String(input) === path && init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init?.body)))
}

test('shows the login page when locked', async () => {
  mockFetch({ 'GET /api/session': () => jsonResponse(200, { state: 'locked', mcpEnabled: false }) })
  renderWithQuery(<App />)
  expect(await screen.findByLabelText(/Master password/)).toBeInTheDocument()
})

const unlockedRoutes = {
  'GET /api/session': () =>
    jsonResponse(200, {
      state: 'unlocked',
      mcpEnabled: false,
      bwLogin: { loggedIn: true, server: 'https://vault.example.test', email: 'user@example.test' },
    }),
  'GET /api/queue?status=pending': () => jsonResponse(200, []),
  'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
  'GET /api/health': () => jsonResponse(200, { status: 'ok', version: '261028 abc1234' }),
}

async function openLogoutDialog() {
  fireEvent.click(await screen.findByRole('button', { name: 'Log out' }))
  return await screen.findByRole('dialog', { name: 'Log out' })
}

test('the title opens the Vault page and cannot be selected, the email can', async () => {
  mockFetch({
    ...gatewayRoutes,
    ...unlockedRoutes,
    'GET /api/templates': () => jsonResponse(200, { templates: [], saving: false }),
    'GET /api/datatypes': () => jsonResponse(200, { datatypes: [], saving: false }),
    'GET /api/queue/auto-commit': () =>
      jsonResponse(200, { afterSeconds: 10, maxEntries: 20, pending: 0, committing: false, nextCommitAt: null }),
  })
  renderWithQuery(<App />)
  const vault = await screen.findByRole('button', { name: 'Vault' })
  expect(vault).toHaveAttribute('aria-current', 'page')

  fireEvent.click(screen.getByRole('button', { name: 'Types' }))
  expect(await screen.findByRole('tab', { name: 'Datatypes' })).toBeInTheDocument()
  expect(vault).not.toHaveAttribute('aria-current')

  const title = screen.getByRole('button', { name: TITLE_NAME })
  expect(within(screen.getByRole('heading', { level: 1 })).getByRole('button', { name: TITLE_NAME })).toBe(title)
  expect(title).toHaveStyle({ userSelect: 'none', cursor: 'pointer' })
  fireEvent.click(title)

  await waitFor(() => expect(vault).toHaveAttribute('aria-current', 'page'))
  expect(screen.queryByRole('tab', { name: 'Datatypes' })).toBeNull()

  const email = screen.getByText(/user@example.test/)
  expect(window.getComputedStyle(email).userSelect).not.toBe('none')
  expect(email.closest('button')).toBeNull()
})

test('the app bar shows the build commit faintly after the title, with the full version on hover', async () => {
  mockFetch({
    ...gatewayRoutes,
    ...unlockedRoutes,
    'GET /api/health': () => jsonResponse(200, { status: 'ok', version: '0.1.0-alpha.1 (261116 d475d7f-dirty)' }),
  })
  renderWithQuery(<App />)
  const commit = await screen.findByText('d475d7f-dirty')
  expect(commit).toHaveAttribute('title', '0.1.0-alpha.1 (261116 d475d7f-dirty)')
  expect(commit).toHaveStyle({ opacity: '0.35', fontSize: '11px' })
  const title = screen.getByRole('button', { name: TITLE_NAME })
  expect(commit.closest('button')).toBeNull()
  expect(commit.parentElement).toBe(title.parentElement)
  expect(title.compareDocumentPosition(commit) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

test('log out asks first, with the Bitwarden box unchecked, and cancel keeps the session', async () => {
  const fetchMock = mockFetch({ ...gatewayRoutes, ...unlockedRoutes, 'DELETE /api/session': () => jsonResponse(204) })
  renderWithQuery(<App />)
  expect(await screen.findByRole('heading', { name: /^bwtidy/ })).toBeInTheDocument()
  expect(screen.getByRole('main')).toBeInTheDocument()

  const dialog = await openLogoutDialog()
  expect(dialog).toHaveTextContent('Log out of bwtidy. Unsaved changes are written first.')
  expect(within(dialog).getByRole('checkbox', { name: BOX })).not.toBeChecked()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(screen.getByRole('heading', { name: /^bwtidy/ })).toBeInTheDocument()
  expect(fetchMock).not.toHaveBeenCalledWith('/api/session', expect.objectContaining({ method: 'DELETE' }))
})

test('log out with the box unchecked ends the session only and shows the unlock of the same account', async () => {
  const fetchMock = mockFetch({ ...gatewayRoutes, ...unlockedRoutes, 'DELETE /api/session': () => jsonResponse(204) })
  renderWithQuery(<App />)

  const dialog = await openLogoutDialog()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Log out' }))

  expect(await screen.findByLabelText(/Master password/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Unlock' })).toBeInTheDocument()
  expect(screen.getByText(`Account ${EMAIL} (vaultwarden)`)).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/api/session', expect.objectContaining({ method: 'DELETE' }))
  expect(fetchMock).not.toHaveBeenCalledWith('/api/bw-logout', expect.anything())
  // the worker exits with its session, the account is opened again for the next unlock
  expect(posts(fetchMock, '/gateway/account')).toEqual([{ server: SERVER, email: EMAIL, create: false }])
})

test('log out with the box checked logs out of Bitwarden and shows the Bitwarden login page', async () => {
  const fetchMock = mockFetch({ ...gatewayRoutes, ...unlockedRoutes, 'POST /api/bw-logout': () => jsonResponse(204) })
  renderWithQuery(<App />)

  const dialog = await openLogoutDialog()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: BOX }))
  expect(fetchMock).not.toHaveBeenCalledWith('/api/bw-logout', expect.anything())
  fireEvent.click(within(dialog).getByRole('button', { name: 'Log out' }))

  expect(await screen.findByRole('button', { name: 'Log in' })).toBeInTheDocument()
  expect(screen.getByLabelText(/Email/)).toHaveValue('user@example.test')
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/bw-logout',
    expect.objectContaining({ method: 'POST', headers: expect.objectContaining({ 'X-Bwtidy-Request': '1' }) }),
  )
  expect(fetchMock).not.toHaveBeenCalledWith('/api/session', expect.objectContaining({ method: 'DELETE' }))
  expect(posts(fetchMock, '/gateway/account')).toHaveLength(1)
})

test('log out of Bitwarden after the worker exited shows the unlock with a note, logging nothing out', async () => {
  let gone = true
  const fetchMock = mockFetch({
    ...gatewayRoutes,
    ...unlockedRoutes,
    'POST /api/bw-logout': () => {
      if (gone) {
        gone = false
        return jsonResponse(401, { detail: 'No account' })
      }
      // the account's next worker runs no session, and only a session logs the CLI out
      return jsonResponse(401, { detail: 'Not logged in' })
    },
  })
  renderWithQuery(<App />)

  const dialog = await openLogoutDialog()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: BOX }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Log out' }))

  expect(await screen.findByText('Unlock first to log out of Bitwarden')).toBeInTheDocument()
  expect(screen.getByLabelText(/Master password/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Log in' })).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(fetchMock.mock.calls.filter(([input]) => String(input) === '/api/bw-logout')).toHaveLength(2)
  expect(posts(fetchMock, '/gateway/account')).toEqual([{ server: SERVER, email: EMAIL, create: false }])
})

test('log out of Bitwarden when no worker runs any more shows the unlock with a note', async () => {
  let running = true
  const fetchMock = mockFetch({
    ...unlockedRoutes,
    'GET /gateway/account': () => jsonResponse(200, { ...stopped(true), running }),
    'POST /gateway/account': () => jsonResponse(200, { ...stopped(true), running }),
    'POST /api/bw-logout': () => {
      running = false
      return jsonResponse(401, { detail: 'No account' })
    },
  })
  renderWithQuery(<App />)

  const dialog = await openLogoutDialog()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: BOX }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Log out' }))

  expect(await screen.findByText('Unlock first to log out of Bitwarden')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(fetchMock.mock.calls.filter(([input]) => String(input) === '/api/bw-logout')).toHaveLength(2)
})

test('log out of Bitwarden while the worker is on its way out shows the unlock with a note', async () => {
  let running = true
  const fetchMock = mockFetch({
    ...unlockedRoutes,
    'GET /gateway/account': () => jsonResponse(200, { ...stopped(true), running }),
    'POST /gateway/account': () => jsonResponse(200, { ...stopped(true), running }),
    'POST /api/bw-logout': () => {
      running = false
      return jsonResponse(502, { detail: "The account's worker does not answer" })
    },
  })
  renderWithQuery(<App />)

  const dialog = await openLogoutDialog()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: BOX }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Log out' }))

  expect(await screen.findByText('Unlock first to log out of Bitwarden')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(fetchMock.mock.calls.filter(([input]) => String(input) === '/api/bw-logout')).toHaveLength(2)
})

test('log out of Bitwarden after the session timed out shows the unlock with a note, gone after the unlock', async () => {
  let unlocked = true
  mockFetch({
    ...gatewayRoutes,
    ...unlockedRoutes,
    'GET /api/session': () =>
      jsonResponse(200, {
        state: unlocked ? 'unlocked' : 'locked',
        mcpEnabled: false,
        bwLogin: { loggedIn: true, server: SERVER, email: EMAIL },
      }),
    'POST /api/bw-logout': () => {
      unlocked = false
      return jsonResponse(401, { detail: 'Not logged in' })
    },
    'POST /api/session': () => {
      unlocked = true
      return jsonResponse(200, {
        state: 'unlocked',
        mcpEnabled: false,
        bwLogin: { loggedIn: true, server: SERVER, email: EMAIL },
      })
    },
  })
  renderWithQuery(<App />)

  const dialog = await openLogoutDialog()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: BOX }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Log out' }))

  expect(await screen.findByText('Unlock first to log out of Bitwarden')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText(/Master password/), { target: { value: 'pw' } })
  fireEvent.click(screen.getByRole('button', { name: 'Unlock' }))
  expect(await screen.findByRole('button', { name: 'Log out' })).toBeInTheDocument()
  expect(screen.queryByText('Unlock first to log out of Bitwarden')).toBeNull()
})

test('a failed log out of Bitwarden says so in the dialog', async () => {
  mockFetch({ ...unlockedRoutes, 'POST /api/bw-logout': () => jsonResponse(500, { detail: 'bw logout failed' }) })
  renderWithQuery(<App />)

  const dialog = await openLogoutDialog()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: BOX }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Log out' }))

  expect(await within(dialog).findByRole('alert')).toHaveTextContent('Could not log out of Bitwarden')
  expect(within(dialog).getByRole('button', { name: 'Log out' })).toBeEnabled()
})

test('a 401 from any request switches to the login page', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    mockFetch({
      'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false }),
      'GET /api/queue?status=pending': () => jsonResponse(200, []),
      'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
      'GET /api/health': () => jsonResponse(200, { status: 'ok', version: '261028 abc1234' }),
      'POST /api/session/heartbeat': () => jsonResponse(401, { detail: 'Not logged in' }),
    })
    renderWithQuery(<App />)
    await screen.findByRole('button', { name: 'Log out' })

    await vi.advanceTimersByTimeAsync(30_000)

    await waitFor(() => expect(screen.getByLabelText(/Master password/)).toBeInTheDocument())
  } finally {
    vi.useRealTimers()
  }
})

test('names the backups a login could not re-encrypt', async () => {
  mockFetch({
    'GET /api/session': () =>
      jsonResponse(200, {
        state: 'unlocked',
        mcpEnabled: false,
        backupReencryption: { done: 4, failed: ['20200101T000000Z.zip.age'] },
      }),
  })
  renderWithQuery(<App />)
  expect(await screen.findByRole('alert')).toHaveTextContent('20200101T000000Z.zip.age')
})

test('the menu has Settings and About bwtidy', async () => {
  mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false }),
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
  })
  renderWithQuery(<App />)

  fireEvent.click(await screen.findByRole('button', { name: 'More actions' }))
  await screen.findByRole('menuitem', { name: 'Settings' })
  expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Settings', 'About bwtidy'])
  expect(screen.queryByRole('menuitem', { name: /Log out/ })).toBeNull()
})

test('the menu has Switch account between them when an account is open', async () => {
  mockFetch({ ...gatewayRoutes, ...unlockedRoutes })
  renderWithQuery(<App />)

  fireEvent.click(await screen.findByRole('button', { name: 'More actions' }))
  await screen.findByRole('menuitem', { name: 'Switch account' })
  expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual([
    'Settings',
    'Switch account',
    'About bwtidy',
  ])
})

test('About bwtidy opens the About page', async () => {
  mockFetch({ ...gatewayRoutes, ...unlockedRoutes })
  renderWithQuery(<App />)

  fireEvent.click(await screen.findByRole('button', { name: 'More actions' }))
  fireEvent.click(await screen.findByRole('menuitem', { name: 'About bwtidy' }))

  expect(await screen.findByRole('heading', { name: 'About bwtidy' })).toBeInTheDocument()
  expect(await screen.findByText('Version 261028 abc1234')).toBeInTheDocument()
})

test('the app bar shows the account, with the host unless it is vault.bitwarden.com', async () => {
  mockFetch({ ...gatewayRoutes, ...unlockedRoutes })
  renderWithQuery(<App />)
  expect(await within(await screen.findByRole('banner')).findByText(`${EMAIL} (vaultwarden)`)).toBeInTheDocument()
})

test('the app bar shows only the email for vault.bitwarden.com', async () => {
  const account = { server: 'https://vault.bitwarden.com', email: EMAIL }
  mockFetch({
    ...unlockedRoutes,
    'GET /gateway/account': () => jsonResponse(200, { account, running: true }),
  })
  renderWithQuery(<App />)
  expect(await within(await screen.findByRole('banner')).findByText(EMAIL)).toBeInTheDocument()
})

test('Switch account ends the session, forgets the account and shows the account form', async () => {
  localStorage.setItem('bwtidy.account', JSON.stringify({ server: SERVER, email: EMAIL }))
  const fetchMock = mockFetch({
    ...gatewayRoutes,
    ...unlockedRoutes,
    'DELETE /api/session': () => jsonResponse(204),
    'DELETE /gateway/account': () => jsonResponse(204),
  })
  renderWithQuery(<App />)

  fireEvent.click(await screen.findByRole('button', { name: 'More actions' }))
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Switch account' }))

  expect(await screen.findByRole('button', { name: 'Continue' })).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/api/session', expect.objectContaining({ method: 'DELETE' }))
  expect(fetchMock).toHaveBeenCalledWith('/gateway/account', expect.objectContaining({ method: 'DELETE' }))
  expect(localStorage.getItem('bwtidy.account')).toBeNull()
  expect(posts(fetchMock, '/gateway/account')).toEqual([])
})

test('Switch account under the unlock shows the account form', async () => {
  const fetchMock = mockFetch({
    ...gatewayRoutes,
    'GET /api/session': () => jsonResponse(200, { state: 'locked', mcpEnabled: false }),
    'DELETE /gateway/account': () => jsonResponse(204),
  })
  renderWithQuery(<App />)

  expect(await screen.findByText(`Account ${EMAIL} (vaultwarden)`)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Switch account' }))

  expect(await screen.findByRole('button', { name: 'Continue' })).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/gateway/account', expect.objectContaining({ method: 'DELETE' }))
  expect(fetchMock).not.toHaveBeenCalledWith('/api/session', expect.objectContaining({ method: 'DELETE' }))
})

// The gateway's answer for the account when no worker runs (docs/spec.md, Gateway).
function stopped(loggedIn: boolean) {
  return { account: { server: SERVER, email: EMAIL }, running: false, exists: true, pending: false, loggedIn }
}

function sessionAsked(fetchMock: ReturnType<typeof mockFetch>): boolean {
  return fetchMock.mock.calls.some(([input]) => String(input) === '/api/session')
}

test('a remembered account is opened again and shows the unlock without starting a worker', async () => {
  localStorage.setItem('bwtidy.account', JSON.stringify({ server: SERVER, email: EMAIL }))
  const fetchMock = mockFetch({
    'GET /gateway/account': () => jsonResponse(200, { account: null, running: false }),
    'POST /gateway/account': () => jsonResponse(200, stopped(true)),
  })
  renderWithQuery(<App />)

  expect(await screen.findByRole('button', { name: 'Unlock' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Continue' })).toBeNull()
  expect(posts(fetchMock, '/gateway/account')).toEqual([{ server: SERVER, email: EMAIL, create: false }])
  expect(sessionAsked(fetchMock)).toBe(false)
})

test('the account of the routing cookie shows its unlock while its worker does not run', async () => {
  const fetchMock = mockFetch({ 'GET /gateway/account': () => jsonResponse(200, stopped(true)) })
  renderWithQuery(<App />)

  expect(await screen.findByRole('button', { name: 'Unlock' })).toBeInTheDocument()
  expect(screen.getByText(`Account ${EMAIL} (vaultwarden)`)).toBeInTheDocument()
  expect(posts(fetchMock, '/gateway/account')).toEqual([])
  expect(sessionAsked(fetchMock)).toBe(false)
})

test('an account whose CLI is logged out shows the Bitwarden login while its worker does not run', async () => {
  const fetchMock = mockFetch({ 'GET /gateway/account': () => jsonResponse(200, stopped(false)) })
  renderWithQuery(<App />)

  expect(await screen.findByRole('button', { name: 'Log in' })).toBeInTheDocument()
  expect(screen.getByLabelText(/Server/)).toHaveValue(SERVER)
  expect(screen.getByLabelText(/Email/)).toHaveValue(EMAIL)
  expect(sessionAsked(fetchMock)).toBe(false)
})

test('a worker that exited behind the page shows what the gateway knows', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    let running = true
    const fetchMock = mockFetch({
      ...unlockedRoutes,
      'GET /gateway/account': () => jsonResponse(200, { ...stopped(false), running }),
      'POST /gateway/account': () => jsonResponse(200, { ...stopped(false), running }),
      'POST /api/session/heartbeat': () => {
        running = false
        return jsonResponse(401, { detail: 'No account' })
      },
    })
    renderWithQuery(<App />)
    await screen.findByRole('button', { name: 'Log out' })

    await vi.advanceTimersByTimeAsync(30_000)

    // the Bitwarden login, as the CLI logged out on the gateway's word, and no worker started
    expect(await screen.findByRole('button', { name: 'Log in' })).toBeInTheDocument()
    expect(posts(fetchMock, '/gateway/account')).toHaveLength(1)
    expect(posts(fetchMock, '/api/session')).toEqual([])
  } finally {
    vi.useRealTimers()
  }
})

test('a remembered account that is gone shows the account form', async () => {
  localStorage.setItem('bwtidy.account', JSON.stringify({ server: SERVER, email: EMAIL }))
  mockFetch({
    'GET /gateway/account': () => jsonResponse(200, { account: null, running: false }),
    'POST /gateway/account': () => jsonResponse(404, { exists: false }),
  })
  renderWithQuery(<App />)

  expect(await screen.findByRole('button', { name: 'Continue' })).toBeInTheDocument()
  expect(localStorage.getItem('bwtidy.account')).toBeNull()
})

test('without a remembered account the app asks for the account', async () => {
  const fetchMock = mockFetch({
    'GET /gateway/account': () => jsonResponse(200, { account: null, running: false }),
  })
  renderWithQuery(<App />)

  expect(await screen.findByRole('button', { name: 'Continue' })).toBeInTheDocument()
  expect(fetchMock).not.toHaveBeenCalledWith('/api/session', expect.anything())
})

const BEFORE_YOU_START = { name: 'Before you start' }

test('the notice for a first start shows above the account form and the Bitwarden login', async () => {
  const fetchMock = mockFetch({
    'GET /gateway/account': () => jsonResponse(200, { account: null, running: false, firstRun: true }),
    'POST /gateway/account': () => jsonResponse(200, { ...stopped(false), firstRun: true }),
  })
  renderWithQuery(<App />)

  // there with the form at once, the form usable under it
  const form = await screen.findByRole('button', { name: 'Continue' })
  expect(screen.getByRole('note', BEFORE_YOU_START)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText(/Email/), { target: { value: EMAIL } })
  fireEvent.click(form)

  expect(await screen.findByRole('button', { name: 'Log in' })).toBeInTheDocument()
  expect(screen.getByRole('note', BEFORE_YOU_START)).toBeInTheDocument()
  expect(posts(fetchMock, '/gateway/account')).toHaveLength(1)
})

test('the notice for a first start shows above the unlock and goes once a login happened', async () => {
  let firstRun = true
  mockFetch({
    'GET /gateway/account': () => jsonResponse(200, { ...stopped(true), firstRun }),
    'POST /gateway/account': () => jsonResponse(200, { ...stopped(true), firstRun }),
    'DELETE /gateway/account': () => jsonResponse(204),
  })
  renderWithQuery(<App />)

  expect(await screen.findByRole('button', { name: 'Unlock' })).toBeInTheDocument()
  expect(screen.getByRole('note', BEFORE_YOU_START)).toBeInTheDocument()
  // a login in another browser, then this one shows the account form: the gateway is asked again
  firstRun = false
  fireEvent.click(screen.getByRole('button', { name: 'Switch account' }))
  expect(await screen.findByRole('button', { name: 'Continue' })).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByRole('note', BEFORE_YOU_START)).toBeNull())
})

test('no notice for a first start once an account logged in', async () => {
  mockFetch({ 'GET /gateway/account': () => jsonResponse(200, { ...stopped(true), firstRun: false }) })
  renderWithQuery(<App />)

  expect(await screen.findByRole('button', { name: 'Unlock' })).toBeInTheDocument()
  expect(screen.queryByRole('note', BEFORE_YOU_START)).toBeNull()
})

test('an unlock that finds the worker gone starts it again and unlocks there', async () => {
  let gone = true
  let state = 'locked'
  const fetchMock = mockFetch({
    ...gatewayRoutes,
    ...unlockedRoutes,
    'GET /api/session': () => jsonResponse(200, { state, mcpEnabled: false }),
    'POST /api/session': () => {
      if (gone) {
        gone = false
        return jsonResponse(401, { detail: 'No account' })
      }
      state = 'unlocked'
      return jsonResponse(200, { state, mcpEnabled: false })
    },
  })
  renderWithQuery(<App />)

  fireEvent.change(await screen.findByLabelText(/Master password/), { target: { value: 'pw' } })
  fireEvent.click(screen.getByRole('button', { name: 'Unlock' }))

  expect(await screen.findByRole('button', { name: 'Log out' })).toBeInTheDocument()
  expect(posts(fetchMock, '/gateway/account')).toHaveLength(1)
  expect(posts(fetchMock, '/api/session')).toHaveLength(2)
})

const OTHER = { server: SERVER, email: 'peer@example.test' }

// What the browser fires in this tab when another tab of the profile writes localStorage.
function otherTabWrites(key: string | null, newValue: string | null) {
  window.dispatchEvent(new StorageEvent('storage', { key, newValue, storageArea: localStorage }))
}

async function renderUnlocked() {
  const fetchMock = mockFetch({ ...gatewayRoutes, ...unlockedRoutes, 'DELETE /api/session': () => jsonResponse(204) })
  renderWithQuery(<App />)
  await screen.findByRole('heading', { name: /^bwtidy/ })
  return fetchMock
}

test('another tab opening another account reloads this tab', async () => {
  await renderUnlocked()
  otherTabWrites('bwtidy.account', JSON.stringify(OTHER))
  expect(reload).toHaveBeenCalledTimes(1)
})

test('another tab writing the same account does not reload this tab', async () => {
  await renderUnlocked()
  otherTabWrites('bwtidy.account', JSON.stringify({ email: EMAIL, server: SERVER }))
  expect(reload).not.toHaveBeenCalled()
})

test('another tab forgetting the account reloads this tab', async () => {
  await renderUnlocked()
  otherTabWrites('bwtidy.account', null)
  otherTabWrites(null, null)
  expect(reload).toHaveBeenCalledTimes(2)
})

test('other localStorage keys of another tab do not reload this tab', async () => {
  await renderUnlocked()
  otherTabWrites('bwtidy.accountForm', JSON.stringify(OTHER))
  otherTabWrites(`bwtidy.seenQueueEntries:${SERVER}:${EMAIL}`, '{"pending":[1]}')
  expect(reload).not.toHaveBeenCalled()
})

test('a tab on the account form reloads when another tab opens an account, not when it forgets one', async () => {
  mockFetch({ 'GET /gateway/account': () => jsonResponse(200, { account: null, running: false }) })
  renderWithQuery(<App />)
  await screen.findByRole('button', { name: 'Continue' })

  otherTabWrites('bwtidy.account', null)
  expect(reload).not.toHaveBeenCalled()
  otherTabWrites('bwtidy.account', JSON.stringify(OTHER))
  expect(reload).toHaveBeenCalledTimes(1)
})

test('log out tells the other tabs, and a log out in another tab reloads this one', async () => {
  await renderUnlocked()
  otherTabWrites('bwtidy.loggedOut', '1')
  expect(reload).toHaveBeenCalledTimes(1)

  const dialog = await openLogoutDialog()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Log out' }))
  await screen.findByRole('button', { name: 'Unlock' })
  expect(localStorage.getItem('bwtidy.loggedOut')).not.toBeNull()
  expect(localStorage.getItem('bwtidy.account')).toBe(JSON.stringify({ server: SERVER, email: EMAIL }))
})

test('a tab whose worker is gone after another tab switched account reloads instead of opening its old one', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    const fetchMock = mockFetch({
      ...gatewayRoutes,
      ...unlockedRoutes,
      'POST /api/session/heartbeat': () => jsonResponse(401, { detail: 'No account' }),
    })
    renderWithQuery(<App />)
    await screen.findByRole('button', { name: 'Log out' })
    // The other tab's switch, without its storage event, so only the reopen can notice it.
    localStorage.setItem('bwtidy.account', JSON.stringify(OTHER))

    await vi.advanceTimersByTimeAsync(30_000)

    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1))
    expect(posts(fetchMock, '/gateway/account')).toEqual([])
  } finally {
    vi.useRealTimers()
  }
})

test('a tab whose worker is gone reopens its account while no other tab changed it', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    const fetchMock = mockFetch({
      ...gatewayRoutes,
      ...unlockedRoutes,
      'POST /api/session/heartbeat': () => jsonResponse(401, { detail: 'No account' }),
    })
    renderWithQuery(<App />)
    await screen.findByRole('button', { name: 'Log out' })

    await vi.advanceTimersByTimeAsync(30_000)

    await waitFor(() => expect(posts(fetchMock, '/gateway/account')).toHaveLength(1))
    expect(reload).not.toHaveBeenCalled()
  } finally {
    vi.useRealTimers()
  }
})

test('the color mode button opens a menu with System, Light and Dark and the current one checked', async () => {
  document.cookie = `${COLOR_MODE_COOKIE}=; Path=/; Max-Age=0`
  mockFetch({ ...gatewayRoutes, ...unlockedRoutes })
  renderWithQuery(
    <ColorModeProvider>
      <App />
    </ColorModeProvider>,
  )

  fireEvent.click(await screen.findByRole('button', { name: 'Color mode: System' }))
  expect(screen.getAllByRole('menuitemradio').map((item) => item.textContent)).toEqual(['System', 'Light', 'Dark'])
  expect(screen.getByRole('menuitemradio', { name: 'System' })).toHaveAttribute('aria-checked', 'true')
  expect(screen.getByRole('menuitemradio', { name: 'Dark' })).toHaveAttribute('aria-checked', 'false')

  fireEvent.click(screen.getByRole('menuitemradio', { name: 'Dark' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Color mode: Dark' }))
  expect(document.cookie).toContain(`${COLOR_MODE_COOKIE}=dark`)
  expect(screen.getByRole('menuitemradio', { name: 'Dark' })).toHaveAttribute('aria-checked', 'true')
  expect(screen.getByRole('menuitemradio', { name: 'System' })).toHaveAttribute('aria-checked', 'false')

  fireEvent.click(screen.getByRole('menuitemradio', { name: 'System' }))
  await screen.findByRole('button', { name: 'Color mode: System' })
  expect(document.cookie).not.toContain(COLOR_MODE_COOKIE)
})

test('the Datatypes tab opens the Statistics page', async () => {
  mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false }),
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
    'GET /api/templates': () => jsonResponse(200, { templates: [], saving: false }),
    'GET /api/templates/matches': () => jsonResponse(200, {}),
    'GET /api/datatypes': () => jsonResponse(200, { datatypes: [], saving: false }),
    'GET /api/statistics': () =>
      jsonResponse(200, {
        limits: { maxPatternLen: 500, maxRepeat: 100, maxStates: 120, maxDepth: 20, maxValueLen: 1000 },
        reloads: 0,
        datatypes: [],
        rows: [],
        rowCount: 0,
      }),
  })
  renderWithQuery(<App />)

  fireEvent.click(await screen.findByRole('button', { name: 'Types' }))
  fireEvent.click(await screen.findByRole('tab', { name: 'Datatypes' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Pattern limits and statistics' }))
  expect(await screen.findByRole('heading', { name: 'Statistics' })).toBeInTheDocument()
})

const pageRoutes = {
  ...unlockedRoutes,
  'GET /api/templates': () => jsonResponse(200, { templates: [], saving: false }),
  'GET /api/datatypes': () => jsonResponse(200, []),
  'GET /api/queue/auto-commit': () =>
    jsonResponse(200, { committing: false, commitCount: 0, nextCommitAt: null, phase: null, failed: [] }),
}

function gridFetches(fetchMock: ReturnType<typeof mockFetch>): number {
  return fetchMock.mock.calls.filter(([input]) => String(input) === '/api/grid').length
}

test('the Vault button is the current page on start and clicking it does nothing', async () => {
  const fetchMock = mockFetch(pageRoutes)
  renderWithQuery(<App />)
  const vault = await screen.findByRole('button', { name: 'Vault' })
  expect(vault).toHaveAttribute('aria-current', 'page')
  expect(vault).toHaveClass(CURRENT_NAV_CLASS)
  expect(vault).toBeEnabled()
  await waitFor(() => expect(gridFetches(fetchMock)).toBeGreaterThan(0))
  const before = fetchMock.mock.calls.length

  fireEvent.click(vault)
  await new Promise((resolve) => setTimeout(resolve, 50))

  expect(fetchMock.mock.calls.length).toBe(before)
  expect(screen.getByRole('button', { name: 'Vault' })).toHaveAttribute('aria-current', 'page')
})

test('opening Types moves the current mark from Vault to Types', async () => {
  mockFetch(pageRoutes)
  renderWithQuery(<App />)
  const vault = await screen.findByRole('button', { name: 'Vault' })
  const templates = screen.getByRole('button', { name: 'Types' })
  expect(templates).not.toHaveAttribute('aria-current')
  expect(templates).not.toHaveClass(CURRENT_NAV_CLASS)

  fireEvent.click(templates)

  await waitFor(() => expect(templates).toHaveAttribute('aria-current', 'page'))
  expect(templates).toHaveClass(CURRENT_NAV_CLASS)
  expect(vault).not.toHaveAttribute('aria-current')
  expect(vault).not.toHaveClass(CURRENT_NAV_CLASS)
  expect(screen.getAllByRole('button', { current: 'page' })).toHaveLength(1)
})
