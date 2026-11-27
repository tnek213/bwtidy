import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { GridData } from '../api/grid.ts'
import { jsonResponse, mockFetch, renderWithQuery, waitForPolls } from '../test-utils.tsx'
import type { Datatype } from '../api/datatypes.ts'
import type { Template, TemplateChange } from '../api/templates.ts'
import { TemplatesTab } from './TemplatesTab.tsx'
import { TYPE_GUIDANCE } from './typeGuidance.ts'
import { ITEM_TYPES } from '../shared/templateRules.ts'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const computer: Template = {
  name: 'Computer',
  type: 2,
  coordinates: [
    { kind: 'field', name: 'user', fieldType: 0 },
    { kind: 'field', name: 'user password', fieldType: 1 },
  ],
}

const grid: GridData = {
  columns: [
    { id: 'name', label: 'name', group: 'item', secret: false, multi: false, readOnly: false },
    { id: 'type', label: 'type', group: 'item', secret: false, multi: false, readOnly: false },
  ],
  toolNoteId: 'tool-note',
  rows: [
    { id: 'tool-note', revisionDate: '2026-10-20T08:00:00Z', cells: { name: 'bwtidy', type: 'note' } },
    { id: 'laptop', revisionDate: '2026-10-21T08:00:00Z', cells: { name: 'Laptop', type: 'note' } },
  ],
  fields: [
    { name: 'BIOS Password', fieldType: 1 },
    { name: 'Enabled', fieldType: 2 },
    { name: 'user', fieldType: 0 },
    { name: 'user password', fieldType: 1 },
  ],
}

const pin: Datatype = {
  id: '1f0c9a3e',
  name: 'PIN',
  pattern: '\\d{4,8}',
  description: '',
  ignoreCase: false,
  wholeValue: true,
  examples: ['1234'],
  counterExamples: [],
  states: 10,
  maxRepeat: 8,
  shortest: 4,
  broken: null,
}

const broken: Datatype = {
  ...pin,
  id: '2a7c1d04',
  name: 'Old',
  pattern: '(',
  states: null,
  maxRepeat: null,
  shortest: null,
  broken: 'unbalanced parenthesis (column 1)',
}

const queued = () => jsonResponse(202, { entryId: 7, templates: [] })

function changesOf(init: RequestInit): TemplateChange[] {
  return (JSON.parse(String(init.body)) as { changes: TemplateChange[] }).changes
}

// The template a PUT body adds or sets.
function sentTemplate(body: unknown): Template {
  const change = (body as { changes: TemplateChange[] }).changes.find(
    (candidate) => candidate.op === 'addTemplate' || candidate.op === 'setTemplate',
  )
  if (change === undefined || !('template' in change)) {
    throw new Error('the PUT neither adds nor sets a template')
  }
  return change.template
}

// The list as the backend leaves it after the PUT's changes, enough for these tests.
function withChanges(templates: Template[], init: RequestInit): Template[] {
  let next = [...templates]
  for (const change of changesOf(init)) {
    if (change.op === 'addTemplate') {
      next = [...next, change.template]
    } else if (change.op === 'setTemplate') {
      next = next.map((template) => (template.name === change.template.name ? change.template : template))
    } else if (change.op === 'removeTemplate') {
      next = next.filter((template) => template.name !== change.name)
    }
  }
  return next
}

function renderPage(
  put: (init: RequestInit) => Response = queued,
  templates: () => unknown = () => ({ templates: [computer], saving: false }),
) {
  const fetchMock = mockFetch({
    'GET /api/templates': () => jsonResponse(200, templates()),
    'GET /api/datatypes': () => jsonResponse(200, { datatypes: [pin, broken], saving: false }),
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/queue/auto-commit': () =>
      jsonResponse(200, { afterSeconds: 10, maxEntries: 20, pending: 0, committing: false, nextCommitAt: null }),
    'GET /api/grid': () => jsonResponse(200, grid),
    'GET /api/template-matches': () =>
      jsonResponse(200, {
        laptop: { match: 'Computer', scores: [{ template: 'Computer', score: 1, typeMatches: true, filled: 3, total: 3, missing: [] }] },
      }),
    'PUT /api/templates': put,
  })
  renderWithQuery(<TemplatesTab />)
  return fetchMock
}

