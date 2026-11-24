import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from '../App.tsx'
import { jsonResponse, mockFetch, renderWithQuery } from '../test-utils.tsx'

const PASSWORD = 'correct horse battery staple'

let logSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  logSpy = vi.spyOn(console, 'log')
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function expectPasswordNotLeaked() {
  const stored = [localStorage, sessionStorage].flatMap((storage) =>
    Object.keys(storage).map((key) => `${key}=${storage.getItem(key)}`),
  )
  expect(stored.join('\n')).not.toContain(PASSWORD)
  expect(window.location.href).not.toContain(encodeURIComponent(PASSWORD))
  expect(JSON.stringify(logSpy.mock.calls)).not.toContain(PASSWORD)
  expect(screen.getByLabelText(/Master password/)).toHaveValue('')
}

async function submitPassword() {
  const field = await screen.findByLabelText(/Master password/)
  fireEvent.change(field, { target: { value: PASSWORD } })
  fireEvent.click(screen.getByRole('button', { name: 'Unlock' }))
}

test('submit posts the password', async () => {
  const fetchMock = mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'locked', mcpEnabled: false }),
    'POST /api/session': () => jsonResponse(401, { detail: 'Wrong master password' }),
  })
  renderWithQuery(<App />)
  await submitPassword()

  await screen.findByText('Wrong master password')
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/session',
    expect.objectContaining({
      method: 'POST',
      credentials: 'same-origin',
      body: JSON.stringify({ password: PASSWORD }),
    }),
  )
})

test('a 401 shows the error and stays on the login page', async () => {
  mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'locked', mcpEnabled: false }),
    'POST /api/session': () => jsonResponse(401, { detail: 'Wrong master password' }),
  })
  renderWithQuery(<App />)
  await submitPassword()

  expect(await screen.findByRole('alert')).toHaveTextContent('Wrong master password')
  expect(screen.getByRole('button', { name: 'Unlock' })).toBeEnabled()
  expect(screen.queryByRole('button', { name: 'Log out' })).not.toBeInTheDocument()
  expectPasswordNotLeaked()
})

test('too many wrong passwords against a running session shows how long to wait', async () => {
  mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'locked', mcpEnabled: false }),
    'POST /api/session': () => jsonResponse(429, { detail: 'Too many wrong passwords. Try again in 4 s.' }),
  })
  renderWithQuery(<App />)
  await submitPassword()

  expect(await screen.findByRole('alert')).toHaveTextContent('Too many wrong passwords. Try again in 4 s.')
  expect(screen.getByRole('button', { name: 'Unlock' })).toBeEnabled()
  expectPasswordNotLeaked()
})

test('a worker on its way out: the next worker is started and the login repeated', async () => {
  const account = { account: { server: 'https://vaultwarden', email: 'dev@bwtidy.test' }, running: true }
  const logins = [
    () => jsonResponse(502, { detail: "The account's worker does not answer" }),
    () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false }),
  ]
  const fetchMock = mockFetch({
    'GET /gateway/account': () => jsonResponse(200, account),
    'POST /gateway/account': () => jsonResponse(200, account),
    'GET /api/session': () => jsonResponse(200, { state: 'locked', mcpEnabled: false }),
    'POST /api/session': () => logins.shift()?.() ?? jsonResponse(500, {}),
  })
  renderWithQuery(<App />)
  await submitPassword()

  expect(await screen.findByRole('button', { name: 'Log out' })).toBeInTheDocument()
  expect(logins).toEqual([])
  const reopened = fetchMock.mock.calls.filter(
    ([url, init]) => url === '/gateway/account' && init?.method === 'POST',
  )
  expect(reopened).toHaveLength(1)
})

test('success switches to the shell', async () => {
  mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'locked', mcpEnabled: false }),
    'POST /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false }),
  })
  renderWithQuery(<App />)
  await submitPassword()

  expect(await screen.findByRole('button', { name: 'Log out' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'bwtidy' })).toBeInTheDocument()
  expect(screen.queryByLabelText(/Master password/)).not.toBeInTheDocument()
  expect(JSON.stringify([localStorage, sessionStorage])).not.toContain(PASSWORD)
})

// The gateway with the account open and no worker running: the unlock starts one.
const ACCOUNT = { server: 'https://vaultwarden', email: 'dev@bwtidy.test' }
const notRunning = { account: ACCOUNT, running: false, exists: true, pending: false, loggedIn: true }

