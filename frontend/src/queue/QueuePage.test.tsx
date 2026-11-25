import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from '../App.tsx'
import { MASK } from '../shared/values.ts'
import { accountQueryKey } from '../api/account.ts'
import { jsonResponse, mockFetch, newQueryClient, renderWithQuery } from '../test-utils.tsx'
import type { QueueEntry } from '../api/queue.ts'
import QueuePage from './QueuePage.tsx'

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// The Queue page loads every tab's list for the unread counts.
const EMPTY_LISTS = {
  'GET /api/queue?status=applied': () => jsonResponse(200, []),
  'GET /api/queue?status=rejected': () => jsonResponse(200, []),
  'GET /api/queue?status=failed': () => jsonResponse(200, []),
}

function entry(id: number, summary: string, extra: Partial<QueueEntry> = {}): QueueEntry {
  return {
    id,
    createdAt: '2026-10-26T12:00:00Z',
    source: 'mcp',
    summary,
    status: 'pending',
    decidedAt: null,
    operation: { kind: 'renameField' },
    diff: [],
    diffError: null,
    result: null,
    ...extra,
  }
}

const RENAME = entry(1, 'Rename Password to password', {
  diff: [
    { itemId: 'item-1', itemName: 'Code forge', column: 'fields.Password', before: { masked: true }, after: null },
    { itemId: 'item-1', itemName: 'Code forge', column: 'fields.password', before: null, after: { masked: true } },
    { itemId: 'item-1', itemName: 'Code forge', column: 'folder', before: 'Old', after: 'Servers' },
  ],
})
const TRASH = entry(2, 'Trash the old router', {
  operation: { kind: 'trashItem' },
  diff: [{ itemId: 'item-2', itemName: 'Router', column: 'trash', before: false, after: true }],
})
const MOVE = entry(3, 'Move NAS to Servers', {
  operation: { kind: 'moveToFolder' },
  diff: [{ itemId: 'item-3', itemName: 'NAS', column: 'folder', before: null, after: 'Servers' }],
})

test('renders each diff with secret values masked', async () => {
  mockFetch({ ...EMPTY_LISTS, 'GET /api/queue?status=pending': () => jsonResponse(200, [RENAME]) })
  renderWithQuery(<QueuePage />)

  const table = await screen.findByRole('table', { name: 'Changes of entry 1' })
  const rows = within(table).getAllByRole('row')
  expect(rows).toHaveLength(4)
  expect(rows[1]).toHaveTextContent('Code forge')
  expect(rows[1]).toHaveTextContent('fields.Password')
  expect(rows[1]).toHaveTextContent(MASK)
  expect(rows[1]).toHaveTextContent('none')
  expect(rows[2]).toHaveTextContent(MASK)
  expect(rows[3]).toHaveTextContent('Old')
  expect(rows[3]).toHaveTextContent('Servers')
  expect(within(table).getAllByTestId('masked-value')).toHaveLength(2)
  expect(screen.getByText('Rename Password to password')).toBeInTheDocument()
  expect(screen.getByText(/from mcp/)).toBeInTheDocument()
})

test('a setUris entry shows the old and new URI lists with their match words', async () => {
  const uris = entry(7, 'Set the URIs of "Mail" (2)', {
    operation: { kind: 'setUris' },
    diff: [
      {
        itemId: 'item-7',
        itemName: 'Mail',
        column: 'login.uris',
        before: ['https://mail.example.test'],
        after: ['https://mail.example.test', '^https://webmail\\.'],
        uris: {
          before: [{ uri: 'https://mail.example.test', match: null }],
          after: [
            { uri: 'https://mail.example.test', match: 'host' },
            { uri: '^https://webmail\\.', match: 'regex' },
          ],
        },
      },
    ],
  })
  mockFetch({ ...EMPTY_LISTS, 'GET /api/queue?status=pending': () => jsonResponse(200, [uris]) })
  renderWithQuery(<QueuePage />)

  const table = await screen.findByRole('table', { name: 'Changes of entry 7' })
  const [, row] = within(table).getAllByRole('row')
  const cells = within(row).getAllByRole('cell')
  expect(cells[1]).toHaveTextContent('login.uris')
  const before = within(cells[2]).getAllByRole('listitem')
  expect(before.map((line) => line.textContent)).toEqual(['https://mail.example.test (default)'])
  const after = within(cells[3]).getAllByRole('listitem')
  expect(after.map((line) => line.textContent)).toEqual([
    'https://mail.example.test (host)',
    '^https://webmail\\. (regex)',
  ])
})