function puts(fetchMock: ReturnType<typeof mockFetch>): unknown[] {
  return fetchMock.mock.calls
    .filter(([path, init]) => path === '/api/templates' && init?.method === 'PUT')
    .map(([, init]) => JSON.parse(String(init?.body)))
}

async function choose(scope: HTMLElement, label: string, option: string) {
  fireEvent.mouseDown(within(scope).getByRole('combobox', { name: label }))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}

function row(scope: HTMLElement, number: number): HTMLElement {
  return within(scope).getByRole('group', { name: `Coordinate row ${number}` })
}

// A row added by Add coordinate has its options open already (focused, openOnFocus).
async function openCoordinate(scope: HTMLElement, number: number): Promise<HTMLElement> {
  const input = within(scope).getByRole('combobox', { name: `Coordinate ${number}` })
  if (input.getAttribute('aria-expanded') !== 'true') {
    fireEvent.mouseDown(input)
  }
  return screen.findByRole('listbox')
}

// Adds a coordinate row and picks an option of it by its accessible name (label, type).
async function addCoordinate(scope: HTMLElement, option: string) {
  fireEvent.click(within(scope).getByRole('button', { name: 'Add coordinate' }))
  const number = within(scope).getAllByRole('group').length
  const listbox = await openCoordinate(scope, number)
  fireEvent.click(within(listbox).getByRole('option', { name: option }))
}

async function openNewTemplate(): Promise<HTMLElement> {
  await screen.findByRole('cell', { name: '1' })
  fireEvent.click(screen.getByRole('button', { name: 'New template' }))
  return screen.findByRole('dialog', { name: 'New template' })
}

test('create and save puts an addTemplate as the only key of the body, closes at once and shows Saving', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  let stored: unknown = { templates: [computer], saving: false }
  let answer: (response: Response) => void = () => {}
  const fetchMock = renderPage(
    (init) => {
      stored = { templates: withChanges([computer], init), saving: true }
      return new Promise<Response>((resolve) => {
        answer = resolve
      }) as unknown as Response
    },
    () => stored,
  )
  const dialog = await openNewTemplate()

  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Web login' } })
  await choose(dialog, 'Type', 'Login')
  await addCoordinate(dialog, 'login.username, Built-in')
  await addCoordinate(dialog, 'login.totp, Built-in, hidden')
  await addCoordinate(dialog, 'New field…')
  fireEvent.change(within(dialog).getByRole('textbox', { name: 'New field name 3' }), { target: { value: 'PIN' } })
  await choose(row(dialog, 3), 'Field type', 'Hidden')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(puts(fetchMock)[0]).toEqual({
    changes: [
      {
        op: 'addTemplate',
        template: {
          name: 'Web login',
          type: 1,
          coordinates: [
            { kind: 'path', path: 'login.username' },
            { kind: 'path', path: 'login.totp' },
            { kind: 'field', name: 'PIN', fieldType: 1 },
          ],
        },
      },
    ],
  })
  // Closed while the PUT still runs.
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  answer(jsonResponse(202, { entryId: 7, templates: [] }))

  expect(await screen.findByText('Saving')).toBeInTheDocument()
  expect(await screen.findByRole('cell', { name: 'Web login' })).toBeInTheDocument()

  stored = { templates: (stored as { templates: Template[] }).templates, saving: false }
  await waitForPolls(() => expect(screen.queryByText('Saving')).not.toBeInTheDocument())
  expect(screen.getByRole('cell', { name: 'Web login' })).toBeInTheDocument()
})

test('the browser extension hint is visible while editing', async () => {
  renderPage()
  const dialog = await openNewTemplate()

  const hint = within(dialog).getByText(/browser extension copies/)
  expect(hint).toHaveTextContent('login.username')
  expect(hint).toHaveTextContent('login.password')
  expect(hint).toHaveTextContent('login.totp')
  expect(hint).toHaveTextContent(/autofill matches them by name/)
})

