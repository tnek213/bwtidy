import { createTheme } from '@mui/material/styles'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { jsonResponse, mockFetch, renderWithQuery } from '../test-utils.tsx'
import { mcpBarSx } from './mcpStyle.ts'
import McpToggle from './McpToggle.tsx'
import type { McpTokenInfo } from '../api/session.ts'
import { expiryWarning } from './mcpToken.ts'

const DAY = 24 * 60 * 60 * 1000

afterEach(() => {
  vi.unstubAllGlobals()
})

// The switch once the session has answered, which enables it.
async function readySwitch() {
  const toggle = await screen.findByRole('switch', { name: 'MCP' })
  await waitFor(() => expect(toggle).toBeEnabled())
  return toggle
}

function inDays(days: number): string {
  return new Date(Date.now() + days * DAY).toISOString()
}

const NEEDS_TOKEN = 'Create an MCP token first: MCP token menu, Create.'

// A session with a token that lives long enough, unless mcpToken says otherwise.
function shell(mcpEnabled: boolean, mcp: (init: RequestInit) => Response, mcpToken = true) {
  return mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled, mcpToken }),
    'GET /api/mcp/token': () =>
      jsonResponse(200, { createdAt: CREATED, expiresAt: inDays(60), lastUsedAt: null } satisfies McpTokenInfo),
    'POST /api/mcp': mcp,
  })
}

test('the switch reflects mcpEnabled from the session', async () => {
  shell(true, () => jsonResponse(200, { mcpEnabled: true }))
  renderWithQuery(<McpToggle />)
  await waitFor(() => expect(screen.getByRole('switch', { name: 'MCP' })).toBeChecked())
})

test('switching posts the change and shows the answer', async () => {
  const fetchMock = shell(false, (init) => {
    const { enabled } = JSON.parse(String(init.body)) as { enabled: boolean }
    return jsonResponse(200, { mcpEnabled: enabled })
  })
  renderWithQuery(<McpToggle />)

  const toggle = await readySwitch()
  expect(toggle).not.toBeChecked()
  fireEvent.click(toggle)

  await waitFor(() => expect(screen.getByRole('switch', { name: 'MCP' })).toBeChecked())
  const call = fetchMock.mock.calls.find(([path]) => path === '/api/mcp')
  expect(call?.[1]?.method).toBe('POST')
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ enabled: true })

  fireEvent.click(screen.getByRole('switch', { name: 'MCP' }))
  await waitFor(() => expect(screen.getByRole('switch', { name: 'MCP' })).not.toBeChecked())
  const last = fetchMock.mock.calls.filter(([path]) => path === '/api/mcp').at(-1)
  expect(JSON.parse(String(last?.[1]?.body))).toEqual({ enabled: false })
})

test('a failed change leaves the switch as the backend has it and says so', async () => {
  shell(false, () => jsonResponse(500, { detail: 'MCP could not start' }))
  renderWithQuery(<McpToggle />)
  fireEvent.click(await readySwitch())
  expect(await screen.findByText('Could not switch MCP: MCP could not start')).toBeInTheDocument()
  expect(screen.getByRole('switch', { name: 'MCP' })).not.toBeChecked()
})

test('without a token the switch stays off, says what to do and sends nothing', async () => {
  const fetchMock = shell(false, () => jsonResponse(200, { mcpEnabled: true }), false)
  renderWithQuery(<McpToggle />)
  const toggle = await readySwitch()
  fireEvent.click(toggle)
  expect(await screen.findByText(NEEDS_TOKEN)).toBeInTheDocument()
  expect(screen.getByRole('switch', { name: 'MCP' })).not.toBeChecked()
  expect(fetchMock.mock.calls.some(([path]) => path === '/api/mcp')).toBe(false)
})

test('the snackbar names the menu items as they are', async () => {
  shell(false, () => jsonResponse(200, { mcpEnabled: true }), false)
  renderWithQuery(<McpToggle />)
  fireEvent.click(await readySwitch())
  await screen.findByText(NEEDS_TOKEN)
  fireEvent.click(screen.getByRole('button', { name: 'MCP token' }))
  expect(await screen.findByRole('menuitem', { name: 'Create' })).toBeInTheDocument()
})

test('an expired token does not switch MCP on either', async () => {
  const fetchMock = mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false, mcpToken: true }),
    'GET /api/mcp/token': () => jsonResponse(200, { createdAt: CREATED, expiresAt: inDays(-1), lastUsedAt: null }),
  })
  renderWithQuery(<McpToggle />)
  await screen.findByText('MCP token expired')
  fireEvent.click(screen.getByRole('switch', { name: 'MCP' }))
  expect(await screen.findByText(/^The MCP token has expired\. Replace it first/)).toBeInTheDocument()
  expect(screen.getByRole('switch', { name: 'MCP' })).not.toBeChecked()
  expect(fetchMock.mock.calls.some(([path]) => path === '/api/mcp')).toBe(false)
})