test('approve selected posts the chosen ids and names what was applied', async () => {
  let pending = [RENAME, TRASH, MOVE]
  const fetchMock = mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, pending),
    'GET /api/queue?status=failed': () => jsonResponse(200, []),
    'POST /api/queue/approve': () => {
      pending = [RENAME]
      return jsonResponse(200, { applied: [2, 3], failed: [], pending: [], backup: '20261026T120000Z.zip.age' })
    },
  })
  renderWithQuery(<QueuePage />)

  const approve = await screen.findByRole('button', { name: 'Approve selected' })
  expect(approve).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: 'Trash the old router' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'Move NAS to Servers' }))
  fireEvent.click(approve)

  const result = await screen.findByTestId('approve-result')
  expect(result).toHaveTextContent('Applied 2 entries (backup 20261026T120000Z.zip.age)')
  expect(result).toHaveTextContent('Trash the old router')
  expect(result).toHaveTextContent('Move NAS to Servers')
  const call = fetchMock.mock.calls.find(([path]) => path === '/api/queue/approve')
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ ids: [2, 3] })
  await waitFor(() => expect(screen.queryByTestId('queue-entry-2')).not.toBeInTheDocument())
})

test('approve all and reject selected send every id and the chosen ones', async () => {
  const fetchMock = mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, [RENAME, TRASH]),
    'GET /api/queue?status=failed': () => jsonResponse(200, []),
    'POST /api/queue/approve': () => jsonResponse(200, { applied: [1, 2], failed: [], pending: [], backup: 'b' }),
    'POST /api/queue/reject': () => jsonResponse(200, { rejected: [1] }),
  })
  renderWithQuery(<QueuePage />)

  fireEvent.click(await screen.findByRole('checkbox', { name: 'Rename Password to password' }))
  fireEvent.click(screen.getByRole('button', { name: 'Reject selected' }))
  expect(await screen.findByText('Rejected 1 entry.')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Approve all' }))
  await screen.findByTestId('approve-result')

  const bodies = fetchMock.mock.calls
    .filter(([path, init]) => String(path).startsWith('/api/queue/') && init?.method === 'POST')
    .map(([path, init]) => [path, JSON.parse(String(init?.body))])
  expect(bodies).toEqual([
    ['/api/queue/reject', { ids: [1] }],
    ['/api/queue/approve', { ids: [1, 2] }],
  ])
})

test('failed entries show their reason after approval and in the failed tab', async () => {
  const failed = entry(3, 'Move NAS to Servers', {
    status: 'failed',
    decidedAt: '2026-10-26T12:05:00Z',
    result: { error: 'conflicts', detail: '1 item changed since the change was queued' },
  })
  mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, [MOVE]),
    'GET /api/queue?status=failed': () => jsonResponse(200, [failed]),
    'POST /api/queue/approve': () => jsonResponse(200, { applied: [], failed: [3], pending: [], backup: null }),
  })
  renderWithQuery(<QueuePage />)

  fireEvent.click(await screen.findByRole('button', { name: 'Approve all' }))

  const result = await screen.findByTestId('approve-result')
  await waitFor(() =>
    expect(result).toHaveTextContent('Move NAS to Servers: 1 item changed since the change was queued'),
  )

  fireEvent.click(screen.getByRole('tab', { name: /^Failed/ }))
  const card = await screen.findByTestId('queue-entry-3')
  expect(card).toHaveTextContent('Failed: 1 item changed since the change was queued')
})