test('an editor without coordinates explains what a coordinate is, until one is added', async () => {
  renderPage()
  const dialog = await openNewTemplate()

  const hint = within(dialog).getByText(/^No coordinates yet\. A coordinate is a place in an item/)
  expect(hint).toHaveTextContent('A field name pattern such as user_* covers several custom fields at once.')

  await addCoordinate(dialog, 'fields.user, Text')
  expect(within(dialog).queryByText(/No coordinates yet/)).not.toBeInTheDocument()
})

test('the table shows the description of a template after its name, empty without one', async () => {
  const described: Template = { ...computer, description: 'A laptop or desktop' }
  const bare: Template = { ...computer, name: 'Bare' }
  renderPage(queued, () => ({ templates: [described, bare], saving: false }))

  const table = await screen.findByRole('table', { name: 'Templates' })
  const headers = within(table).getAllByRole('columnheader').map((header) => header.textContent)
  expect(headers.slice(0, 3)).toEqual(['Name', 'Description', 'Type'])
  // The column of Edit and Delete is named for a screen reader.
  expect(within(table).getByRole('columnheader', { name: 'Actions' })).toBeInTheDocument()
  const [, first, second] = within(table).getAllByRole('row')
  expect(within(first).getAllByRole('cell')[1]).toHaveTextContent('A laptop or desktop')
  expect(within(second).getAllByRole('cell')[1]).toBeEmptyDOMElement()
})