test('switching off works without a token', async () => {
  const fetchMock = mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: true, mcpToken: false }),
    'POST /api/mcp': () => jsonResponse(200, { mcpEnabled: false }),
  })
  renderWithQuery(<McpToggle />)
  fireEvent.click(await readySwitch())
  await waitFor(() => expect(screen.getByRole('switch', { name: 'MCP' })).not.toBeChecked())
  expect(fetchMock.mock.calls.some(([path]) => path === '/api/mcp')).toBe(true)
  expect(screen.queryByText(NEEDS_TOKEN)).not.toBeInTheDocument()
})

test('a refusal from the backend shows its reason', async () => {
  shell(false, () => jsonResponse(409, { detail: NEEDS_TOKEN }))
  renderWithQuery(<McpToggle />)
  fireEvent.click(await readySwitch())
  expect(await screen.findByText(NEEDS_TOKEN)).toBeInTheDocument()
  expect(screen.queryByText(/^Could not switch MCP/)).not.toBeInTheDocument()
  expect(screen.getByRole('switch', { name: 'MCP' })).not.toBeChecked()
})

test('the switch is not the default color and the bar turns red only while MCP is on', async () => {
  shell(true, () => jsonResponse(200, { mcpEnabled: true }))
  renderWithQuery(<McpToggle />)
  const toggle = await screen.findByRole('switch', { name: 'MCP' })
  await waitFor(() => expect(toggle).toBeChecked())
  expect(toggle.closest('.MuiSwitch-switchBase')).not.toHaveClass('MuiSwitch-colorDefault')
  expect(toggle.closest('.MuiSwitch-switchBase')).toHaveClass('MuiSwitch-colorSuccess')

  const light = createTheme({ palette: { mode: 'light' } })
  const dark = createTheme({ palette: { mode: 'dark' } })
  expect(mcpBarSx(light, false)).toEqual({})
  expect(mcpBarSx(dark, false)).toEqual({})
  // 20% of the theme's red into the primary #1976d2, and into the dark mode bar (#121212 with its 9.2% overlay)
  expect(mcpBarSx(light, true)).toMatchObject({ backgroundColor: 'rgb(62, 104, 177)' })
  expect(mcpBarSx(dark, true)).toMatchObject({ backgroundColor: 'rgb(81, 45, 43)' })
})

const TOKEN = 'test-token-abc'
const CREATED = '2026-11-08T12:00:00+00:00'

// A token that lives lifetime days and has left days to go.
function tokenInfo(lifetime: number, left: number): McpTokenInfo {
  return { createdAt: inDays(left - lifetime), expiresAt: inDays(left), lastUsedAt: null }
}

function tokenShell(
  mcpToken: boolean,
  tokens: string[] = [TOKEN],
  info: McpTokenInfo = { createdAt: CREATED, expiresAt: inDays(60), lastUsedAt: null },
) {
  let next = 0
  return mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false, mcpToken }),
    'GET /api/mcp/token': () => jsonResponse(200, info),
    'POST /api/mcp/token': (init) => {
      const { days } = JSON.parse(String(init.body)) as { days: number | null }
      return jsonResponse(201, {
        token: tokens[next++],
        createdAt: CREATED,
        expiresAt: days === null ? null : inDays(days),
      })
    },
    'DELETE /api/mcp/token': () => jsonResponse(204),
  })
}

// The first item names the action the session's mcpToken allows, so finding
// it also waits for the session answer.
async function openTokenMenu(first: 'Create' | 'Replace') {
  fireEvent.click(await screen.findByRole('button', { name: 'MCP token' }))
  return screen.findByRole('menuitem', { name: first })
}

// Create or Replace, then the expiry dialog with the choice given.
async function createToken(first: 'Create' | 'Replace', choice?: string) {
  fireEvent.click(await openTokenMenu(first))
  const dialog = await screen.findByRole('dialog', { name: 'New MCP token' })
  if (choice !== undefined) {
    fireEvent.click(within(dialog).getByRole('radio', { name: choice }))
  }
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }))
  return screen.findByRole('dialog', { name: 'MCP token' })
}

function postedBodies(fetchMock: ReturnType<typeof mockFetch>): unknown[] {
  return fetchMock.mock.calls
    .filter(([path, init]) => path === '/api/mcp/token' && init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init?.body)))
}