test('dismiss deletes a failed entry, only failed entries have the button', async () => {
  let failed = [
    entry(3, 'Move NAS to Servers', {
      status: 'failed',
      decidedAt: '2026-10-26T12:05:00Z',
      result: { error: 'conflicts', detail: '1 item changed since the change was queued' },
    }),
    entry(4, 'Trash the old router', {
      status: 'failed',
      decidedAt: '2026-10-26T12:06:00Z',
      result: { error: 'invalid', detail: 'gone' },
    }),
  ]
  const fetchMock = mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, [MOVE]),
    'GET /api/queue?status=failed': () => jsonResponse(200, failed),
    'POST /api/queue/dismiss': () => {
      failed = failed.filter((one) => one.id !== 3)
      return jsonResponse(200, { dismissed: [3], notFailed: [] })
    },
  })
  renderWithQuery(<QueuePage />)

  await screen.findByTestId('queue-entry-3')
  expect(within(screen.getByTestId('queue-entry-3')).queryByRole('button', { name: 'Dismiss' })).toBeNull()

  fireEvent.click(screen.getByRole('tab', { name: /^Failed/ }))
  const card = await screen.findByTestId('queue-entry-3')
  fireEvent.click(within(card).getByRole('button', { name: 'Dismiss' }))

  await waitFor(() => expect(screen.queryByTestId('queue-entry-3')).toBeNull())
  expect(screen.getByTestId('queue-entry-4')).toBeInTheDocument()
  const bodies = fetchMock.mock.calls
    .filter(([path]) => path === '/api/queue/dismiss')
    .map(([, init]) => JSON.parse(String(init?.body)))
  expect(bodies).toEqual([{ ids: [3] }])
})

test('a dismiss the backend refuses says so and keeps the entry', async () => {
  const failed = entry(3, 'Move NAS to Servers', { status: 'failed', result: { error: 'invalid', detail: 'gone' } })
  mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/queue?status=failed': () => jsonResponse(200, [failed]),
    'POST /api/queue/dismiss': () => jsonResponse(500, { detail: 'disk full' }),
  })
  renderWithQuery(<QueuePage />)

  fireEvent.click(await screen.findByRole('tab', { name: /^Failed/ }))
  fireEvent.click(within(await screen.findByTestId('queue-entry-3')).getByRole('button', { name: 'Dismiss' }))

  expect(await screen.findByText(/^Could not dismiss/)).toBeInTheDocument()
  expect(screen.getByTestId('queue-entry-3')).toBeInTheDocument()
})

function failedEntry(id: number, summary: string, result: QueueEntry['result'], extra: Partial<QueueEntry> = {}) {
  return entry(id, summary, { status: 'failed', decidedAt: '2026-10-26T12:05:00Z', result, ...extra })
}

const NOTES_EDIT = {
  operation: { kind: 'setLeaf' },
  diff: [{ itemId: 'item-6', itemName: 'Mail', column: 'notes', before: 'old', after: 'mine' }],
}

const CONFLICT = failedEntry(
  6,
  'Set notes of "Mail"',
  {
    error: 'conflicts',
    detail: '2 items changed since the change was queued',
    conflicts: [{ itemId: 'item-6' }, { itemId: 'item-7' }],
  },
  {
    ...NOTES_EDIT,
    diff: [
      ...NOTES_EDIT.diff,
      { itemId: 'item-7', itemName: 'NAS', column: 'folder', before: null, after: 'Servers' },
    ],
  },
)

function retryBodies(fetchMock: ReturnType<typeof mockFetch>) {
  return fetchMock.mock.calls
    .filter(([path]) => path === '/api/queue/retry')
    .map(([, init]) => JSON.parse(String(init?.body)))
}

