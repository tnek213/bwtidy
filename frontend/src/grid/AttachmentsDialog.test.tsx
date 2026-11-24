import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { QueueEntry } from '../api/queue.ts'
import { jsonResponse, renderWithQuery } from '../test-utils.tsx'
import AttachmentsDialog from './AttachmentsDialog.tsx'
import type { GridRow } from '../api/grid.ts'
import { gridAnswer, gridColumn, renderGrid } from './testSupport.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

const row: GridRow = {
  id: 'item-1',
  revisionDate: '2026-11-01T10:00:00Z',
  cells: { name: 'Internal CA', attachments: ['ca-notes.txt', 'Root.pem'] },
  attachments: [
    { id: 'att-1', fileName: 'ca-notes.txt' },
    { id: 'att-2', fileName: 'Root.pem' },
  ],
}

function open(pending: QueueEntry[] = []) {
  const onApply = vi.fn()
  const onClose = vi.fn()
  renderWithQuery(<AttachmentsDialog row={row} pending={pending} onClose={onClose} onApply={onApply} />)
  return { onApply, onClose, dialog: screen.getByRole('dialog', { name: 'Attachments of Internal CA' }) }
}

function attachmentRow(dialog: HTMLElement, name: string): HTMLElement {
  return within(dialog).getByRole('listitem', { name })
}

function startRename(dialog: HTMLElement, name: string): { input: HTMLElement; save: HTMLElement } {
  fireEvent.click(within(attachmentRow(dialog, name)).getByRole('button', { name: 'Rename' }))
  return {
    input: within(dialog).getByLabelText(`New name for ${name}`),
    save: within(dialog).getByRole('button', { name: 'Save' }),
  }
}

test('lists the file names of the attachments in the item order', () => {
  const { dialog } = open()
  const items = within(within(dialog).getByRole('list', { name: 'Attachments' })).getAllByRole('listitem')
  expect(items.map((item) => item.getAttribute('aria-label'))).toEqual(['ca-notes.txt', 'Root.pem'])
})

test('Save queues one renameAttachment with the name the dialog showed', () => {
  const { dialog, onApply } = open()
  const { input, save } = startRename(dialog, 'ca-notes.txt')
  expect(input).toHaveValue('ca-notes.txt')
  expect(save).toBeDisabled()

  fireEvent.change(input, { target: { value: 'ca-notes-2026.txt' } })
  fireEvent.click(save)
  expect(onApply).toHaveBeenCalledTimes(1)
  expect(onApply).toHaveBeenCalledWith([
    {
      kind: 'renameAttachment',
      itemId: 'item-1',
      revisionDate: '2026-11-01T10:00:00Z',
      attachmentId: 'att-1',
      fileName: 'ca-notes-2026.txt',
      shownFileName: 'ca-notes.txt',
    },
  ])
})

test.each([
  ['', 'The name is empty'],
  ['   ', 'The name is empty'],
  ['notes/ca.txt', 'A file name may not hold a slash or a backslash'],
  ['notes\\ca.txt', 'A file name may not hold a slash or a backslash'],
  ['ca-notes.txt', 'The attachment has this name already'],
  ['root.PEM', 'Another attachment is named Root.pem'],
])('refuses %j with its message and queues nothing', (name, message) => {
  const { dialog, onApply } = open()
  const { input, save } = startRename(dialog, 'ca-notes.txt')
  // A first change, so the field counts as edited also when the name typed is the one it has now.
  fireEvent.change(input, { target: { value: 'x' } })
  fireEvent.change(input, { target: { value: name } })
  expect(within(dialog).getByText(message)).toBeInTheDocument()
  expect(save).toBeDisabled()
  fireEvent.submit(input.closest('form') as HTMLFormElement)
  expect(onApply).not.toHaveBeenCalled()
})

test('changing only the case of its own name is allowed', () => {
  const { dialog, onApply } = open()
  const { input, save } = startRename(dialog, 'Root.pem')
  fireEvent.change(input, { target: { value: 'root.pem' } })
  expect(save).toBeEnabled()
  fireEvent.click(save)
  expect(onApply).toHaveBeenCalledWith([expect.objectContaining({ attachmentId: 'att-2', fileName: 'root.pem', shownFileName: 'Root.pem' })])
})

test('Cancel leaves the rename without queueing anything', () => {
  const { dialog, onApply } = open()
  startRename(dialog, 'Root.pem')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  expect(within(dialog).queryByLabelText('New name for Root.pem')).not.toBeInTheDocument()
  expect(onApply).not.toHaveBeenCalled()
})

test('an attachment with a rename queued shows it and takes no other', () => {
  const entry: QueueEntry = {
    id: 7,
    createdAt: '2026-11-17T10:00:00Z',
    source: 'ui',
    summary: 'Rename attachment "ca-notes.txt" of "Internal CA" to "ca.txt"',
    status: 'pending',
    decidedAt: null,
    operation: {
      kind: 'renameAttachment',
      itemId: 'item-1',
      revisionDate: '2026-11-01T10:00:00Z',
      attachmentId: 'att-1',
      fileName: 'ca.txt',
      shownFileName: 'ca-notes.txt',
    },
    diff: [],
    diffError: null,
    result: null,
  }
  const { dialog } = open([entry])
  const queued = attachmentRow(dialog, 'ca-notes.txt')
  expect(within(queued).getByText('Rename to "ca.txt" is queued')).toBeInTheDocument()
  expect(within(queued).getByRole('button', { name: 'Rename' })).toBeDisabled()
  expect(within(attachmentRow(dialog, 'Root.pem')).getByRole('button', { name: 'Rename' })).toBeEnabled()
})

const grid = gridAnswer(['name', gridColumn('attachments', { multi: true, readOnly: true })], [row])

test('the cell menu opens the dialog and a save posts the rename to the queue', async () => {
  const { fetchMock } = renderGrid(grid, {
    routes: {
      'GET /api/queue?status=pending': () => jsonResponse(200, []),
      'POST /api/queue': () => jsonResponse(201, { ids: [1] }),
    },
  })
  await screen.findByText('Internal CA')
  const cell = document.querySelector<HTMLElement>('.ag-row[row-id="item-1"] .ag-cell[col-id="attachments"]')
  fireEvent.contextMenu(cell as HTMLElement, { clientX: 10, clientY: 10 })
  fireEvent.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Rename attachments' }))

  const dialog = await screen.findByRole('dialog', { name: 'Attachments of Internal CA' })
  const { input, save } = startRename(dialog, 'ca-notes.txt')
  fireEvent.change(input, { target: { value: 'ca-notes-2026.txt' } })
  fireEvent.click(save)
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Attachments of Internal CA' })).not.toBeInTheDocument())
  const posted = fetchMock.mock.calls
    .filter(([path, init]) => path === '/api/queue' && init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init?.body)))
  expect(posted).toEqual([
    {
      operations: [
        {
          kind: 'renameAttachment',
          itemId: 'item-1',
          revisionDate: '2026-11-01T10:00:00Z',
          attachmentId: 'att-1',
          fileName: 'ca-notes-2026.txt',
          shownFileName: 'ca-notes.txt',
        },
      ],
    },
  ])
})
