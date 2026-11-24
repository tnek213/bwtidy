import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from '../App.tsx'
import { jsonResponse, mockFetch, renderWithQuery } from '../test-utils.tsx'

const SERVER = 'https://vaultwarden'
const EMAIL = 'user@example.test'

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

const noAccount = {
  'GET /gateway/account': () => jsonResponse(200, { account: null, running: false }),
  'GET /api/session': () =>
    jsonResponse(200, {
      state: 'locked',
      mcpEnabled: false,
      bwLogin: { loggedIn: false, server: SERVER, email: EMAIL, accountFixed: true },
    }),
}

// The gateway's answer: no worker runs, it starts only for the Bitwarden login or the unlock.
function opened(init: RequestInit, loggedIn = false) {
  const { server, email, create } = JSON.parse(String(init.body)) as {
    server: string
    email: string
    create: boolean
  }
  return jsonResponse(200, { account: { server, email }, running: false, exists: !create, pending: false, loggedIn })
}

function sessionAsked(fetchMock: ReturnType<typeof mockFetch>): boolean {
  return fetchMock.mock.calls.some(([input]) => String(input) === '/api/session')
}

function accountPosts(fetchMock: ReturnType<typeof mockFetch>): unknown[] {
  return fetchMock.mock.calls
    .filter(([input, init]) => String(input) === '/gateway/account' && init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init?.body)))
}

async function fill(server: string, email: string) {
  fireEvent.change(await screen.findByLabelText(/Server/), { target: { value: server } })
  fireEvent.change(screen.getByLabelText(/Email/), { target: { value: email } })
}

test('the form starts with the default server and an empty email, Continue disabled', async () => {
  mockFetch(noAccount)
  renderWithQuery(<App />)

  expect(await screen.findByLabelText(/Server/)).toHaveValue('https://vault.bitwarden.com')
  expect(screen.getByLabelText(/Email/)).toHaveValue('')
  expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled()
})

test('an existing account opens and goes on to its Bitwarden login', async () => {
  const fetchMock = mockFetch({ ...noAccount, 'POST /gateway/account': opened })
  renderWithQuery(<App />)

  await fill(` ${SERVER} `, EMAIL)
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

  expect(await screen.findByRole('button', { name: 'Log in' })).toBeInTheDocument()
  expect(screen.getByLabelText(/Email/)).toHaveAttribute('readonly')
  expect(accountPosts(fetchMock)).toEqual([{ server: SERVER, email: EMAIL, create: false }])
  expect(JSON.parse(localStorage.getItem('bwtidy.account') ?? 'null')).toEqual({ server: SERVER, email: EMAIL })
  // no worker runs yet, so the gateway's answer decides the page
  expect(sessionAsked(fetchMock)).toBe(false)
})

test('an existing account whose CLI is logged in opens at the unlock without a worker', async () => {
  const fetchMock = mockFetch({ ...noAccount, 'POST /gateway/account': (init) => opened(init, true) })
  renderWithQuery(<App />)

  await fill(SERVER, EMAIL)
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

  expect(await screen.findByRole('button', { name: 'Unlock' })).toBeInTheDocument()
  expect(screen.getByText(`Account ${EMAIL} (vaultwarden)`)).toBeInTheDocument()
  expect(sessionAsked(fetchMock)).toBe(false)
})

test('the form remembers what was typed last', async () => {
  localStorage.setItem('bwtidy.accountForm', JSON.stringify({ server: SERVER, email: EMAIL }))
  mockFetch(noAccount)
  renderWithQuery(<App />)

  expect(await screen.findByLabelText(/Server/)).toHaveValue(SERVER)
  expect(screen.getByLabelText(/Email/)).toHaveValue(EMAIL)
})

test('Continue saves the typed values for the next time', async () => {
  mockFetch({ ...noAccount, 'POST /gateway/account': () => jsonResponse(422, { detail: 'Not an email address.' }) })
  renderWithQuery(<App />)

  await fill(SERVER, 'peer@example.test')
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

  await screen.findByRole('alert')
  expect(JSON.parse(localStorage.getItem('bwtidy.accountForm') ?? 'null')).toEqual({
    server: SERVER,
    email: 'peer@example.test',
  })
})

test('a broken localStorage leaves the form with its defaults', async () => {
  localStorage.setItem('bwtidy.accountForm', '{not json')
  mockFetch(noAccount)
  renderWithQuery(<App />)

  expect(await screen.findByLabelText(/Server/)).toHaveValue('https://vault.bitwarden.com')
})

test('a missing account is offered for creation, and Create posts again with create', async () => {
  const fetchMock = mockFetch({
    ...noAccount,
    'POST /gateway/account': (init) =>
      JSON.parse(String(init.body)).create ? opened(init) : jsonResponse(404, { exists: false }),
  })
  renderWithQuery(<App />)

  await fill(SERVER, EMAIL)
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

  const dialog = await screen.findByRole('dialog', { name: 'Create account' })
  expect(dialog).toHaveTextContent(
    `There is no bwtidy account for ${EMAIL} on ${SERVER} yet. Create it? It is kept only if the Bitwarden login succeeds.`,
  )
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }))

  expect(await screen.findByRole('button', { name: 'Log in' })).toBeInTheDocument()
  expect(screen.getByLabelText(/Email/)).toHaveValue(EMAIL)
  expect(accountPosts(fetchMock)).toEqual([
    { server: SERVER, email: EMAIL, create: false },
    { server: SERVER, email: EMAIL, create: true },
  ])
  expect(sessionAsked(fetchMock)).toBe(false)
})

test('Cancel in the create dialog stays on the form', async () => {
  const fetchMock = mockFetch({ ...noAccount, 'POST /gateway/account': () => jsonResponse(404, { exists: false }) })
  renderWithQuery(<App />)

  await fill(SERVER, EMAIL)
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  const dialog = await screen.findByRole('dialog', { name: 'Create account' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))

  expect(await screen.findByRole('button', { name: 'Continue' })).toBeEnabled()
  expect(accountPosts(fetchMock)).toHaveLength(1)
})

test('a 422 shows its reason under the form', async () => {
  mockFetch({
    ...noAccount,
    'POST /gateway/account': () =>
      jsonResponse(422, { detail: 'https://intranet.example.test does not resolve to a public address.' }),
  })
  renderWithQuery(<App />)

  await fill('https://intranet.example.test', EMAIL)
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'https://intranet.example.test does not resolve to a public address.',
  )
  expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled()
})

test('another failure says the account could not be opened', async () => {
  mockFetch({ ...noAccount, 'POST /gateway/account': () => jsonResponse(500, {}) })
  renderWithQuery(<App />)

  await fill(SERVER, EMAIL)
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Could not open the account, try again')
  expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled()
})

test('a remembered account that cannot be opened shows the reason on the form', async () => {
  localStorage.setItem('bwtidy.account', JSON.stringify({ server: SERVER, email: EMAIL }))
  mockFetch({
    ...noAccount,
    'POST /gateway/account': () =>
      jsonResponse(422, { detail: 'https://vaultwarden does not resolve to a public address.' }),
  })
  renderWithQuery(<App />)

  expect(await screen.findByRole('alert')).toHaveTextContent('https://vaultwarden does not resolve to a public address.')
  expect(screen.getByRole('button', { name: 'Continue' })).toBeInTheDocument()
})