test('a failed entry has Retry unless it was cut off, is a merge conflict or lost its value', async () => {
  const failed = [
    failedEntry(1, 'Write failed', { error: 'writeFailed', detail: 'bw said no' }),
    failedEntry(2, 'Invalid', { error: 'invalid', detail: 'the value cannot be decrypted' }),
    CONFLICT,
    failedEntry(3, 'Cut off', { error: 'interrupted', detail: 'The commit was cut off' }),
    failedEntry(4, 'Merge', { error: 'mergeConflicts', detail: 'a choice is needed' }),
    failedEntry(5, 'Gone', { error: 'conflicts', detail: '1 item changed' }, { valueGone: true }),
  ]
  mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/queue?status=failed': () => jsonResponse(200, failed),
  })
  renderWithQuery(<QueuePage />)

  fireEvent.click(await screen.findByRole('tab', { name: /^Failed/ }))
  await screen.findByTestId('queue-entry-1')
  const buttons = (id: number) =>
    within(screen.getByTestId(`queue-entry-${id}`))
      .getAllByRole('button')
      .map((button) => button.textContent)
  for (const id of [1, 2, 6]) {
    expect(buttons(id)).toEqual(['Retry', 'Dismiss'])
  }
  for (const id of [3, 4, 5]) {
    expect(buttons(id)).toEqual(['Dismiss'])
  }
  expect(screen.getByTestId('queue-entry-4')).toHaveTextContent('Propose the merge again with a choice per item.')
  expect(screen.getByTestId('queue-entry-5')).toHaveTextContent(
    'The value is gone after a restart. Type it again in the grid.',
  )
  expect(screen.getByTestId('queue-entry-3')).not.toHaveTextContent(/Propose the merge|value is gone/)
})

test('Retry of a write failure applies it at once without overwrite', async () => {
  let failed = [failedEntry(1, 'Set notes of "Mail"', { error: 'writeFailed', detail: 'bw said no' }, NOTES_EDIT)]
  const fetchMock = mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/queue?status=failed': () => jsonResponse(200, failed),
    'GET /api/queue/auto-commit': () => jsonResponse(200, { afterSeconds: 10, maxEntries: 20 }),
    'POST /api/queue/retry': () => {
      failed = []
      return jsonResponse(200, { applied: [1], failed: [], pending: [], backup: 'b.zip.age' })
    },
  })
  renderWithQuery(<QueuePage />)

  fireEvent.click(await screen.findByRole('tab', { name: /^Failed/ }))
  fireEvent.click(within(await screen.findByTestId('queue-entry-1')).getByRole('button', { name: 'Retry' }))

  const result = await screen.findByTestId('approve-result')
  expect(result).toHaveTextContent('Applied 1 entry (backup b.zip.age)')
  expect(result).toHaveTextContent('Set notes of "Mail"')
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(retryBodies(fetchMock)).toEqual([{ ids: [1], overwrite: false }])
  await waitFor(() => expect(screen.queryByTestId('queue-entry-1')).toBeNull())
})

test('Retry of a conflict asks first, one line per item and column, and then overwrites', async () => {
  const fetchMock = mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/queue?status=failed': () => jsonResponse(200, [CONFLICT]),
    'GET /api/queue/auto-commit': () => jsonResponse(200, { afterSeconds: 10, maxEntries: 20 }),
    'POST /api/queue/retry': () => jsonResponse(200, { applied: [6], failed: [], pending: [], backup: 'b.zip.age' }),
  })
  renderWithQuery(<QueuePage />)

  fireEvent.click(await screen.findByRole('tab', { name: /^Failed/ }))
  const retry = () => within(screen.getByTestId('queue-entry-6')).getByRole('button', { name: 'Retry' })
  await screen.findByTestId('queue-entry-6')
  fireEvent.click(retry())
  const dialog = await screen.findByRole('dialog')
  const lines = within(dialog)
    .getAllByText(/.+/, { selector: 'p' })
    .map((line) => line.textContent)
  expect(lines).toEqual([
    "Mail's notes was changed elsewhere since you edited it.",
    "NAS's folder was changed elsewhere since you edited it.",
    'Retry writes your value over that change. A backup is taken first, so the other value can be restored from the Change log.',
  ])
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(retryBodies(fetchMock)).toEqual([])

  fireEvent.click(retry())
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Retry' }))
  expect(await screen.findByTestId('approve-result')).toHaveTextContent('Applied 1 entry')
  expect(retryBodies(fetchMock)).toEqual([{ ids: [6], overwrite: true }])
})