test('the editor saves the description with the template, an emptied one is left out', async () => {
  const described: Template = { ...computer, description: 'A laptop or desktop' }
  const fetchMock = renderPage(queued, () => ({ templates: [described], saving: false }))
  await screen.findByRole('cell', { name: 'A laptop or desktop' })

  fireEvent.click(screen.getByRole('button', { name: 'Edit Computer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit Computer' })
  const field = within(dialog).getByRole('textbox', { name: 'Description' })
  expect(field).toHaveValue('A laptop or desktop')
  expect(within(dialog).getByText('Shown in the list and to an agent connected through MCP.')).toBeInTheDocument()
  fireEvent.change(field, { target: { value: ' Any computer ' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(puts(fetchMock)[0]).toEqual({
    changes: [{ op: 'setTemplate', template: { ...computer, description: 'Any computer' } }],
  })

  fireEvent.click(await screen.findByRole('button', { name: 'Edit Computer' }))
  const again = await screen.findByRole('dialog', { name: 'Edit Computer' })
  fireEvent.change(within(again).getByRole('textbox', { name: 'Description' }), { target: { value: '  ' } })
  fireEvent.click(within(again).getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(puts(fetchMock)).toHaveLength(2))
  expect(puts(fetchMock)[1]).toEqual({ changes: [{ op: 'setTemplate', template: computer }] })
})

test('a description of more than 500 characters is refused before the request', async () => {
  const fetchMock = renderPage()
  await screen.findByRole('cell', { name: '1' })

  fireEvent.click(screen.getByRole('button', { name: 'Edit Computer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit Computer' })
  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Description' }), { target: { value: 'x'.repeat(501) } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  expect(await within(dialog).findByText('A description holds at most 500 characters.')).toBeInTheDocument()
  expect(puts(fetchMock)).toHaveLength(0)
})

test('the guidance of the selected type shows under the type select and follows the type', async () => {
  renderPage()
  const dialog = await openNewTemplate()

  const guidance = () => within(dialog).getByRole('region', { name: 'Type guidance' })
  expect(guidance()).toHaveTextContent(TYPE_GUIDANCE[2].useFor[0])
  expect(guidance()).toHaveTextContent(TYPE_GUIDANCE[2].notFor[0])
  expect(guidance()).toHaveTextContent('Use Secure note for')
  expect(guidance()).not.toHaveTextContent(/Use for/)

  await choose(dialog, 'Type', 'Login')
  expect(guidance()).toHaveTextContent(TYPE_GUIDANCE[1].useFor[0])
  expect(guidance()).toHaveTextContent(TYPE_GUIDANCE[1].notFor[0])
  expect(guidance()).not.toHaveTextContent(TYPE_GUIDANCE[2].useFor[0])
  expect(guidance()).toHaveTextContent('Use Login for')
  expect(guidance()).not.toHaveTextContent('Use Secure note for')

  await choose(dialog, 'Type', 'Card')
  expect(guidance()).toHaveTextContent(TYPE_GUIDANCE[3].useFor[0])
  expect(guidance()).toHaveTextContent('Use Card for')
  expect(guidance()).not.toHaveTextContent(/Use for/)
  expect(guidance()).not.toHaveTextContent(TYPE_GUIDANCE[1].useFor[0])
})

test('every type in the select has guidance with use and not-for examples', () => {
  for (const type of Object.keys(ITEM_TYPES).map(Number)) {
    const guidance = TYPE_GUIDANCE[type]
    expect(guidance, `type ${type}`).toBeDefined()
    expect(guidance.useFor.length).toBeGreaterThanOrEqual(2)
    expect(guidance.useFor.length).toBeLessThanOrEqual(3)
    expect(guidance.notFor.length).toBeGreaterThanOrEqual(1)
    expect(guidance.notFor.length).toBeLessThanOrEqual(2)
  }
})

test('a 422 from the backend opens the editor again with the draft and the error', async () => {
  renderPage(() => jsonResponse(422, { detail: 'Template Computer: the type note has no login.username' }))
  await screen.findByRole('cell', { name: '1' })

  fireEvent.click(screen.getByRole('button', { name: 'Edit Computer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit Computer' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove coordinate 1' }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  const again = await screen.findByText(/the type note has no login\.username/)
  const reopened = again.closest<HTMLElement>('[role="dialog"]')!
  expect(within(reopened).getByText(/Nothing was saved/)).toBeInTheDocument()
  expect(within(reopened).getAllByRole('group')).toHaveLength(1)
  expect(within(reopened).getByRole('combobox', { name: 'Coordinate 1' })).toHaveValue('fields.user password')
})

test('an edit that renames a template puts the rename first, then the template under its new name', async () => {
  const fetchMock = renderPage()
  await screen.findByRole('cell', { name: '1' })

  fireEvent.click(screen.getByRole('button', { name: 'Edit Computer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit Computer' })
  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: ' Laptop ' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(puts(fetchMock)[0]).toEqual({
    changes: [
      { op: 'renameTemplate', name: 'Computer', to: 'Laptop' },
      { op: 'setTemplate', template: { ...computer, name: 'Laptop' } },
    ],
  })
})

test('an edit that keeps the name puts the template alone, a delete its removal by name', async () => {
  const fetchMock = renderPage()
  await screen.findByRole('cell', { name: '1' })

  fireEvent.click(screen.getByRole('button', { name: 'Edit Computer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit Computer' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove coordinate 1' }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  const [edit] = (puts(fetchMock)[0] as { changes: TemplateChange[] }).changes
  expect(edit).toEqual({ op: 'setTemplate', template: { ...computer, coordinates: computer.coordinates.slice(1) } })

  fireEvent.click(await screen.findByRole('button', { name: 'Delete Computer' }))
  const confirm = await screen.findByRole('dialog', { name: 'Delete template' })
  fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
  await waitFor(() => expect(puts(fetchMock)).toHaveLength(2))
  expect(puts(fetchMock)[1]).toEqual({ changes: [{ op: 'removeTemplate', name: 'Computer' }] })
})

test('a failed save says it could not save and reloads the templates', async () => {
  const fetchMock = renderPage(() => jsonResponse(500, { detail: 'broken' }))
  await screen.findByRole('cell', { name: '1' })

  fireEvent.click(screen.getByRole('button', { name: 'Delete Computer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete template' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))

  expect(await screen.findByText(/Could not save/)).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  await waitFor(() =>
    expect(fetchMock.mock.calls.filter(([path, init]) => path === '/api/templates' && init?.method === 'GET')).toHaveLength(2),
  )
})

test('an invalid template is refused before any request', async () => {
  const fetchMock = renderPage()
  const dialog = await openNewTemplate()

  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Computer' } })
  await addCoordinate(dialog, 'fields.user, Text')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  expect(await within(dialog).findByText('There is already a template named Computer.')).toBeInTheDocument()
  expect(puts(fetchMock)).toHaveLength(0)
})

test('the coordinate options are the built-in slots of the type and the vault fields, New field and Field name pattern last', async () => {
  renderPage()
  const dialog = await openNewTemplate()

  fireEvent.click(within(dialog).getByRole('button', { name: 'Add coordinate' }))
  const names = (listbox: HTMLElement) =>
    within(listbox)
      .getAllByRole('option')
      .map((option) => option.getAttribute('aria-label'))
  expect(names(await openCoordinate(dialog, 1))).toEqual([
    'fields.BIOS Password, Hidden',
    'fields.Enabled, Boolean',
    'fields.user, Text',
    'fields.user password, Hidden',
    'New field…',
    'Field name pattern…',
  ])
  fireEvent.keyDown(within(dialog).getByRole('combobox', { name: 'Coordinate 1' }), { key: 'Escape' })

  await choose(dialog, 'Type', 'SSH key')
  expect(names(await openCoordinate(dialog, 1)).slice(0, 4)).toEqual([
    'sshKey.privateKey, Built-in, hidden',
    'sshKey.publicKey, Built-in',
    'sshKey.keyFingerprint, Built-in',
    'fields.BIOS Password, Hidden',
  ])
})

test('typing filters the options and New field and Field name pattern stay', async () => {
  renderPage()
  const dialog = await openNewTemplate()

  fireEvent.click(within(dialog).getByRole('button', { name: 'Add coordinate' }))
  const input = within(dialog).getByRole('combobox', { name: 'Coordinate 1' })
  fireEvent.change(input, { target: { value: 'password' } })
  const listbox = await screen.findByRole('listbox')
  expect(within(listbox).getAllByRole('option').map((option) => option.getAttribute('aria-label'))).toEqual([
    'fields.BIOS Password, Hidden',
    'fields.user password, Hidden',
    'New field…',
    'Field name pattern…',
  ])
})

test('a field another row uses is not offered again', async () => {
  renderPage()
  const dialog = await openNewTemplate()

  await addCoordinate(dialog, 'fields.user, Text')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add coordinate' }))
  const listbox = await openCoordinate(dialog, 2)
  expect(within(listbox).queryByRole('option', { name: 'fields.user, Text' })).not.toBeInTheDocument()
})

test('picking a vault field sets its field type', async () => {
  const fetchMock = renderPage()
  const dialog = await openNewTemplate()

  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Laptop' } })
  await addCoordinate(dialog, 'fields.BIOS Password, Hidden')
  await addCoordinate(dialog, 'fields.Enabled, Boolean')
  expect(within(row(dialog, 1)).getByRole('combobox', { name: 'Field type' })).toHaveTextContent('Hidden')
  expect(within(row(dialog, 2)).getByRole('combobox', { name: 'Field type' })).toHaveTextContent('Boolean')
  expect(within(dialog).queryByTitle('No item has this field')).not.toBeInTheDocument()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(sentTemplate(puts(fetchMock)[0]).coordinates).toEqual([
    { kind: 'field', name: 'BIOS Password', fieldType: 1 },
    { kind: 'field', name: 'Enabled', fieldType: 2 },
  ])
})

test('New field gives a free text input that starts from what was typed, with the warning', async () => {
  renderPage()
  const dialog = await openNewTemplate()

  fireEvent.click(within(dialog).getByRole('button', { name: 'Add coordinate' }))
  const input = within(dialog).getByRole('combobox', { name: 'Coordinate 1' })
  fireEvent.change(input, { target: { value: 'TPM PIN' } })
  fireEvent.click(await screen.findByRole('option', { name: 'New field…' }))

  const name = within(dialog).getByRole('textbox', { name: 'New field name 1' })
  expect(name).toHaveValue('TPM PIN')
  expect(within(dialog).getByText('Not in the vault yet')).toBeInTheDocument()
  expect(row(dialog, 1)).toHaveAttribute('title', 'No item has this field')
})

test('Field name pattern makes a fieldPattern row with its chip and hint, saved with its datatype and flag', async () => {
  let stored: unknown = { templates: [computer], saving: false }
  const fetchMock = renderPage(
    (init) => {
      stored = { templates: withChanges([computer], init), saving: false }
      return queued()
    },
    () => stored,
  )
  const dialog = await openNewTemplate()

  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Shared' } })
  await addCoordinate(dialog, 'fields.user, Text')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add coordinate' }))
  fireEvent.change(within(dialog).getByRole('combobox', { name: 'Coordinate 2' }), { target: { value: 'pin_' } })
  fireEvent.click(await screen.findByRole('option', { name: 'Field name pattern…' }))

  const pattern = within(row(dialog, 2)).getByRole('textbox', { name: 'Field name pattern 2' })
  expect(pattern).toHaveValue('pin_')
  expect(within(row(dialog, 2)).getByText('pattern')).toBeInTheDocument()
  expect(within(row(dialog, 2)).getByText('* matches any characters, for example user_*')).toBeInTheDocument()
  // without a * it is refused before any request
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
  expect(await within(dialog).findByText('A pattern needs a * and some other character.')).toBeInTheDocument()
  expect(puts(fetchMock)).toHaveLength(0)

  fireEvent.change(pattern, { target: { value: ' pin_* ' } })
  await choose(row(dialog, 2), 'Field type', 'Hidden')
  await choose(row(dialog, 2), 'Datatype', 'PIN')
  fireEvent.click(within(row(dialog, 2)).getByRole('checkbox', { name: 'Optional' }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(sentTemplate(puts(fetchMock)[0]).coordinates).toEqual([
    { kind: 'field', name: 'user', fieldType: 0 },
    { kind: 'fieldPattern', pattern: 'pin_*', fieldType: 1, datatype: pin.id, optional: true },
  ])
  expect(await screen.findByText('fields.pin_*: PIN (pattern, optional)')).toBeInTheDocument()
})

test('a * typed into a New field name stays a literal field name', async () => {
  const fetchMock = renderPage()
  const dialog = await openNewTemplate()

  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Starry' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add coordinate' }))
  fireEvent.change(within(dialog).getByRole('combobox', { name: 'Coordinate 1' }), { target: { value: 'user_*' } })
  fireEvent.click(await screen.findByRole('option', { name: 'New field…' }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(sentTemplate(puts(fetchMock)[0]).coordinates).toEqual([{ kind: 'field', name: 'user_*', fieldType: 0 }])
})

test('a typed name near equal to a vault field is offered as that field', async () => {
  const fetchMock = renderPage()
  const dialog = await openNewTemplate()

  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Laptop' } })
  await addCoordinate(dialog, 'New field…')
  fireEvent.change(within(dialog).getByRole('textbox', { name: 'New field name 1' }), {
    target: { value: 'bios  password ' },
  })
  expect(within(dialog).getByText('The vault has fields.BIOS Password (Hidden).')).toBeInTheDocument()
  expect(within(dialog).queryByText('Not in the vault yet')).not.toBeInTheDocument()

  fireEvent.click(within(dialog).getByRole('button', { name: 'Use fields.BIOS Password' }))
  expect(within(dialog).getByRole('combobox', { name: 'Coordinate 1' })).toHaveValue('fields.BIOS Password')
  expect(within(row(dialog, 1)).getByRole('combobox', { name: 'Field type' })).toHaveTextContent('Hidden')
  expect(row(dialog, 1)).not.toHaveAttribute('title')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(sentTemplate(puts(fetchMock)[0]).coordinates).toEqual([
    { kind: 'field', name: 'BIOS Password', fieldType: 1 },
  ])
})

test('a field no item has gets the warning background, in the list and in the editor', async () => {
  const stale: Template = {
    name: 'Router',
    type: 2,
    coordinates: [
      { kind: 'field', name: 'user', fieldType: 0 },
      { kind: 'field', name: 'WPA key', fieldType: 1 },
    ],
  }
  renderPage(queued, () => ({ templates: [stale], saving: false }))
  await screen.findByRole('cell', { name: 'Router' })
  // The warning needs the vault's fields from the grid.
  await waitFor(() => expect(screen.getByText('fields.WPA key').closest('[title]')).toHaveAttribute('title', 'No item has this field'))
  expect(screen.getByText('fields.user').closest('[title]')).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Edit Router' }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit Router' })
  expect(row(dialog, 1)).not.toHaveAttribute('title')
  expect(row(dialog, 2)).toHaveAttribute('title', 'No item has this field')
  expect(within(dialog).getByRole('combobox', { name: 'Coordinate 2' })).toHaveValue('fields.WPA key')
})

test('a text or hidden coordinate gets a Datatype select with None first, a boolean field gets none', async () => {
  renderPage()
  const dialog = await openNewTemplate()

  await addCoordinate(dialog, 'fields.user, Text')
  await addCoordinate(dialog, 'fields.BIOS Password, Hidden')
  await addCoordinate(dialog, 'fields.Enabled, Boolean')
  const select = () => within(row(dialog, 1)).getByRole('combobox', { name: 'Datatype' })
  expect(select()).toHaveTextContent('None')
  expect(within(row(dialog, 2)).getByRole('combobox', { name: 'Datatype' })).toHaveTextContent('None')
  expect(within(row(dialog, 3)).queryByRole('combobox', { name: 'Datatype' })).not.toBeInTheDocument()

  fireEvent.mouseDown(select())
  const options = within(await screen.findByRole('listbox')).getAllByRole('option')
  expect(options.map((option) => option.textContent)).toEqual(['None', 'PIN', 'Old (broken)'])
  expect(options[2]).toHaveAttribute('aria-disabled', 'true')
})

test('a built-in slot gets the select too, and a datatype saves on its coordinate and shows on the chip', async () => {
  let stored: unknown = { templates: [computer], saving: false }
  const fetchMock = renderPage(
    (init) => {
      stored = { templates: withChanges([computer], init), saving: false }
      return queued()
    },
    () => stored,
  )
  const dialog = await openNewTemplate()

  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Web' } })
  await choose(dialog, 'Type', 'Login')
  await addCoordinate(dialog, 'login.username, Built-in')
  await addCoordinate(dialog, 'fields.BIOS Password, Hidden')
  await choose(row(dialog, 2), 'Datatype', 'PIN')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(sentTemplate(puts(fetchMock)[0]).coordinates).toEqual([
    { kind: 'path', path: 'login.username' },
    { kind: 'field', name: 'BIOS Password', fieldType: 1, datatype: pin.id },
  ])
  expect(await screen.findByText('fields.BIOS Password: PIN')).toBeInTheDocument()
})

test('a template that leaves the select alone, or sets it back to None, has no datatype key', async () => {
  const fetchMock = renderPage()
  const dialog = await openNewTemplate()

  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Laptop' } })
  await addCoordinate(dialog, 'fields.user, Text')
  await addCoordinate(dialog, 'fields.BIOS Password, Hidden')
  await choose(row(dialog, 2), 'Datatype', 'PIN')
  await choose(row(dialog, 2), 'Datatype', 'None')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  const coordinates = sentTemplate(puts(fetchMock)[0]).coordinates
  expect(coordinates.map((coordinate) => Object.keys(coordinate).sort())).toEqual([
    ['fieldType', 'kind', 'name'],
    ['fieldType', 'kind', 'name'],
  ])
})

test('picking another field keeps the datatype, picking a boolean field drops it', async () => {
  renderPage()
  const dialog = await openNewTemplate()

  await addCoordinate(dialog, 'fields.user, Text')
  await choose(row(dialog, 1), 'Datatype', 'PIN')
  const listbox = await openCoordinate(dialog, 1)
  fireEvent.click(within(listbox).getByRole('option', { name: 'fields.BIOS Password, Hidden' }))
  expect(within(row(dialog, 1)).getByRole('combobox', { name: 'Datatype' })).toHaveTextContent('PIN')

  fireEvent.click(within(await openCoordinate(dialog, 1)).getByRole('option', { name: 'fields.Enabled, Boolean' }))
  expect(within(row(dialog, 1)).queryByRole('combobox', { name: 'Datatype' })).not.toBeInTheDocument()

  fireEvent.click(within(await openCoordinate(dialog, 1)).getByRole('option', { name: 'fields.user, Text' }))
  expect(within(row(dialog, 1)).getByRole('combobox', { name: 'Datatype' })).toHaveTextContent('None')
})

test('the Optional checkbox saves optional true on its coordinate only and shows on the chip', async () => {
  let stored: unknown = { templates: [computer], saving: false }
  const fetchMock = renderPage(
    (init) => {
      stored = { templates: withChanges([computer], init), saving: false }
      return queued()
    },
    () => stored,
  )
  const dialog = await openNewTemplate()

  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Laptop' } })
  await addCoordinate(dialog, 'fields.user, Text')
  await addCoordinate(dialog, 'fields.BIOS Password, Hidden')
  const checkbox = within(row(dialog, 2)).getByRole('checkbox', { name: 'Optional' })
  fireEvent.mouseOver(checkbox)
  expect(await screen.findByText('Not needed for a match. A value still has to fit its datatype.')).toBeInTheDocument()
  fireEvent.click(checkbox)
  // another pick in the row keeps the flag
  fireEvent.click(within(await openCoordinate(dialog, 2)).getByRole('option', { name: 'fields.user password, Hidden' }))
  expect(within(row(dialog, 2)).getByRole('checkbox', { name: 'Optional' })).toBeChecked()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(sentTemplate(puts(fetchMock)[0]).coordinates).toEqual([
    { kind: 'field', name: 'user', fieldType: 0 },
    { kind: 'field', name: 'user password', fieldType: 1, optional: true },
  ])
  expect(await screen.findByText('fields.user password (optional)')).toBeInTheDocument()
})

test('a template whose every coordinate is optional is refused before any request', async () => {
  const fetchMock = renderPage()
  const dialog = await openNewTemplate()

  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Laptop' } })
  await addCoordinate(dialog, 'fields.user, Text')
  fireEvent.click(within(row(dialog, 1)).getByRole('checkbox', { name: 'Optional' }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  expect(await within(dialog).findByText('A template needs at least one required field.')).toBeInTheDocument()
  expect(puts(fetchMock)).toHaveLength(0)
  // one required coordinate is enough
  fireEvent.click(within(row(dialog, 1)).getByRole('checkbox', { name: 'Optional' }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
})

test('an id that is no datatype stays through an edit, and the warning of the API shows', async () => {
  const withUnknown: Template = {
    name: 'Router',
    type: 2,
    coordinates: [{ kind: 'field', name: 'user', fieldType: 0, datatype: 'deadbeef' }],
  }
  const warning = "Router: fields.user names the unknown datatype 'deadbeef', it counts as no datatype"
  const fetchMock = renderPage(queued, () => ({ templates: [withUnknown], saving: false, warnings: [warning] }))

  expect(await screen.findByText(warning)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Edit Router' }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit Router' })
  expect(within(row(dialog, 1)).getByRole('combobox', { name: 'Datatype' })).toHaveTextContent('Unknown datatype')
  await waitFor(() => expect(screen.getByText('fields.user')).toBeInTheDocument())
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(puts(fetchMock)[0]).toMatchObject({ changes: [{ op: 'setTemplate', template: withUnknown }] })
})

test('the warning of the API about a duplicate template shows on the list', async () => {
  const warning = 'Copy: same type and fields as Computer, which is listed first and takes every match.'
  renderPage(queued, () => ({ templates: [computer, { ...computer, name: 'Copy' }], saving: false, warnings: [warning] }))

  expect(await screen.findByText(warning)).toBeInTheDocument()
})

test('the editor warns under the name while the draft is a duplicate of a template above it', async () => {
  const fetchMock = renderPage()
  const dialog = await openNewTemplate()
  const warning = 'Laptop: same type and fields as Computer, which is listed first and takes every match.'

  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Laptop' } })
  // the fields of Computer in another order
  await addCoordinate(dialog, 'fields.user password, Hidden')
  expect(within(dialog).queryByText(warning)).not.toBeInTheDocument()
  await addCoordinate(dialog, 'fields.user, Text')
  expect(within(dialog).getByText(warning)).toBeInTheDocument()

  // an optional flag scores differently
  fireEvent.click(within(row(dialog, 2)).getByRole('checkbox', { name: 'Optional' }))
  expect(within(dialog).queryByText(warning)).not.toBeInTheDocument()
  fireEvent.click(within(row(dialog, 2)).getByRole('checkbox', { name: 'Optional' }))
  expect(within(dialog).getByText(warning)).toBeInTheDocument()

  // saving stays allowed
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
})
