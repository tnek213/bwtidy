import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { QueueEntry } from '../api/queue.ts'
import { jsonResponse, renderWithQuery } from '../test-utils.tsx'
import FoldersDialog from './FoldersDialog.tsx'
import type { VaultFolder } from '../api/grid.ts'
import { gridAnswer, renderGrid } from './testSupport.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

const folders: VaultFolder[] = [
  { id: 'f-empty', name: 'Empty', items: 0, trashedItems: 0 },
  { id: 'f-old', name: 'Old', items: 0, trashedItems: 1 },
  { id: 'f-work', name: 'Work', items: 2, trashedItems: 0 },
]

function open(pending: QueueEntry[] = []) {
  const onApply = vi.fn()
  const onClose = vi.fn()
  renderWithQuery(<FoldersDialog folders={folders} pending={pending} onClose={onClose} onApply={onApply} />)
  return { onApply, onClose, dialog: screen.getByRole('dialog', { name: 'Folders' }) }
}

function folderRow(dialog: HTMLElement, name: string): HTMLElement {
  return within(dialog).getByRole('listitem', { name })
}

test('lists every folder with its item count', () => {
  const { dialog } = open()
  expect(within(folderRow(dialog, 'Empty')).getByText('0 items')).toBeInTheDocument()
  expect(within(folderRow(dialog, 'Old')).getByText('0 items, 1 in the trash')).toBeInTheDocument()
  expect(within(folderRow(dialog, 'Work')).getByText('2 items')).toBeInTheDocument()
})

test('Delete is only for an empty folder and says why it is off', async () => {
  const { dialog, onApply } = open()
  const work = within(folderRow(dialog, 'Work')).getByRole('button', { name: 'Delete' })
  expect(work).toBeDisabled()
  fireEvent.mouseOver(work.parentElement as HTMLElement)
  expect(await screen.findByRole('tooltip')).toHaveTextContent('Move its 2 items out first')
  expect(within(folderRow(dialog, 'Old')).getByRole('button', { name: 'Delete' })).toBeDisabled()

  fireEvent.click(within(folderRow(dialog, 'Empty')).getByRole('button', { name: 'Delete' }))
  expect(onApply).toHaveBeenCalledWith([{ kind: 'deleteFolder', folderId: 'f-empty', shownName: 'Empty' }])
})

test('Rename opens a name field, Save queues the rename', () => {
  const { dialog, onApply } = open()
  fireEvent.click(within(folderRow(dialog, 'Work')).getByRole('button', { name: 'Rename' }))
  const input = within(dialog).getByLabelText('New name for Work')
  expect(input).toHaveValue('Work')
  const save = within(dialog).getByRole('button', { name: 'Save' })
  expect(save).toBeDisabled()

  fireEvent.change(input, { target: { value: 'empty' } })
  expect(save).toBeDisabled()
  expect(within(dialog).getByText('Another folder is named Empty')).toBeInTheDocument()
  fireEvent.change(input, { target: { value: '  ' } })
  expect(save).toBeDisabled()

  fireEvent.change(input, { target: { value: 'Job' } })
  fireEvent.click(save)
  expect(onApply).toHaveBeenCalledWith([{ kind: 'renameFolder', folderId: 'f-work', shownName: 'Work', newName: 'Job' }])
})

test('Cancel leaves the rename without queueing anything', () => {
  const { dialog, onApply } = open()
  fireEvent.click(within(folderRow(dialog, 'Empty')).getByRole('button', { name: 'Rename' }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  expect(within(dialog).queryByLabelText('New name for Empty')).not.toBeInTheDocument()
  expect(within(folderRow(dialog, 'Empty')).getByRole('button', { name: 'Rename' })).toBeEnabled()
  expect(onApply).not.toHaveBeenCalled()
})

test('a folder with a queued change shows it and takes no other', () => {
  const entry: QueueEntry = {
    id: 7,
    createdAt: '2026-11-12T10:00:00Z',
    source: 'ui',
    summary: 'Rename the folder "Empty" to "Spare"',
    status: 'pending',
    decidedAt: null,
    operation: { kind: 'renameFolder', folderId: 'f-empty', shownName: 'Empty', newName: 'Spare' },
    diff: [],
    diffError: null,
    result: null,
  }
  const { dialog } = open([entry])
  const row = folderRow(dialog, 'Empty')
  expect(within(row).getByText('Rename to "Spare" is queued')).toBeInTheDocument()
  expect(within(row).getByRole('button', { name: 'Rename' })).toBeDisabled()
  expect(within(row).getByRole('button', { name: 'Delete' })).toBeDisabled()
})

const grid = gridAnswer(
  ['name', 'folder', 'fields.User'],
  [
    {
      id: 'item-1',
      cells: { name: 'Code forge', folder: 'Work', 'fields.User': 'user' },
      folderId: 'f-work',
    },
  ],
  { folders },
)

test('the folder column header menu opens Manage folders and queues its action', async () => {
  const { fetchMock } = renderGrid(grid, {
    routes: {
      'GET /api/near-duplicates': () => jsonResponse(200, []),
      'POST /api/queue': () => jsonResponse(201, { ids: [1] }),
    },
  })
  await screen.findByText('Code forge')
  fireEvent.click(await screen.findByRole('button', { name: 'Actions for folder' }))
  const menu = await screen.findByRole('menu')
  expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Manage folders…'])
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Manage folders…' }))

  const dialog = await screen.findByRole('dialog', { name: 'Folders' })
  fireEvent.click(within(within(dialog).getByRole('listitem', { name: 'Empty' })).getByRole('button', { name: 'Delete' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Folders' })).not.toBeInTheDocument())
  const posted = fetchMock.mock.calls
    .filter(([path, init]) => path === '/api/queue' && init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init?.body)))
  expect(posted).toEqual([{ operations: [{ kind: 'deleteFolder', folderId: 'f-empty', shownName: 'Empty' }] }])
})

test('a field column keeps its own menu', async () => {
  renderGrid(grid, { routes: { 'GET /api/near-duplicates': () => jsonResponse(200, []) } })
  await screen.findByText('Code forge')
  fireEvent.click(await screen.findByRole('button', { name: 'Actions for fields.User' }))
  const menu = await screen.findByRole('menu')
  expect(within(menu).queryByRole('menuitem', { name: 'Manage folders…' })).not.toBeInTheDocument()
  expect(within(menu).getByRole('menuitem', { name: 'Rename field…' })).toBeInTheDocument()
})

test('a folder that changed elsewhere says so', async () => {
  renderGrid(grid, {
    routes: {
      'GET /api/near-duplicates': () => jsonResponse(200, []),
      'POST /api/queue': () =>
        jsonResponse(409, {
          detail: '1 folder changed since the grid was loaded, nothing was written',
          conflicts: [{ folderId: 'f-empty', expected: 'Empty', actual: 'Spare' }],
        }),
    },
  })
  await screen.findByText('Code forge')
  fireEvent.click(await screen.findByRole('button', { name: 'Actions for folder' }))
  fireEvent.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Manage folders…' }))
  const dialog = await screen.findByRole('dialog', { name: 'Folders' })
  fireEvent.click(within(within(dialog).getByRole('listitem', { name: 'Empty' })).getByRole('button', { name: 'Delete' }))
  expect(await screen.findByText(/the folder changed elsewhere since the grid loaded/)).toBeInTheDocument()
})