test('Retry of a folder conflict names the folder', async () => {
  const folder = failedEntry(
    8,
    'Rename the folder "Empty" to "Spare"',
    {
      error: 'conflicts',
      detail: 'the folder changed since the change was queued',
      conflicts: [{ folderId: 'f2', expected: 'Empty', actual: 'Elsewhere' }],
    },
    {
      operation: { kind: 'renameFolder' },
      diff: [{ itemId: null, itemName: 'Empty', column: 'folder', before: 'Empty', after: 'Spare' }],
    },
  )
  mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/queue?status=failed': () => jsonResponse(200, [folder]),
    'GET /api/queue/auto-commit': () => jsonResponse(200, { afterSeconds: 10, maxEntries: 20 }),
  })
  renderWithQuery(<QueuePage />)

  fireEvent.click(await screen.findByRole('tab', { name: /^Failed/ }))
  fireEvent.click(within(await screen.findByTestId('queue-entry-8')).getByRole('button', { name: 'Retry' }))
  const dialog = await screen.findByRole('dialog')
  const lines = within(dialog)
    .getAllByText(/.+/, { selector: 'p' })
    .map((line) => line.textContent)
  expect(lines).toEqual([
    'The folder Empty was changed elsewhere since you edited it.',
    'Retry writes your value over that change. A backup is taken first, so the other value can be restored from the Change log.',
  ])
})

test('a retry the backend refuses says why', async () => {
  mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/queue?status=failed': () =>
      jsonResponse(200, [failedEntry(1, 'Write failed', { error: 'writeFailed', detail: 'bw said no' })]),
    'GET /api/queue/auto-commit': () => jsonResponse(200, { afterSeconds: 10, maxEntries: 20 }),
    'POST /api/queue/retry': () => jsonResponse(409, { detail: 'entry 1 is not failed' }),
  })
  renderWithQuery(<QueuePage />)

  fireEvent.click(await screen.findByRole('tab', { name: /^Failed/ }))
  fireEvent.click(within(await screen.findByTestId('queue-entry-1')).getByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('Could not retry: entry 1 is not failed')).toBeInTheDocument()
})

test('a rejected entry with a detail shows it', async () => {
  const refused = entry(5, 'Refused proposal for "Mail": it would put a secret value of the item into name', {
    status: 'rejected',
    decidedAt: '2026-10-26T12:05:00Z',
    operation: { kind: 'setLeaf', column: 'name', value: { masked: true } },
    diff: [{ itemId: 'item-5', itemName: 'Mail', column: 'name', before: 'Mail', after: { masked: true } }],
    result: {
      error: 'secretValue',
      detail: 'the proposal would put a secret value of the item into a non-secret column',
    },
  })
  mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/queue?status=rejected': () => jsonResponse(200, [refused]),
  })
  renderWithQuery(<QueuePage />)

  fireEvent.click(await screen.findByRole('tab', { name: /^Rejected/ }))
  const card = await screen.findByTestId('queue-entry-5')
  expect(within(card).getByRole('alert')).toHaveTextContent(
    'Refused: the proposal would put a secret value of the item into a non-secret column',
  )
  expect(within(card).getByTestId('masked-value')).toBeInTheDocument()
})

test('a pending entry that no longer fits the vault says why', async () => {
  mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () =>
      jsonResponse(200, [entry(4, 'Rename a gone item', { diffError: 'item gone-1 is not in the vault' })]),
  })
  renderWithQuery(<QueuePage />)
  expect(await screen.findByText(/no longer fits the vault: item gone-1 is not in the vault/)).toBeInTheDocument()
})