test('without a token the menu offers Create and a disabled Remove', async () => {
  tokenShell(false)
  renderWithQuery(<McpToggle />)
  await openTokenMenu('Create')
  expect(screen.queryByRole('menuitem', { name: 'Replace' })).not.toBeInTheDocument()
  expect(screen.getByRole('menuitem', { name: 'Remove' })).toHaveAttribute('aria-disabled', 'true')
})

test('Create shows the token, the URL and the link to the docs once', async () => {
  const writeText = vi.fn(async () => {})
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
  const fetchMock = tokenShell(false)
  renderWithQuery(<McpToggle />)
  const dialog = await createToken('Create')
  expect(dialog).toHaveTextContent('shown only now')
  expect(dialog).toHaveTextContent('It expires')
  expect(dialog).toHaveTextContent(
    'An MCP client sends the token as the header Authorization: Bearer TOKEN to the MCP URL. A new token replaces this one.',
  )
  expect(postedBodies(fetchMock)).toEqual([{ days: 90 }])
  expect(screen.getByLabelText('Token')).toHaveValue(TOKEN)
  const url = `${window.location.origin}/mcp`
  expect(screen.getByLabelText('MCP URL')).toHaveValue(url)
  const link = screen.getByRole('link', { name: 'How to connect your MCP client' })
  expect(link).toHaveAttribute('href', '/docs/how-to/mcp/')
  expect(link).toHaveAttribute('target', '_blank')
  expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  expect(screen.queryByRole('button', { name: /^Claude Code/ })).not.toBeInTheDocument()
  expect(screen.queryByText(/Codex|Gemini/)).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Copy mcp url' }))
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(url))
  expect(await screen.findByText('Copied')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Copy token' }))
  await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(TOKEN))

  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(document.body).not.toHaveTextContent(TOKEN)
  await openTokenMenu('Replace')
  expect(screen.getByRole('menuitem', { name: 'Remove' })).not.toHaveAttribute('aria-disabled')
})

test('Replace makes a new token and shows it', async () => {
  tokenShell(true, ['second-token'])
  renderWithQuery(<McpToggle />)
  await createToken('Replace')
  expect(screen.getByLabelText('Token')).toHaveValue('second-token')
})

test('Remove deletes the token and offers Create again', async () => {
  const fetchMock = tokenShell(true)
  renderWithQuery(<McpToggle />)
  await openTokenMenu('Replace')
  fireEvent.click(screen.getByRole('menuitem', { name: 'Remove' }))
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([path, init]) => path === '/api/mcp/token' && init?.method === 'DELETE')).toBe(
      true,
    ),
  )
  await openTokenMenu('Create')
  expect(screen.getByRole('menuitem', { name: 'Remove' })).toHaveAttribute('aria-disabled', 'true')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('a failed Create says so and shows no dialog', async () => {
  mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false, mcpToken: false }),
    'POST /api/mcp/token': () => jsonResponse(500, { detail: 'disk full' }),
  })
  renderWithQuery(<McpToggle />)
  fireEvent.click(await openTokenMenu('Create'))
  const ask = await screen.findByRole('dialog', { name: 'New MCP token' })
  fireEvent.click(within(ask).getByRole('button', { name: 'Create' }))
  expect(await screen.findByText('Could not create the MCP token: disk full')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
})

test('the expiry dialog offers the lifetimes with 90 days picked and warns about no expiry', async () => {
  const fetchMock = tokenShell(false)
  renderWithQuery(<McpToggle />)
  fireEvent.click(await openTokenMenu('Create'))
  const dialog = await screen.findByRole('dialog', { name: 'New MCP token' })
  const radios = within(dialog).getAllByRole('radio')
  expect(radios.map((radio) => radio.closest('label')?.textContent)).toEqual([
    '7 days',
    '30 days',
    '90 days',
    '365 days',
    'No expiry',
  ])
  expect(within(dialog).getByRole('radio', { name: '90 days' })).toBeChecked()
  expect(dialog).not.toHaveTextContent('A token without an expiry works until you delete it.')
  fireEvent.click(within(dialog).getByRole('radio', { name: 'No expiry' }))
  expect(dialog).toHaveTextContent('A token without an expiry works until you delete it.')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }))
  const shown = await screen.findByRole('dialog', { name: 'MCP token' })
  expect(shown).toHaveTextContent('It has no expiry and works until you delete it.')
  expect(postedBodies(fetchMock)).toEqual([{ days: null }])
})

