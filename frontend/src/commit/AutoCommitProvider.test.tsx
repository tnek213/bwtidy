import { act, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from '../App.tsx'
import type { GridData } from '../api/grid.ts'
import { accountQueryKey } from '../api/account.ts'
import { jsonResponse, mockFetch, newQueryClient, renderWithQuery, waitForPolls } from '../test-utils.tsx'
import type { AutoCommitStatus } from './autoCommit.ts'
import type { DiffRow, QueueEntry } from '../api/queue.ts'
import { showAccount, switchAccount } from '../shared/account.ts'

// The polls of the auto-commit come every second: the tests move a fake clock instead of waiting.
beforeEach(() => {
  localStorage.clear()
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const grid: GridData = {
  columns: [
    { id: 'name', label: 'name', group: 'item', secret: false, multi: false, readOnly: false },
    { id: 'folder', label: 'folder', group: 'item', secret: false, multi: false, readOnly: false },
    { id: 'login.username', label: 'login.username', group: 'login', secret: false, multi: false, readOnly: false },
    { id: 'card.brand', label: 'card.brand', group: 'card', secret: false, multi: false, readOnly: false },
  ],
  rows: [
    {
      id: 'item-1',
      revisionDate: '2026-10-01T10:00:00Z',
      cells: { name: 'Code forge', type: 'login', folder: 'Work', 'login.username': 'user' },
    },
    { id: 'item-3', revisionDate: '2026-10-03T10:00:00Z', cells: { name: 'Visa', type: 'card', 'card.brand': 'Visa' } },
  ],
}

function uiEntry(id: number, diff: DiffRow, status: QueueEntry['status'] = 'pending', result: QueueEntry['result'] = null): QueueEntry {
  return {
    id,
    createdAt: '2026-10-28T10:00:00Z',
    source: 'ui',
    summary: `Set ${diff.column} of "${diff.itemName}"`,
    status,
    decidedAt: null,
    operation: { kind: 'setLeaf' },
    diff: [diff],
    diffError: null,
    result,
  }
}

const usernameEdit: DiffRow = { itemId: 'item-1', itemName: 'Code forge', column: 'login.username', before: 'user', after: 'user.l' }
const brandEdit: DiffRow = { itemId: 'item-3', itemName: 'Visa', column: 'card.brand', before: 'Visa', after: 'Mastercard' }

// The status of a backend that ended commitCount commits so far.
function statusOf(pending: QueueEntry[], commitCount = 0, committing = false): AutoCommitStatus {
  return {
    afterSeconds: 10,
    maxEntries: 20,
    pending: pending.length,
    committing,
    nextCommitAt: pending.length > 0 ? new Date(Date.now() + 8000).toISOString() : null,
    commitCount,
    phase: null,
  }
}

// The grid after a commit wrote these cells.
function written(...edits: DiffRow[]): GridData {
  return {
    ...grid,
    rows: grid.rows.map((row) => {
      const cells = { ...row.cells }
      for (const edit of edits.filter((edit) => edit.itemId === row.id)) {
        cells[edit.column] = edit.after as string
      }
      return { ...row, cells }
    }),
  }
}

function gridCalls(fetchMock: ReturnType<typeof mockFetch>): number {
  return fetchMock.mock.calls.filter(([path]) => path === '/api/grid').length
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

function renderApp(routes: Parameters<typeof mockFetch>[0], queryClient = newQueryClient()) {
  const fetchMock = mockFetch({
    'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false }),
    'GET /api/health': () => jsonResponse(200, { status: 'ok', version: '261028 abc1234' }),
    'GET /api/templates': () => jsonResponse(200, { templates: [] }),
    'GET /api/template-matches': () => jsonResponse(200, {}),
    'GET /api/near-duplicates': () => jsonResponse(200, []),
    'GET /api/queue?status=applied': () => jsonResponse(200, []),
    'GET /api/queue?status=rejected': () => jsonResponse(200, []),
    ...routes,
  })
  renderWithQuery(<App />, queryClient)
  return fetchMock
}

test('the Queue button says the unsaved edits with a countdown, and Commit now commits them', async () => {
  let pending = [uiEntry(7, usernameEdit)]
  let commits = 0
  const fetchMock = renderApp({
    'GET /api/grid': () => jsonResponse(200, grid),
    'GET /api/queue?status=pending': () => jsonResponse(200, pending),
    'GET /api/queue?status=failed': () => jsonResponse(200, []),
    'GET /api/queue/auto-commit': () => jsonResponse(200, statusOf(pending, commits)),
    'POST /api/queue/commit': () => {
      pending = []
      commits += 1
      return jsonResponse(200, { applied: [7], failed: [], pending: [], backup: 'b.zip.age' })
    },
  })

  const queueButton = await screen.findByRole('button', { name: 'Queue, 1 unsaved edits' })
  fireEvent.mouseOver(queueButton)
  expect(await screen.findByRole('tooltip')).toHaveTextContent(/^1 unsaved edits\. Saved automatically in [0-9] s\.$/)

  fireEvent.click(screen.getByRole('button', { name: 'Commit now' }))

  await waitForPolls(() => expect(screen.queryByRole('button', { name: 'Queue, 1 unsaved edits' })).toBeNull())
  expect(fetchMock.mock.calls.filter(([path]) => path === '/api/queue/commit')).toHaveLength(1)
  expect(screen.queryByRole('button', { name: 'Commit now' })).toBeNull()
  // The commit ended, so the grid reloads.
  await waitForPolls(() => expect(gridCalls(fetchMock)).toBeGreaterThan(1))
})

function mcpEntry(id: number): QueueEntry {
  return { ...uiEntry(id, brandEdit), source: 'mcp', summary: `Set card.brand of "Visa"` }
}

test.each([
  ['UI edits only', [uiEntry(7, usernameEdit)], 'Queue, 1 unsaved edits', /^1 unsaved edits\. Saved automatically in [0-9] s\.$/],
  ['MCP proposals only', [mcpEntry(8), mcpEntry(9)], 'Queue, 2 MCP proposals to review', /^2 MCP proposals to review\.$/],
  [
    'both',
    [uiEntry(7, usernameEdit), mcpEntry(8)],
    'Queue, 2 pending: 1 unsaved edits, 1 MCP proposals to review',
    /^1 unsaved edits\. Saved automatically in [0-9] s\.1 MCP proposals to review\.$/,
  ],
  ['nothing', [], 'Queue, nothing pending', /^Nothing pending\.$/],
])('the Queue button tooltip explains its number: %s', async (_name, pending, label, tooltip) => {
  renderApp({
    'GET /api/grid': () => jsonResponse(200, grid),
    'GET /api/queue?status=pending': () => jsonResponse(200, pending),
    'GET /api/queue?status=failed': () => jsonResponse(200, []),
    'GET /api/queue/auto-commit': () => jsonResponse(200, statusOf(pending.filter((entry) => entry.source === 'ui'))),
  })

  const button = await screen.findByRole('button', { name: label })
  fireEvent.mouseOver(button)
  expect(await screen.findByRole('tooltip')).toHaveTextContent(tooltip)
  // The count is the badge, and the unsaved chip is gone.
  expect(button).toHaveTextContent(pending.length > 0 ? String(pending.length) : 'Queue')
  expect(screen.queryByText(/ unsaved$/)).toBeNull()
  expect(screen.queryByRole('button', { name: 'Commit now' }) !== null).toBe(pending.some((entry) => entry.source === 'ui'))
})

test('a commit that cannot run says Could not save with the reason and keeps the edit unsaved', async () => {
  const pending = [uiEntry(7, usernameEdit)]
  const fetchMock = renderApp({
    'GET /api/grid': () => jsonResponse(200, grid),
    'GET /api/queue?status=pending': () => jsonResponse(200, pending),
    'GET /api/queue?status=failed': () => jsonResponse(200, []),
    'GET /api/queue/auto-commit': () => jsonResponse(200, statusOf(pending)),
    'POST /api/queue/commit': () => jsonResponse(500, { detail: 'the backup failed' }),
  })
  await screen.findByRole('button', { name: 'Queue, 1 unsaved edits' })

  fireEvent.click(screen.getByRole('button', { name: 'Commit now' }))

  expect(await screen.findByText('Could not save: the backup failed')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Queue, 1 unsaved edits' })).toBeInTheDocument()
  expect(cell('item-1', 'login.username')).toHaveClass('bw-queued-cell')
  expect(gridCalls(fetchMock)).toBe(1)
})

test('a value a commit took stays in the grid until the grid reloaded after the commit', async () => {
  let pending = [uiEntry(7, usernameEdit)]
  let committing = false
  let commits = 0
  let vault = grid
  let gridHeld = Promise.resolve()
  const fetchMock = renderApp({
    'GET /api/grid': async () => {
      await gridHeld
      return jsonResponse(200, vault)
    },
    'GET /api/queue?status=pending': () => jsonResponse(200, pending),
    'GET /api/queue?status=failed': () => jsonResponse(200, []),
    'GET /api/queue/auto-commit': () => jsonResponse(200, statusOf(pending, commits, committing)),
  })
  const username = () => cell('item-1', 'login.username')
  await waitForPolls(() => expect(username()).toHaveClass('bw-queued-cell'))
  expect(username()).toHaveTextContent('user.l')

  // The commit takes the entry from the pending list and runs.
  pending = []
  committing = true
  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(username()).toHaveTextContent('user.l')
  expect(gridCalls(fetchMock)).toBe(1)

  // It ended, and the reload of the grid takes its time.
  let release: () => void = () => {}
  gridHeld = new Promise((resolve) => {
    release = resolve
  })
  vault = written(usernameEdit)
  committing = false
  commits = 1
  await waitForPolls(() => expect(gridCalls(fetchMock)).toBe(2))
  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(username()).toHaveTextContent('user.l')
  expect(username()).toHaveClass('bw-queued-cell')

  release()
  await waitForPolls(() => expect(username()).not.toHaveClass('bw-queued-cell'))
  expect(username()).toHaveTextContent('user.l')
  expect(gridCalls(fetchMock)).toBe(2)
})

test('two commits in a row keep both values until the grid reloaded after the second', async () => {
  let pending = [uiEntry(7, usernameEdit)]
  let committing = false
  let commits = 0
  let vault = grid
  const fetchMock = renderApp({
    'GET /api/grid': () => jsonResponse(200, vault),
    'GET /api/queue?status=pending': () => jsonResponse(200, pending),
    'GET /api/queue?status=failed': () => jsonResponse(200, []),
    'GET /api/queue/auto-commit': () => jsonResponse(200, statusOf(pending, commits, committing)),
  })
  const username = () => cell('item-1', 'login.username')
  const brand = () => cell('item-3', 'card.brand')
  await waitForPolls(() => expect(username()).toHaveClass('bw-queued-cell'))

  // The first commit runs, and an edit queued meanwhile waits for the next one.
  pending = []
  committing = true
  await act(() => vi.advanceTimersByTimeAsync(2000))
  pending = [uiEntry(8, brandEdit)]
  await waitForPolls(() => expect(brand()).toHaveClass('bw-queued-cell'))

  // The first ended and the second started before a status said no commit runs.
  pending = []
  commits = 1
  vault = written(usernameEdit)
  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(gridCalls(fetchMock)).toBe(1)
  expect(username()).toHaveTextContent('user.l')
  expect(brand()).toHaveTextContent('Mastercard')

  committing = false
  commits = 2
  vault = written(usernameEdit, brandEdit)
  await waitForPolls(() => expect(brand()).not.toHaveClass('bw-queued-cell'))
  expect(username()).not.toHaveClass('bw-queued-cell')
  expect(username()).toHaveTextContent('user.l')
  expect(brand()).toHaveTextContent('Mastercard')
  expect(gridCalls(fetchMock)).toBe(2)

  // A third commit reloads the grid again.
  pending = [uiEntry(9, { ...usernameEdit, before: 'user.l', after: 'user.k' })]
  await waitForPolls(() => expect(username()).toHaveTextContent('user.k'))
  pending = []
  commits = 3
  vault = written(usernameEdit, brandEdit, { ...usernameEdit, after: 'user.k' })
  await waitForPolls(() => expect(username()).not.toHaveClass('bw-queued-cell'))
  expect(username()).toHaveTextContent('user.k')
  expect(gridCalls(fetchMock)).toBe(3)
})

test('an edit that left the pending list without a commit (rejected) leaves the grid', async () => {
  let pending = [uiEntry(7, usernameEdit)]
  const fetchMock = renderApp({
    'GET /api/grid': () => jsonResponse(200, grid),
    'GET /api/queue?status=pending': () => jsonResponse(200, pending),
    'GET /api/queue?status=failed': () => jsonResponse(200, []),
    'GET /api/queue/auto-commit': () => jsonResponse(200, statusOf(pending)),
  })
  const username = () => cell('item-1', 'login.username')
  await waitForPolls(() => expect(username()).toHaveClass('bw-queued-cell'))

  pending = []
  await waitForPolls(() => expect(username()).not.toHaveClass('bw-queued-cell'))
  expect(username()).toHaveTextContent('user')
  expect(gridCalls(fetchMock)).toBe(1)
})

test('a commit that fails names the items and reasons, marks the conflict and tints the other cell', async () => {
  const conflicted = uiEntry(9, usernameEdit, 'failed', {
    error: 'conflicts',
    detail: 'Code forge changed elsewhere',
    conflicts: [{ itemId: 'item-1' }],
  })
  const refused = uiEntry(10, brandEdit, 'failed', { error: 'invalid', detail: 'card.brand is not allowed' })
  const changed: GridData = {
    ...grid,
    rows: [{ ...grid.rows[0], revisionDate: '2026-10-05T10:00:00Z', cells: { ...grid.rows[0].cells, folder: 'Home' } }, grid.rows[1]],
  }
  let commits = 0
  const fetchMock = renderApp({
    'GET /api/grid': () => jsonResponse(200, commits > 0 ? changed : grid),
    'GET /api/queue?status=pending': () =>
      jsonResponse(200, commits > 0 ? [] : [uiEntry(9, usernameEdit), uiEntry(10, brandEdit)]),
    'GET /api/queue?status=failed': () => jsonResponse(200, commits > 0 ? [conflicted, refused] : []),
    'GET /api/queue/auto-commit': () => jsonResponse(200, statusOf([], commits)),
  })
  await screen.findByRole('button', { name: 'Queue, 2 unsaved edits' })
  commits = 1

  const alert = await waitForPolls(() => screen.getByText(/Could not save 2 changes/))
  const snackbar = alert.closest<HTMLElement>('[role="alert"]')!
  expect(within(snackbar).getByText('Code forge: Code forge changed elsewhere')).toBeInTheDocument()
  expect(within(snackbar).getByText('Visa: card.brand is not allowed')).toBeInTheDocument()

  await waitForPolls(() => expect(cell('item-1', 'folder')).toHaveClass('bw-conflict-cell'))
  expect(cell('item-1', 'folder').closest('.ag-row')).toHaveClass('bw-conflict-row')
  expect(cell('item-3', 'card.brand')).toHaveClass('bw-failed-cell')

  fireEvent.click(within(snackbar).getByRole('button', { name: 'Close' }))

  await waitForPolls(() => expect(cell('item-3', 'card.brand')).not.toHaveClass('bw-failed-cell'))
  expect(cell('item-1', 'folder')).toHaveClass('bw-conflict-cell')
  // Reported once per browser.
  expect(JSON.parse(localStorage.getItem('bwtidy.seenFailedUiEntries') ?? '[]')).toEqual([9, 10])
  const reloads = gridCalls(fetchMock)
  commits = 2
  await waitForPolls(() => expect(gridCalls(fetchMock)).toBeGreaterThan(reloads))
  await act(() => vi.advanceTimersByTimeAsync(2000))
  expect(screen.queryByText(/Could not save/)).toBeNull()
})

test('a commit approved on the Queue page reloads the grid and reports its failed ui entries', async () => {
  const refused = uiEntry(10, brandEdit, 'failed', { error: 'invalid', detail: 'card.brand is not allowed' })
  let commits = 0
  const fetchMock = renderApp({
    'GET /api/grid': () => jsonResponse(200, grid),
    'GET /api/queue?status=pending': () => jsonResponse(200, commits > 0 ? [] : [uiEntry(10, brandEdit)]),
    'GET /api/queue?status=failed': () => jsonResponse(200, commits > 0 ? [refused] : []),
    'GET /api/queue/auto-commit': () => jsonResponse(200, statusOf([], commits)),
    'POST /api/queue/approve': () => {
      commits = 1
      return jsonResponse(200, { applied: [], failed: [10], pending: [], backup: 'b.zip.age' })
    },
  })
  await screen.findByRole('button', { name: 'Queue, 1 unsaved edits' })
  const reloads = gridCalls(fetchMock)

  fireEvent.click(screen.getByRole('button', { name: /^Queue/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'Approve all' }))
  const alert = await waitForPolls(() => screen.getByText('Could not save a change.'))
  expect(within(alert.closest<HTMLElement>('[role="alert"]')!).getByText('Visa: card.brand is not allowed')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Vault' }))
  await waitForPolls(() => expect(cell('item-3', 'card.brand')).toHaveClass('bw-failed-cell'))
  expect(gridCalls(fetchMock)).toBeGreaterThan(reloads)
  expect(JSON.parse(localStorage.getItem('bwtidy.seenFailedUiEntries') ?? '[]')).toEqual([10])
})

test('a failed entry is reported again for another account that has an entry with the same id', async () => {
  const seenKey = 'bwtidy.seenFailedUiEntries'
  const accountA = { server: 'https://vault.bitwarden.com', email: 'user@example.com' }
  const accountB = { server: 'https://vault.bitwarden.com', email: 'family@example.com' }
  const queryClient = newQueryClient()
  renderApp(
    {
      'GET /gateway/account': () => jsonResponse(200, { account: accountA, running: true }),
      'DELETE /gateway/account': () => jsonResponse(204),
      'DELETE /api/session': () => jsonResponse(204),
      'GET /api/grid': () => jsonResponse(200, grid),
      'GET /api/queue?status=pending': () => jsonResponse(200, []),
      'GET /api/queue?status=failed': () =>
        jsonResponse(200, [uiEntry(9, brandEdit, 'failed', { error: 'invalid', detail: 'card.brand is not allowed' })]),
      'GET /api/queue/auto-commit': () => jsonResponse(200, statusOf([])),
    },
    queryClient,
  )

  await screen.findByText(/Could not save a change/)
  expect(JSON.parse(localStorage.getItem(`${seenKey}:${accountA.server}:${accountA.email}`) ?? '[]')).toEqual([9])
  expect(localStorage.getItem(seenKey)).toBeNull()

  // The account switch unmounts the app, the next account's login mounts it again.
  await act(() => switchAccount(queryClient, true))
  expect(screen.queryByText(/Could not save a change/)).toBeNull()
  act(() => showAccount(queryClient, { kind: 'ready', account: accountB }))

  await screen.findByText(/Could not save a change/)
  expect(queryClient.getQueryData(accountQueryKey)).toMatchObject({ kind: 'ready', account: accountB })
  expect(JSON.parse(localStorage.getItem(`${seenKey}:${accountB.server}:${accountB.email}`) ?? '[]')).toEqual([9])
  expect(JSON.parse(localStorage.getItem(`${seenKey}:${accountA.server}:${accountA.email}`) ?? '[]')).toEqual([9])
})

test('dismissing a failed entry takes back its marks in the grid and leaves the others', async () => {
  const conflicted = uiEntry(9, usernameEdit, 'failed', {
    error: 'conflicts',
    detail: 'Code forge changed elsewhere',
    conflicts: [{ itemId: 'item-1' }],
  })
  const refused = uiEntry(10, brandEdit, 'failed', { error: 'invalid', detail: 'card.brand is not allowed' })
  const changed: GridData = {
    ...grid,
    rows: [{ ...grid.rows[0], revisionDate: '2026-10-05T10:00:00Z', cells: { ...grid.rows[0].cells, folder: 'Home' } }, grid.rows[1]],
  }
  let commits = 0
  let failed = [conflicted, refused]
  renderApp({
    'GET /api/grid': () => jsonResponse(200, commits > 0 ? changed : grid),
    'GET /api/queue?status=pending': () =>
      jsonResponse(200, commits > 0 ? [] : [uiEntry(9, usernameEdit), uiEntry(10, brandEdit)]),
    'GET /api/queue?status=failed': () => jsonResponse(200, commits > 0 ? failed : []),
    'GET /api/queue/auto-commit': () => jsonResponse(200, statusOf([], commits)),
    'POST /api/queue/dismiss': (init) => {
      const { ids } = JSON.parse(String(init.body)) as { ids: number[] }
      failed = failed.filter((entry) => !ids.includes(entry.id))
      return jsonResponse(200, { dismissed: ids, notFailed: [] })
    },
  })
  await screen.findByRole('button', { name: 'Queue, 2 unsaved edits' })
  commits = 1
  await waitForPolls(() => screen.getByText(/Could not save 2 changes/))
  await waitForPolls(() => expect(cell('item-1', 'folder')).toHaveClass('bw-conflict-cell'))

  fireEvent.click(screen.getByRole('button', { name: /^Queue/ }))
  fireEvent.click(await screen.findByRole('tab', { name: /^Failed/ }))
  fireEvent.click(within(await screen.findByTestId('queue-entry-9')).getByRole('button', { name: 'Dismiss' }))
  await waitForPolls(() => expect(screen.queryByTestId('queue-entry-9')).toBeNull())

  fireEvent.click(screen.getByRole('button', { name: 'Vault' }))
  await waitForPolls(() => expect(cell('item-3', 'card.brand')).toHaveClass('bw-failed-cell'))
  expect(cell('item-1', 'folder')).not.toHaveClass('bw-conflict-cell')
  expect(cell('item-1', 'folder').closest('.ag-row')).not.toHaveClass('bw-conflict-row')
  const alert = screen.getByText('Could not save a change.')
  const snackbar = alert.closest<HTMLElement>('[role="alert"]')!
  expect(within(snackbar).queryByText('Code forge: Code forge changed elsewhere')).toBeNull()
  expect(within(snackbar).getByText('Visa: card.brand is not allowed')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /^Queue/ }))
  fireEvent.click(await screen.findByRole('tab', { name: /^Failed/ }))
  fireEvent.click(within(await screen.findByTestId('queue-entry-10')).getByRole('button', { name: 'Dismiss' }))
  await waitForPolls(() => expect(screen.queryByTestId('queue-entry-10')).toBeNull())

  fireEvent.click(screen.getByRole('button', { name: 'Vault' }))
  await waitForPolls(() => expect(cell('item-3', 'card.brand')).not.toHaveClass('bw-failed-cell'))
  expect(screen.queryByText(/Could not save a change/)).toBeNull()
})

test('log out says it saves the unsaved edits while the request runs', async () => {
  let finish: () => void = () => {}
  renderApp({
    'GET /api/grid': () => jsonResponse(200, grid),
    'GET /api/queue?status=pending': () => jsonResponse(200, [uiEntry(7, usernameEdit)]),
    'GET /api/queue?status=failed': () => jsonResponse(200, []),
    'GET /api/queue/auto-commit': () => jsonResponse(200, statusOf([uiEntry(7, usernameEdit)])),
    'DELETE /api/session': () =>
      new Promise<Response>((resolve) => {
        finish = () => resolve(jsonResponse(204))
      }) as unknown as Response,
  })
  await screen.findByRole('button', { name: 'Queue, 1 unsaved edits' })

  fireEvent.click(screen.getByRole('button', { name: 'Log out' }))
  const dialog = await screen.findByRole('dialog', { name: 'Log out' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Log out' }))

  expect(await within(dialog).findByText('Saving your changes')).toBeInTheDocument()
  finish()
  expect(await screen.findByLabelText(/Master password/)).toBeInTheDocument()
})