test('the app bar badge counts the pending entries and opens the queue', async () => {
  const fetchMock = mockFetch({
    ...EMPTY_LISTS,
    'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false }),
    'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
    'GET /api/queue?status=pending': () => jsonResponse(200, [RENAME, TRASH, MOVE]),
  })
  renderWithQuery(<App />)

  const button = await screen.findByRole('button', { name: 'Queue, 3 MCP proposals to review' })
  expect(button).toHaveTextContent('3')
  const poll = fetchMock.mock.calls.find(([path]) => path === '/api/queue?status=pending')
  expect(poll?.[1]?.headers).toMatchObject({ 'X-Bwtidy-Background': '1' })

  fireEvent.click(button)
  expect(await screen.findByRole('heading', { name: 'Queue' })).toBeInTheDocument()
  expect(screen.getByRole('table', { name: 'Changes of entry 2' })).toBeInTheDocument()
})

test('the badge refreshes every 10 seconds', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    let pending = [RENAME]
    mockFetch({
      'GET /api/session': () => jsonResponse(200, { state: 'unlocked', mcpEnabled: false }),
      'GET /api/grid': () => jsonResponse(200, { columns: [], rows: [] }),
      'GET /api/queue?status=pending': () => jsonResponse(200, pending),
    })
    renderWithQuery(<App />)
    await screen.findByRole('button', { name: 'Queue, 1 MCP proposals to review' })

    pending = [RENAME, TRASH]
    await vi.advanceTimersByTimeAsync(10_000)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Queue, 2 MCP proposals to review' })).toBeInTheDocument())
  } finally {
    vi.useRealTimers()
  }
})

test('the auto-commit box shows the settings and saves them', async () => {
  let saved = { afterSeconds: 10, maxEntries: 20 }
  const fetchMock = mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/queue/auto-commit': () => jsonResponse(200, { ...saved, pending: 0, committing: false, nextCommitAt: null }),
    'PUT /api/queue/auto-commit': (init) => {
      saved = JSON.parse(String(init.body))
      return jsonResponse(200, saved)
    },
  })
  renderWithQuery(<QueuePage />)

  const box = await screen.findByRole('form', { name: 'Auto-commit' })
  const after = within(box).getByRole('textbox', { name: 'After seconds' })
  const max = within(box).getByRole('textbox', { name: 'Or at changes' })
  expect(after).toHaveValue('10')
  expect(max).toHaveValue('20')

  fireEvent.change(after, { target: { value: '0' } })
  expect(within(box).getByRole('button', { name: 'Save' })).toBeDisabled()
  fireEvent.change(after, { target: { value: '30' } })
  fireEvent.change(max, { target: { value: '5' } })
  fireEvent.click(within(box).getByRole('button', { name: 'Save' }))

  expect(await within(box).findByText('Saved.')).toBeInTheDocument()
  const put = fetchMock.mock.calls.find(([path, init]) => path === '/api/queue/auto-commit' && init?.method === 'PUT')
  expect(JSON.parse(String(put?.[1]?.body))).toEqual({ afterSeconds: 30, maxEntries: 5 })
})

test('the auto-commit box shows a refusal of the backend', async () => {
  mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/queue/auto-commit': () =>
      jsonResponse(200, { afterSeconds: 10, maxEntries: 20, pending: 0, committing: false, nextCommitAt: null }),
    'PUT /api/queue/auto-commit': () => jsonResponse(422, { detail: 'afterSeconds is 1 to 3600' }),
  })
  renderWithQuery(<QueuePage />)

  const box = await screen.findByRole('form', { name: 'Auto-commit' })
  fireEvent.click(within(box).getByRole('button', { name: 'Save' }))

  expect(await within(box).findByText('Could not save: afterSeconds is 1 to 3600')).toBeInTheDocument()
})