test('the expiry dialog sends the days picked and Cancel makes no token', async () => {
  const fetchMock = tokenShell(false)
  renderWithQuery(<McpToggle />)
  fireEvent.click(await openTokenMenu('Create'))
  const dialog = await screen.findByRole('dialog', { name: 'New MCP token' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(postedBodies(fetchMock)).toEqual([])
  await createToken('Create', '7 days')
  expect(postedBodies(fetchMock)).toEqual([{ days: 7 }])
})

test('the menu shows when the token was made, expires and was last used', async () => {
  const lastUsed = '2026-11-09T08:30:00+00:00'
  tokenShell(true, [TOKEN], { createdAt: CREATED, expiresAt: null, lastUsedAt: lastUsed })
  renderWithQuery(<McpToggle />)
  await openTokenMenu('Replace')
  const times = await screen.findByLabelText('MCP token times')
  expect(times).toHaveTextContent(`Created: ${new Date(CREATED).toLocaleString()}`)
  expect(times).toHaveTextContent('Expires: Never')
  expect(times).toHaveTextContent(`Last used: ${new Date(lastUsed).toLocaleString()}`)
})

test('the menu says Never for a token not used yet', async () => {
  tokenShell(true, [TOKEN], { createdAt: CREATED, expiresAt: null, lastUsedAt: null })
  renderWithQuery(<McpToggle />)
  await openTokenMenu('Replace')
  const times = await screen.findByLabelText('MCP token times')
  expect(times).toHaveTextContent(`Created: ${new Date(CREATED).toLocaleString()}`)
  expect(times).toHaveTextContent('Last used: Never')
})

test('a chip warns a 7 day token from 3 days before the expiry', async () => {
  tokenShell(true, [TOKEN], tokenInfo(7, 4))
  const { unmount } = renderWithQuery(<McpToggle />)
  await openTokenMenu('Replace')
  await screen.findByLabelText('MCP token times')
  expect(screen.queryByText(/^MCP token expire/)).not.toBeInTheDocument()
  unmount()
  tokenShell(true, [TOKEN], tokenInfo(7, 2))
  renderWithQuery(<McpToggle />)
  expect(await screen.findByText('MCP token expires in 2 days')).toBeInTheDocument()
})

test('a chip warns a 365 day token from 15% of its lifetime before the expiry', async () => {
  tokenShell(true, [TOKEN], tokenInfo(365, 60))
  const { unmount } = renderWithQuery(<McpToggle />)
  await openTokenMenu('Replace')
  await screen.findByLabelText('MCP token times')
  expect(screen.queryByText(/^MCP token expire/)).not.toBeInTheDocument()
  unmount()
  tokenShell(true, [TOKEN], tokenInfo(365, 50))
  renderWithQuery(<McpToggle />)
  expect(await screen.findByText('MCP token expires in 50 days')).toBeInTheDocument()
})

test('a chip says when the token has expired', async () => {
  tokenShell(true, [TOKEN], { createdAt: CREATED, expiresAt: inDays(-1), lastUsedAt: null })
  renderWithQuery(<McpToggle />)
  expect(await screen.findByText('MCP token expired')).toBeInTheDocument()
})

test('no chip for a token far from its expiry, without one, or without a token', async () => {
  tokenShell(true, [TOKEN], tokenInfo(90, 80))
  const { unmount } = renderWithQuery(<McpToggle />)
  await openTokenMenu('Replace')
  await screen.findByLabelText('MCP token times')
  expect(screen.queryByText(/^MCP token expire/)).not.toBeInTheDocument()
  unmount()
  tokenShell(false)
  renderWithQuery(<McpToggle />)
  await openTokenMenu('Create')
  expect(screen.queryByText(/^MCP token expire/)).not.toBeInTheDocument()
})

test('expiryWarning counts the days left and knows an expired token', () => {
  const now = Date.parse('2026-11-08T12:00:00Z')
  const week = '2026-11-01T12:00:00Z'
  expect(expiryWarning(null, week, now)).toBeNull()
  // a 7 day token: 15% is 1.05 days, so the 3 days decide
  expect(expiryWarning('2026-11-11T12:00:01Z', week, now)).toBeNull()
  expect(expiryWarning('2026-11-11T12:00:00Z', week, now)).toBe('MCP token expires in 3 days')
  expect(expiryWarning('2026-11-08T13:00:00Z', week, now)).toBe('MCP token expires in 1 day')
  expect(expiryWarning('2026-11-08T12:00:00Z', week, now)).toBe('MCP token expired')
  // a 100 day token: 15% is 15 days
  const long = '2026-08-15T12:00:00Z'
  expect(expiryWarning('2026-11-23T12:00:01Z', long, now)).toBeNull()
  expect(expiryWarning('2026-11-23T12:00:00Z', long, now)).toBe('MCP token expires in 15 days')
  expect(expiryWarning('garbage', week, now)).toBeNull()
  // an unreadable creation time leaves the 3 days
  expect(expiryWarning('2026-11-11T12:00:00Z', 'garbage', now)).toBe('MCP token expires in 3 days')
})