function deferred() {
  let resolve: (response: Response) => void = () => {}
  const promise = new Promise<Response>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

test('without a running worker the unlock shows Starting until the answer', async () => {
  const answer = deferred()
  const fetchMock = mockFetch({
    'GET /gateway/account': () => jsonResponse(200, notRunning),
    'POST /api/session': () => answer.promise,
  })
  renderWithQuery(<App />)
  await submitPassword()

  expect(await screen.findByText('Starting')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Unlock' })).toBeDisabled()
  // the page knew the CLI is logged in from the gateway, no worker was asked
  expect(fetchMock).not.toHaveBeenCalledWith('/api/session', expect.objectContaining({ method: 'GET' }))
  answer.resolve(jsonResponse(200, { state: 'unlocked', mcpEnabled: false }))

  expect(await screen.findByRole('button', { name: 'Log out' })).toBeInTheDocument()
  expect(screen.queryByText('Starting')).not.toBeInTheDocument()
})

test('with a running worker the unlock shows no Starting', async () => {
  const answer = deferred()
  mockFetch({
    'GET /gateway/account': () => jsonResponse(200, { ...notRunning, running: true }),
    'GET /api/session': () => jsonResponse(200, { state: 'locked', mcpEnabled: false }),
    'POST /api/session': () => answer.promise,
  })
  renderWithQuery(<App />)
  await submitPassword()

  await waitFor(() => expect(screen.getByRole('button', { name: 'Unlock' })).toBeDisabled())
  expect(screen.queryByText('Starting')).not.toBeInTheDocument()
  answer.resolve(jsonResponse(200, { state: 'unlocked', mcpEnabled: false }))
  expect(await screen.findByRole('button', { name: 'Log out' })).toBeInTheDocument()
})

test.each([
  [503, 'The worker could not start.'],
  [429, 'Too many accounts are open at once. Try again later.'],
])('a failed start (%i) shows the gateway\'s text', async (status, detail) => {
  mockFetch({
    'GET /gateway/account': () => jsonResponse(200, notRunning),
    'POST /api/session': () => jsonResponse(status, { detail }),
  })
  renderWithQuery(<App />)
  await submitPassword()

  expect(await screen.findByRole('alert')).toHaveTextContent(detail)
  expect(screen.queryByText('Starting')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Unlock' })).toBeEnabled()
  expectPasswordNotLeaked()
})

test('an unlock that finds the CLI logged out shows the Bitwarden login', async () => {
  let started = false
  mockFetch({
    'GET /gateway/account': () => jsonResponse(200, { ...notRunning, running: started }),
    'POST /api/session': () => {
      started = true
      return jsonResponse(503, { detail: 'bw serve is not reachable' })
    },
    'GET /api/session': () =>
      jsonResponse(200, {
        state: 'locked',
        mcpEnabled: false,
        bwLogin: { loggedIn: false, ...ACCOUNT, accountFixed: true },
      }),
  })
  renderWithQuery(<App />)
  await submitPassword()

  expect(await screen.findByRole('button', { name: 'Log in' })).toBeInTheDocument()
})

test('the show button shows and hides the password, and it is hidden after submit', async () => {
  mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'locked', mcpEnabled: false }),
    'POST /api/session': () => jsonResponse(401, { detail: 'Wrong master password' }),
  })
  renderWithQuery(<App />)
  const field = await screen.findByLabelText(/Master password/)
  expect(field).toHaveAttribute('type', 'password')
  expect(field).toHaveAttribute('name', 'password')
  expect(field).toHaveAttribute('autocomplete', 'current-password')
  fireEvent.change(field, { target: { value: PASSWORD } })

  fireEvent.click(screen.getByRole('button', { name: 'Show master password' }))
  expect(field).toHaveAttribute('type', 'text')
  expect((field as HTMLInputElement).value === PASSWORD).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Hide master password' }))
  expect(field).toHaveAttribute('type', 'password')

  fireEvent.click(screen.getByRole('button', { name: 'Show master password' }))
  fireEvent.click(screen.getByRole('button', { name: 'Unlock' }))
  await screen.findByText('Wrong master password')
  const after = screen.getByLabelText(/Master password/)
  expect(after).toHaveAttribute('type', 'password')
  expect(screen.getByRole('button', { name: 'Show master password' })).toBeInTheDocument()
})