// Fake timers that still let the testing library's own waiting run.
async function withFakeTimers(body: () => Promise<void>) {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    await body()
  } finally {
    vi.useRealTimers()
  }
}

function tabNames(): string[] {
  const tabs = within(screen.getByRole('tablist', { name: 'Queue entries' })).getAllByRole('tab')
  return tabs.map((tab) => tab.textContent ?? '')
}

const APPLIED_MOVE = { ...MOVE, status: 'applied' as const, decidedAt: '2026-10-26T12:05:00Z' }
const FAILED_ONE = entry(7, 'Rename the router', { status: 'failed', result: { error: 'invalid', detail: 'gone' } })

test('each tab counts its unread entries', async () => {
  mockFetch({
    ...EMPTY_LISTS,
    'GET /api/queue?status=pending': () => jsonResponse(200, [RENAME, TRASH]),
    'GET /api/queue?status=applied': () => jsonResponse(200, [APPLIED_MOVE]),
    'GET /api/queue?status=failed': () => jsonResponse(200, [FAILED_ONE]),
  })
  renderWithQuery(<QueuePage />)

  await waitFor(() => expect(tabNames()).toEqual(['Pending (2)', 'Applied (1)', 'Rejected', 'Failed (1)']))
})

test('a tab shown for 4 seconds counts as read, also after a reload', async () => {
  await withFakeTimers(async () => {
    mockFetch({
      ...EMPTY_LISTS,
      'GET /api/queue?status=pending': () => jsonResponse(200, [RENAME, TRASH]),
      'GET /api/queue?status=applied': () => jsonResponse(200, [APPLIED_MOVE]),
    })
    const { unmount } = renderWithQuery(<QueuePage />)
    await waitFor(() => expect(tabNames()).toEqual(['Pending (2)', 'Applied (1)', 'Rejected', 'Failed']))

    await act(() => vi.advanceTimersByTimeAsync(4_000))

    expect(tabNames()).toEqual(['Pending', 'Applied (1)', 'Rejected', 'Failed'])
    unmount()
    renderWithQuery(<QueuePage />)
    await waitFor(() => expect(tabNames()).toEqual(['Pending', 'Applied (1)', 'Rejected', 'Failed']))
  })
})

test('a tab shown for 3 seconds stays unread, switching away starts over', async () => {
  await withFakeTimers(async () => {
    mockFetch({
      ...EMPTY_LISTS,
      'GET /api/queue?status=pending': () => jsonResponse(200, [RENAME, TRASH]),
      'GET /api/queue?status=applied': () => jsonResponse(200, [APPLIED_MOVE]),
    })
    renderWithQuery(<QueuePage />)
    await waitFor(() => expect(tabNames()).toEqual(['Pending (2)', 'Applied (1)', 'Rejected', 'Failed']))

    await act(() => vi.advanceTimersByTimeAsync(3_000))
    expect(tabNames()).toEqual(['Pending (2)', 'Applied (1)', 'Rejected', 'Failed'])

    fireEvent.click(screen.getByRole('tab', { name: 'Applied (1)' }))
    await act(() => vi.advanceTimersByTimeAsync(3_000))
    expect(tabNames()).toEqual(['Pending (2)', 'Applied (1)', 'Rejected', 'Failed'])

    await act(() => vi.advanceTimersByTimeAsync(1_000))
    expect(tabNames()).toEqual(['Pending (2)', 'Applied', 'Rejected', 'Failed'])
    expect(JSON.parse(localStorage.getItem('bwtidy.seenQueueEntries') ?? '{}')).toMatchObject({
      pending: [],
      applied: [3],
    })
  })
})

test('the entries one account has read stay unread for another account with the same ids', async () => {
  await withFakeTimers(async () => {
    mockFetch({ ...EMPTY_LISTS, 'GET /api/queue?status=pending': () => jsonResponse(200, [RENAME, TRASH]) })
    const accountA = { server: 'https://vault.bitwarden.com', email: 'user@example.com' }
    const accountB = { server: 'https://vault.bitwarden.com', email: 'family@example.com' }
    const clientA = newQueryClient()
    clientA.setQueryData(accountQueryKey, { kind: 'ready', account: accountA })
    const { unmount } = renderWithQuery(<QueuePage />, clientA)
    await waitFor(() => expect(tabNames()[0]).toBe('Pending (2)'))
    await act(() => vi.advanceTimersByTimeAsync(4_000))
    expect(tabNames()[0]).toBe('Pending')
    const keyA = `bwtidy.seenQueueEntries:${accountA.server}:${accountA.email}`
    expect(JSON.parse(localStorage.getItem(keyA) ?? '{}')).toMatchObject({ pending: [1, 2] })
    expect(localStorage.getItem('bwtidy.seenQueueEntries')).toBeNull()
    unmount()

    const clientB = newQueryClient()
    clientB.setQueryData(accountQueryKey, { kind: 'ready', account: accountB })
    renderWithQuery(<QueuePage />, clientB)
    await waitFor(() => expect(tabNames()[0]).toBe('Pending (2)'))
    await act(() => vi.advanceTimersByTimeAsync(4_000))
    expect(tabNames()[0]).toBe('Pending')
    expect(JSON.parse(localStorage.getItem(keyA) ?? '{}')).toMatchObject({ pending: [1, 2] })
    expect(localStorage.getItem(`bwtidy.seenQueueEntries:${accountB.server}:${accountB.email}`)).not.toBeNull()
  })
})

test('a hidden browser tab marks nothing as read', async () => {
  await withFakeTimers(async () => {
    mockFetch({ ...EMPTY_LISTS, 'GET /api/queue?status=pending': () => jsonResponse(200, [RENAME]) })
    renderWithQuery(<QueuePage />)
    await waitFor(() => expect(tabNames()[0]).toBe('Pending (1)'))

    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    try {
      act(() => {
        document.dispatchEvent(new Event('visibilitychange'))
      })
      await act(() => vi.advanceTimersByTimeAsync(5_000))
      expect(tabNames()[0]).toBe('Pending (1)')
    } finally {
      visibility.mockRestore()
    }
  })
})

test('an entry that moves from Pending to Applied leaves the count of Pending for that of Applied', async () => {
  await withFakeTimers(async () => {
    let pending = [RENAME, TRASH]
    let applied: QueueEntry[] = []
    mockFetch({
      ...EMPTY_LISTS,
      'GET /api/queue?status=pending': () => jsonResponse(200, pending),
      'GET /api/queue?status=applied': () => jsonResponse(200, applied),
    })
    renderWithQuery(<QueuePage />)
    await waitFor(() => expect(tabNames()).toEqual(['Pending (2)', 'Applied', 'Rejected', 'Failed']))
    fireEvent.click(screen.getByRole('tab', { name: 'Rejected' }))

    pending = [RENAME]
    applied = [{ ...TRASH, status: 'applied', decidedAt: '2026-10-26T12:05:00Z' }]
    await act(() => vi.advanceTimersByTimeAsync(10_000))

    await waitFor(() => expect(tabNames()).toEqual(['Pending (1)', 'Applied (1)', 'Rejected', 'Failed']))
  })
})

test('each tab says what its entries mean', async () => {
  mockFetch({ ...EMPTY_LISTS, 'GET /api/queue?status=pending': () => jsonResponse(200, []) })
  renderWithQuery(<QueuePage />)

  expect(await screen.findByText(/^Waiting for your approval \(agent proposals\) or for the next auto-commit/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: /^Applied/ }))
  expect(screen.getByText('Written to the vault.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: /^Rejected/ }))
  expect(screen.getByText(/^Not written: you rejected them, or bwtidy refused a proposal/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: /^Failed/ }))
  expect(screen.getByText(/^Approved, but not written\. The reason says why\./)).toBeInTheDocument()
})
