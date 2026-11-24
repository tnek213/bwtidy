import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { jsonResponse, mockFetch, renderWithQuery, textSecurity } from '../test-utils.tsx'
import type { Template } from '../api/templates.ts'
import type { GridRow } from '../api/grid.ts'
import NewItemDialog, { GENERATED_PASSWORD, newItemFields, SECRETS_LEFT_EMPTY } from './NewItemDialog.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

const rows: GridRow[] = [
  { id: 'item-1', revisionDate: '2026-10-01T10:00:00Z', cells: { name: 'Code forge', type: 'login', folder: 'Work' } },
  {
    id: 'item-2',
    revisionDate: '2026-10-02T10:00:00Z',
    cells: { name: 'Router', type: 'login', organization: 'Family', collections: ['Shared', 'Home'] },
  },
]

const templates: Template[] = [
  {
    name: 'Computer',
    description: 'A laptop or desktop with its passwords',
    type: 2,
    coordinates: [
      { kind: 'field', name: 'user', fieldType: 0 },
      { kind: 'field', name: 'user password', fieldType: 1 },
      { kind: 'path', path: 'notes' },
      { kind: 'field', name: 'encrypted', fieldType: 2 },
    ],
  },
  {
    name: 'Website',
    type: 1,
    coordinates: [
      { kind: 'path', path: 'login.username' },
      { kind: 'path', path: 'login.password' },
      { kind: 'path', path: 'login.uris' },
    ],
  },
]

function open(post: (init: RequestInit) => Response = () => jsonResponse(201, { ids: [1] })) {
  const fetchMock = mockFetch({ 'POST /api/queue': post })
  const onClose = vi.fn()
  renderWithQuery(<NewItemDialog rows={rows} templates={templates} onClose={onClose} />)
  return { fetchMock, onClose, dialog: screen.getByRole('dialog', { name: 'New item' }) }
}

function posted(fetchMock: ReturnType<typeof mockFetch>): unknown[] {
  return fetchMock.mock.calls
    .filter(([url, init]) => String(url) === '/api/queue' && init?.method === 'POST')
    .map(([, init]) => JSON.parse(String(init?.body)))
}

async function pick(dialog: HTMLElement, combobox: string, option: string) {
  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: combobox }))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}

function inputLabels(dialog: HTMLElement): string[] {
  return within(dialog)
    .getAllByRole('textbox')
    .map((input) => input.getAttribute('id'))
    .map((id) => dialog.querySelector(`label[for="${id}"]`)?.textContent?.replace(/\s*\*$/, '') ?? '')
}

test('the templates come first with their type, then the raw types', async () => {
  const { dialog } = open()

  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Start from' }))
  const options = await screen.findAllByRole('option')
  // MUI's Select lists the group headers among the options.
  expect(options.map((option) => option.textContent)).toEqual([
    'Templates',
    'Computer (Secure note)',
    'Website (Login)',
    'Types',
    'Login',
    'Secure note',
    'Card',
    'Identity',
    'SSH key',
  ])
})

test('with templates there is no empty line', async () => {
  const { dialog } = open()

  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Start from' }))
  await screen.findAllByRole('option')
  expect(screen.queryByRole('option', { name: 'No templates yet' })).toBeNull()
})

test('without templates the heading stays with a disabled line that cannot be chosen', async () => {
  renderWithQuery(<NewItemDialog rows={rows} templates={[]} onClose={vi.fn()} />)
  const dialog = screen.getByRole('dialog', { name: 'New item' })

  const combobox = within(dialog).getByRole('combobox', { name: 'Start from' })
  fireEvent.mouseDown(combobox)
  const options = await screen.findAllByRole('option')
  expect(options.map((option) => option.textContent)).toEqual([
    'Templates',
    'No templates yet',
    'Types',
    'Login',
    'Secure note',
    'Card',
    'Identity',
    'SSH key',
  ])
  const line = screen.getByRole('option', { name: 'No templates yet' })
  expect(line.getAttribute('aria-disabled')).toBe('true')
  const before = combobox.textContent
  fireEvent.click(line)
  expect(combobox.textContent).toBe(before)
})

test('the chosen template shows its description under the select, a raw type or a template without one shows none', async () => {
  const { dialog } = open()
  const description = 'A laptop or desktop with its passwords'
  expect(dialog).not.toHaveTextContent(description)

  await pick(dialog, 'Start from', 'Computer (Secure note)')
  expect(within(dialog).getByText(description)).toBeInTheDocument()

  await pick(dialog, 'Start from', 'Website (Login)')
  expect(dialog).not.toHaveTextContent(description)

  await pick(dialog, 'Start from', 'Computer (Secure note)')
  await pick(dialog, 'Start from', 'Secure note')
  expect(dialog).not.toHaveTextContent(description)
})

test('a template offers its fields in its order, the secret one as a masked text input', async () => {
  const { dialog } = open()

  await pick(dialog, 'Start from', 'Computer (Secure note)')

  // The secret input is now a text input (role textbox), so it is among the labelled textboxes.
  expect(inputLabels(dialog)).toEqual(['Name', 'fields.user', 'fields.user password', 'notes'])
  expect(within(dialog).getByRole('checkbox', { name: 'fields.encrypted' })).not.toBeChecked()
  expectMasked(field(dialog, 'fields.user password'))
  expect(within(dialog).queryByRole('checkbox', { name: 'Generate a password' })).toBeNull()
  expect(dialog).not.toHaveTextContent(SECRETS_LEFT_EMPTY)
  expect(dialog).not.toHaveTextContent(GENERATED_PASSWORD)
})

test('a template secret is required and goes in secretValues, not in values', async () => {
  const { dialog, fetchMock, onClose } = open()

  await pick(dialog, 'Start from', 'Computer (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Box' } })
  fireEvent.change(field(dialog, 'fields.user'), { target: { value: 'root' } })
  fireEvent.change(field(dialog, 'notes'), { target: { value: 'rack' } })
  expect(create(dialog)).toBeDisabled()
  expect(dialog).toHaveTextContent('Required by the template')
  fireEvent.change(field(dialog, 'fields.user password'), { target: { value: 'hunter2' } })
  expect(create(dialog)).toBeEnabled()
  fireEvent.click(create(dialog))

  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(posted(fetchMock)).toEqual([
    {
      operations: [
        {
          kind: 'createItem',
          name: 'Box',
          template: 'Computer',
          values: { 'fields.user': 'root', notes: 'rack' },
          secretValues: { 'fields.user password': 'hunter2' },
        },
      ],
    },
  ])
})

test('one Show what I type shows every secret input and only a dialog with one has it', async () => {
  const { dialog } = open()

  await pick(dialog, 'Start from', 'Secure note')
  expect(within(dialog).queryByRole('checkbox', { name: 'Show what I type' })).toBeNull()

  await pick(dialog, 'Start from', 'Card')
  expect(within(dialog).getAllByRole('checkbox', { name: 'Show what I type' })).toHaveLength(1)
  expectMasked(field(dialog, 'card.number'))
  expectMasked(field(dialog, 'card.code'))
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Show what I type' }))
  expect(textSecurity(field(dialog, 'card.number'))).toBe('')
  expect(textSecurity(field(dialog, 'card.code'))).toBe('')
})

test('a raw login with Generate a password ticked has no password input and sends generatePassword', async () => {
  const { dialog, fetchMock, onClose } = open()

  await pick(dialog, 'Start from', 'Login')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Gen' } })
  expect(within(dialog).queryByLabelText(/^login\.password/)).toBeNull()
  expectMasked(within(dialog).getByLabelText(/^login\.totp/))
  expect(dialog).toHaveTextContent(`${SECRETS_LEFT_EMPTY} ${GENERATED_PASSWORD}`)
  fireEvent.click(create(dialog))

  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(posted(fetchMock)).toEqual([{ operations: [{ kind: 'createItem', name: 'Gen', type: 1, generatePassword: true }] }])
})

test('a raw login with the box unticked offers an optional password that goes in secretValues', async () => {
  const { dialog, fetchMock, onClose } = open()

  await pick(dialog, 'Start from', 'Login')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Typed' } })
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Generate a password' }))
  const password = field(dialog, 'login.password')
  expectMasked(password)
  expect(dialog).not.toHaveTextContent(GENERATED_PASSWORD)
  expect(create(dialog)).toBeEnabled()
  fireEvent.change(password, { target: { value: 'hunter2' } })
  fireEvent.click(create(dialog))

  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(posted(fetchMock)).toEqual([
    { operations: [{ kind: 'createItem', name: 'Typed', type: 1, secretValues: { 'login.password': 'hunter2' } }] },
  ])
})

test('a typed password is left out while Generate a password is ticked again', async () => {
  const { dialog, fetchMock, onClose } = open()

  await pick(dialog, 'Start from', 'Login')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Both' } })
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Generate a password' }))
  fireEvent.change(field(dialog, 'login.password'), { target: { value: 'hunter2' } })
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Generate a password' }))
  fireEvent.click(create(dialog))

  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(posted(fetchMock)).toEqual([{ operations: [{ kind: 'createItem', name: 'Both', type: 1, generatePassword: true }] }])
})

test('a template with login.password needs it typed once the box is unticked', async () => {
  const { dialog, fetchMock, onClose } = open()

  await pick(dialog, 'Start from', 'Website (Login)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Site' } })
  fireEvent.change(field(dialog, 'login.username'), { target: { value: 'me' } })
  fireEvent.change(field(dialog, 'login.uris'), { target: { value: 'https://a.example' } })
  expect(within(dialog).queryByLabelText(/^login\.password/)).toBeNull()
  expect(create(dialog)).toBeEnabled()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Generate a password' }))
  expect(create(dialog)).toBeDisabled()
  fireEvent.change(field(dialog, 'login.password'), { target: { value: 'hunter2' } })
  fireEvent.click(create(dialog))

  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(posted(fetchMock)).toEqual([
    {
      operations: [
        {
          kind: 'createItem',
          name: 'Site',
          template: 'Website',
          values: { 'login.username': 'me', 'login.uris': ['https://a.example'] },
          secretValues: { 'login.password': 'hunter2' },
        },
      ],
    },
  ])
})

test('an SSH private key is multiline, a blank raw secret sends nothing', async () => {
  const { dialog, fetchMock, onClose } = open()

  await pick(dialog, 'Start from', 'SSH key')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Key' } })
  const key = field(dialog, 'sshKey.privateKey')
  expect(key.tagName).toBe('TEXTAREA')
  expect(create(dialog)).toBeEnabled()
  fireEvent.click(create(dialog))

  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(posted(fetchMock)).toEqual([{ operations: [{ kind: 'createItem', name: 'Key', type: 5 }] }])
})

test('newItemFields requires every non-boolean field of a template, secrets too, and none of a raw type', () => {
  const template: Template = {
    name: 'Typed',
    type: 2,
    coordinates: [
      { kind: 'path', path: 'name', datatype: 'name-id' },
      { kind: 'field', name: 'host', fieldType: 0, datatype: 'host-id' },
      { kind: 'field', name: 'encrypted', fieldType: 2 },
      { kind: 'path', path: 'notes' },
      { kind: 'field', name: 'pin', fieldType: 1, datatype: 'pin-id' },
    ],
  }

  expect(newItemFields(2, template)).toEqual([
    { column: 'fields.host', fieldType: 0, datatype: 'host-id', required: true, optional: false, secret: false, pattern: null },
    { column: 'fields.encrypted', fieldType: 2, datatype: null, required: false, optional: false, secret: false, pattern: null },
    { column: 'notes', fieldType: 0, datatype: null, required: true, optional: false, secret: false, pattern: null },
    { column: 'fields.pin', fieldType: 0, datatype: 'pin-id', required: true, optional: false, secret: true, pattern: null },
  ])
  expect(newItemFields(1, null).every((field) => !field.required && !field.optional && field.datatype === null)).toBe(true)
})

test('newItemFields does not require an optional field of a template, secret or not', () => {
  const template: Template = {
    name: 'Loose',
    type: 2,
    coordinates: [
      { kind: 'field', name: 'host', fieldType: 0 },
      { kind: 'field', name: 'serial', fieldType: 0, optional: true },
      { kind: 'field', name: 'pin', fieldType: 1, optional: true },
    ],
  }

  expect(newItemFields(2, template).map(({ column, required, optional }) => ({ column, required, optional }))).toEqual([
    { column: 'fields.host', required: true, optional: false },
    { column: 'fields.serial', required: false, optional: true },
    { column: 'fields.pin', required: false, optional: true },
  ])
})

test('a raw type offers its secret slots in their order, marked secret', () => {
  const secrets = (type: number) =>
    newItemFields(type, null)
      .filter((field) => field.secret)
      .map((field) => field.column)
  expect(secrets(1)).toEqual(['login.password', 'login.totp'])
  expect(secrets(2)).toEqual([])
  expect(secrets(3)).toEqual(['card.number', 'card.code'])
  expect(secrets(5)).toEqual(['sshKey.privateKey'])
  expect(newItemFields(1, null).map((field) => field.column)).toEqual([
    'login.username',
    'login.password',
    'login.totp',
    'login.uris',
    'notes',
  ])
  expect(newItemFields(2, null).map((field) => field.column)).toEqual(['notes'])
  expect(newItemFields(5, null).map((field) => field.column)).toEqual([
    'sshKey.privateKey',
    'sshKey.publicKey',
    'sshKey.keyFingerprint',
    'notes',
  ])
})

test('only a login has Generate a password, on by default', async () => {
  const { dialog } = open()

  await pick(dialog, 'Start from', 'Card')
  expect(within(dialog).queryByRole('checkbox', { name: 'Generate a password' })).toBeNull()

  await pick(dialog, 'Start from', 'Login')
  const generate = within(dialog).getByRole('checkbox', { name: 'Generate a password' })
  expect(generate).toBeChecked()
  expect(dialog).toHaveTextContent(`${SECRETS_LEFT_EMPTY} ${GENERATED_PASSWORD}`)
  fireEvent.click(generate)
  expect(dialog).toHaveTextContent(SECRETS_LEFT_EMPTY)
  expect(dialog).not.toHaveTextContent(GENERATED_PASSWORD)
})

test('an organization no row uses is offered with its collections', async () => {
  mockFetch({})
  renderWithQuery(
    <NewItemDialog rows={rows} organizations={[{ name: 'Book club', collections: ['Fiction', 'Novels'] }]} templates={templates} onClose={vi.fn()} />,
  )
  const dialog = screen.getByRole('dialog', { name: 'New item' })

  await pick(dialog, 'Organization', 'Book club')

  expect(within(dialog).getByRole('checkbox', { name: 'Fiction' })).toBeInTheDocument()
  expect(within(dialog).getByRole('checkbox', { name: 'Novels' })).toBeInTheDocument()
})

test('an organization with no collections is a disabled option with the reason', async () => {
  mockFetch({})
  renderWithQuery(
    <NewItemDialog
      rows={rows}
      organizations={[
        { name: 'Book club', collections: [] },
        { name: 'Choir', collections: ['Songs'] },
      ]}
      templates={templates}
      onClose={vi.fn()}
    />,
  )
  const dialog = screen.getByRole('dialog', { name: 'New item' })

  fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Organization' }))
  const empty = await screen.findByRole('option', { name: 'Book club has no collections, create one in Bitwarden first' })
  expect(empty).toHaveAttribute('aria-disabled', 'true')
  expect(screen.getByRole('option', { name: 'Choir' })).not.toHaveAttribute('aria-disabled')
})

test('Create queues a createItem with the folder, organization, values and generated password', async () => {
  const { dialog, fetchMock, onClose } = open()

  await pick(dialog, 'Start from', 'Website (Login)')
  expect(within(dialog).getByRole('button', { name: 'Create' })).toBeDisabled()
  fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: ' NAS admin ' } })
  await pick(dialog, 'Folder', 'New folder')
  fireEvent.change(within(dialog).getByLabelText('New folder name'), { target: { value: 'Servers' } })
  await pick(dialog, 'Organization', 'Family')
  expect(within(dialog).getByRole('button', { name: 'Create' })).toBeDisabled()
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Shared' }))
  fireEvent.change(within(dialog).getByLabelText(/^login\.username/), { target: { value: 'admin' } })
  fireEvent.change(within(dialog).getByLabelText(/^login\.uris/), {
    target: { value: 'https://nas.local\n\n https://nas.example.org ' },
  })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }))

  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(posted(fetchMock)).toEqual([
    {
      operations: [
        {
          kind: 'createItem',
          name: 'NAS admin',
          template: 'Website',
          folderName: 'Servers',
          organizationName: 'Family',
          collectionNames: ['Shared'],
          values: { 'login.username': 'admin', 'login.uris': ['https://nas.local', 'https://nas.example.org'] },
          generatePassword: true,
        },
      ],
    },
  ])
})

test('a raw type with an existing folder and no values sends only what was chosen', async () => {
  const { dialog, fetchMock, onClose } = open()

  await pick(dialog, 'Start from', 'Secure note')
  fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: 'Wifi' } })
  await pick(dialog, 'Folder', 'Work')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }))

  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(posted(fetchMock)).toEqual([{ operations: [{ kind: 'createItem', name: 'Wifi', type: 2, folderName: 'Work' }] }])
})

test('a refusal shows its reason in the dialog and keeps it open', async () => {
  const { dialog, onClose } = open(() =>
    jsonResponse(422, {
      detail: "Operation 1 (createItem): a personal secure note named bwtidy would be taken for bwtidy's own note",
    }),
  )

  await pick(dialog, 'Start from', 'Secure note')
  fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: 'bwtidy' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }))

  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    "Nothing was queued: a personal secure note named bwtidy would be taken for bwtidy's own note",
  )
  expect(onClose).not.toHaveBeenCalled()
  expect(within(dialog).getByRole('button', { name: 'Create' })).toBeEnabled()
})

test('the text next to Generate a password names the default', async () => {
  mockFetch({
    'GET /api/generator': () =>
      jsonResponse(200, {
        default: { type: 'passphrase', words: 4, separator: '-', capitalize: false, includeNumber: false },
        items: {},
        saving: false,
      }),
  })
  renderWithQuery(<NewItemDialog rows={rows} templates={templates} onClose={vi.fn()} />)
  const dialog = screen.getByRole('dialog', { name: 'New item' })
  expect(screen.queryByTestId('generator-default')).toBeNull()

  await pick(dialog, 'Start from', 'Login')

  expect(await within(dialog).findByText('4 words')).toBeInTheDocument()
  expect(within(dialog).getByRole('checkbox', { name: 'Generate a password' })).toBeChecked()
})

test('no type but a login asks for the generator settings', async () => {
  const fetchMock = mockFetch({})
  renderWithQuery(<NewItemDialog rows={rows} templates={templates} onClose={vi.fn()} />)
  const dialog = screen.getByRole('dialog', { name: 'New item' })

  await pick(dialog, 'Start from', 'Secure note')

  expect(fetchMock.mock.calls.filter(([url]) => String(url) === '/api/generator')).toHaveLength(0)
})

const DATATYPE_ID = 'dt-code'

function datatype(id: string, shortest: number | null = 3) {
  return {
    id,
    name: id,
    pattern: '[0-9]{3}',
    description: '',
    ignoreCase: false,
    examples: [],
    counterExamples: [],
    states: 4,
    maxRepeat: 1,
    shortest,
    broken: null,
  }
}

function result(score: number, tooLong = false) {
  return { distance: score === 1 ? 0 : 1, score, firstEdit: score === 1 ? null : 0, expected: score === 1 ? null : 'a digit', tooLong }
}

function openTemplate(
  template: Template,
  checks: { shortest?: number | null; check?: (value: string) => Response } = {},
) {
  const check = checks.check ?? (() => jsonResponse(200, result(1)))
  const fetchMock = mockFetch({
    'POST /api/queue': () => jsonResponse(201, { ids: [1] }),
    'GET /api/datatypes': () =>
      jsonResponse(200, { datatypes: [datatype(DATATYPE_ID, checks.shortest === undefined ? 3 : checks.shortest)], saving: false }),
    [`POST /api/datatypes/${DATATYPE_ID}/check`]: (init) => check(String(JSON.parse(String(init.body)).value)),
  })
  const onClose = vi.fn()
  renderWithQuery(<NewItemDialog rows={rows} templates={[template]} onClose={onClose} />)
  return { fetchMock, onClose, dialog: screen.getByRole('dialog', { name: 'New item' }) }
}

function field(dialog: HTMLElement, label: string): HTMLElement {
  return within(dialog).getByLabelText(new RegExp(`^${label.replace('.', '\\.')}\\s*\\*?$`))
}

// A secret input is a plain text input masked with CSS, never a password input, and carries the
// password managers' opt-out (see noPasswordManager.ts).
function expectMasked(input: HTMLElement): void {
  expect(input).toHaveAttribute('type', 'text')
  expect(input).toHaveAttribute('autocomplete', 'off')
  expect(input).toHaveAttribute('data-bwignore', 'true')
  expect(textSecurity(input)).toBe('disc')
}

function create(dialog: HTMLElement): HTMLElement {
  return within(dialog).getByRole('button', { name: 'Create' })
}

const pair: Template = {
  name: 'Pair',
  type: 2,
  coordinates: [
    { kind: 'field', name: 'one', fieldType: 0 },
    { kind: 'field', name: 'two', fieldType: 0 },
    { kind: 'field', name: 'flag', fieldType: 2 },
  ],
}

const coded: Template = {
  name: 'Coded',
  type: 2,
  coordinates: [{ kind: 'field', name: 'code', fieldType: 0, datatype: DATATYPE_ID }],
}

test('a raw type needs only a Name', async () => {
  const { dialog } = open()

  await pick(dialog, 'Start from', 'Login')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Plain' } })

  expect(create(dialog)).toBeEnabled()
})

test('a template needs every text field filled, a boolean left unticked is fine', async () => {
  const { dialog } = openTemplate(pair)

  await pick(dialog, 'Start from', 'Pair (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Both' } })
  expect(create(dialog)).toBeDisabled()
  expect(dialog).toHaveTextContent('Required by the template')
  fireEvent.change(field(dialog, 'fields.one'), { target: { value: 'a' } })
  expect(create(dialog)).toBeDisabled()
  fireEvent.change(field(dialog, 'fields.two'), { target: { value: ' ' } })
  expect(create(dialog)).toBeDisabled()
  fireEvent.change(field(dialog, 'fields.two'), { target: { value: 'b' } })

  expect(create(dialog)).toBeEnabled()
  expect(within(dialog).getByRole('checkbox', { name: 'fields.flag' })).not.toBeChecked()
})

test('an optional field says so in its label and may stay blank, a value is still checked', async () => {
  const loose: Template = {
    name: 'Loose',
    type: 2,
    coordinates: [
      { kind: 'field', name: 'one', fieldType: 0 },
      { kind: 'field', name: 'code', fieldType: 0, datatype: DATATYPE_ID, optional: true },
    ],
  }
  const { dialog } = openTemplate(loose, { check: () => jsonResponse(200, result(0.5)) })

  await pick(dialog, 'Start from', 'Loose (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Some' } })
  fireEvent.change(field(dialog, 'fields.one'), { target: { value: 'a' } })
  const code = within(dialog).getByLabelText('fields.code (optional)')
  expect(code).not.toBeRequired()
  expect(create(dialog)).toBeEnabled()
  expect(dialog).not.toHaveTextContent('Required by the template')

  fireEvent.change(code, { target: { value: 'x' } })
  expect(create(dialog)).toBeDisabled()
  expect(await within(dialog).findByText(/expected a digit/)).toBeInTheDocument()
  expect(create(dialog)).toBeDisabled()
})

test('a value off its format shows the text and blocks Create, a matching one frees it', async () => {
  const { dialog } = openTemplate(coded, { check: (value) => jsonResponse(200, result(value === '123' ? 1 : 0.8)) })

  await pick(dialog, 'Start from', 'Coded (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Item' } })
  fireEvent.change(field(dialog, 'fields.code'), { target: { value: '12x' } })
  // Pending until the answer is in.
  expect(create(dialog)).toBeDisabled()
  expect(await within(dialog).findByText('matches format 80%, expected a digit at position 1')).toBeInTheDocument()
  expect(create(dialog)).toBeDisabled()

  fireEvent.change(field(dialog, 'fields.code'), { target: { value: '123' } })
  expect(create(dialog)).toBeDisabled()
  expect(await within(dialog).findByText('matches format')).toBeInTheDocument()
  expect(create(dialog)).toBeEnabled()

  fireEvent.change(field(dialog, 'fields.code'), { target: { value: '1234' } })
  expect(create(dialog)).toBeDisabled()
})

test('a too long answer blocks', async () => {
  const { dialog } = openTemplate(coded, { check: () => jsonResponse(200, { ...result(0), tooLong: true }) })

  await pick(dialog, 'Start from', 'Coded (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Item' } })
  fireEvent.change(field(dialog, 'fields.code'), { target: { value: '1'.repeat(500) } })

  expect(await within(dialog).findByText('too long to check')).toBeInTheDocument()
  expect(create(dialog)).toBeDisabled()
})

test('a refused check does not block and shows nothing', async () => {
  const { dialog, fetchMock } = openTemplate(coded, { check: () => jsonResponse(404, { detail: 'Not Found' }) })

  await pick(dialog, 'Start from', 'Coded (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Item' } })
  fireEvent.change(field(dialog, 'fields.code'), { target: { value: '123' } })

  await waitFor(() => expect(create(dialog)).toBeEnabled())
  expect(dialog).not.toHaveTextContent('matches format')
  const urls = fetchMock.mock.calls.map(([url]) => String(url))
  expect(urls.some((url) => url.includes('123'))).toBe(false)
})

test('a datatype that matches blank lets the field stay blank but checks a filled value', async () => {
  const { dialog } = openTemplate(coded, {
    shortest: 0,
    check: (value) => jsonResponse(200, result(value === '123' ? 1 : 0.5)),
  })

  await pick(dialog, 'Start from', 'Coded (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Item' } })
  await waitFor(() => expect(create(dialog)).toBeEnabled())

  fireEvent.change(field(dialog, 'fields.code'), { target: { value: 'x' } })
  expect(await within(dialog).findByText(/^matches format 50%/)).toBeInTheDocument()
  expect(create(dialog)).toBeDisabled()
  fireEvent.change(field(dialog, 'fields.code'), { target: { value: '' } })
  expect(create(dialog)).toBeEnabled()
})

test('a blank required field blocks while the datatypes load', async () => {
  const { dialog } = openTemplate(coded)

  await pick(dialog, 'Start from', 'Coded (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Item' } })

  expect(create(dialog)).toBeDisabled()
})

test('a datatype the list does not name counts as none', async () => {
  const unknown: Template = {
    name: 'Unknown',
    type: 2,
    coordinates: [{ kind: 'field', name: 'code', fieldType: 0, datatype: 'gone' }],
  }
  const { dialog, fetchMock } = openTemplate(unknown)

  await pick(dialog, 'Start from', 'Unknown (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Item' } })
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === '/api/datatypes')).toBe(true))
  fireEvent.change(field(dialog, 'fields.code'), { target: { value: 'whatever' } })

  await waitFor(() => expect(create(dialog)).toBeEnabled())
  expect(dialog).not.toHaveTextContent('matches format')
})

test('a name coordinate with a datatype checks the Name input', async () => {
  const named: Template = {
    name: 'Named',
    type: 2,
    coordinates: [{ kind: 'path', path: 'name', datatype: DATATYPE_ID }],
  }
  const { dialog } = openTemplate(named, { check: (value) => jsonResponse(200, result(value === '123' ? 1 : 0.8)) })

  await pick(dialog, 'Start from', 'Named (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'abc' } })
  expect(await within(dialog).findByText(/^matches format 80%/)).toBeInTheDocument()
  expect(create(dialog)).toBeDisabled()

  fireEvent.change(field(dialog, 'Name'), { target: { value: '123' } })
  await waitFor(() => expect(create(dialog)).toBeEnabled())
})

test('login.uris with a datatype checks each line and one off line blocks', async () => {
  const site: Template = {
    name: 'Site',
    type: 1,
    coordinates: [{ kind: 'path', path: 'login.uris', datatype: DATATYPE_ID }],
  }
  const { dialog } = openTemplate(site, { check: (value) => jsonResponse(200, result(value === '123' ? 1 : 0.8)) })

  await pick(dialog, 'Start from', 'Site (Login)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Item' } })
  fireEvent.change(field(dialog, 'login.uris'), { target: { value: '123\n\n abc ' } })

  expect(await within(dialog).findByText(/^One URI per line\. matches format 80%/)).toBeInTheDocument()
  expect(create(dialog)).toBeDisabled()
  fireEvent.change(field(dialog, 'login.uris'), { target: { value: '123\n123' } })
  await waitFor(() => expect(create(dialog)).toBeEnabled())
})

test('a datatype on a hidden field blocks an off value like a text field', async () => {
  const pin: Template = {
    name: 'Pin',
    type: 2,
    coordinates: [{ kind: 'field', name: 'pin', fieldType: 1, datatype: DATATYPE_ID }],
  }
  const { dialog } = openTemplate(pin, { check: (value) => jsonResponse(200, result(value === '123' ? 1 : 0.8)) })

  await pick(dialog, 'Start from', 'Pin (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Item' } })
  expect(create(dialog)).toBeDisabled()
  fireEvent.change(field(dialog, 'fields.pin'), { target: { value: '12x' } })
  expect(await within(dialog).findByText(/^matches format 80%/)).toBeInTheDocument()
  expectMasked(field(dialog, 'fields.pin'))
  expect(create(dialog)).toBeDisabled()

  fireEvent.change(field(dialog, 'fields.pin'), { target: { value: '123' } })
  await waitFor(() => expect(create(dialog)).toBeEnabled())
})

function labelled(dialog: HTMLElement, label: string): HTMLElement {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return within(dialog).getByLabelText(new RegExp(`^${escaped}\\s*\\*?$`))
}

const shared: Template = {
  name: 'Shared',
  type: 2,
  coordinates: [
    { kind: 'field', name: 'user_admin', fieldType: 0 },
    { kind: 'fieldPattern', pattern: 'user_*', fieldType: 1 },
    { kind: 'fieldPattern', pattern: 'pc_*', fieldType: 0, optional: true },
  ],
}

test('a pattern asks for a field name that starts from its prefix and must fit it', async () => {
  const { dialog, fetchMock, onClose } = openTemplate(shared)

  await pick(dialog, 'Start from', 'Shared (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Office' } })
  fireEvent.change(labelled(dialog, 'fields.user_admin'), { target: { value: 'root' } })
  const name = labelled(dialog, 'Field name for fields.user_*')
  expect(name).toHaveValue('user_')
  expect(labelled(dialog, 'Field name for fields.pc_*')).toHaveValue('pc_')
  // a required pattern needs its value, typed hidden
  const value = labelled(dialog, 'fields.user_*')
  expectMasked(value)
  expect(create(dialog)).toBeDisabled()
  fireEvent.change(value, { target: { value: 'hunter2' } })
  fireEvent.change(name, { target: { value: 'admin_dev' } })
  expect(create(dialog)).toBeDisabled()
  expect(dialog).toHaveTextContent('The name must fit user_*')
  // a field the template names on its own is not the pattern's
  fireEvent.change(name, { target: { value: 'User_Admin' } })
  expect(create(dialog)).toBeDisabled()
  expect(dialog).toHaveTextContent('fields.User_Admin is another field of the template')
  fireEvent.change(name, { target: { value: 'user_dev' } })
  // the optional pattern left blank is left out, whatever its name
  fireEvent.change(labelled(dialog, 'Field name for fields.pc_*'), { target: { value: 'nope' } })
  expect(create(dialog)).toBeEnabled()
  fireEvent.click(create(dialog))

  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(posted(fetchMock)).toEqual([
    {
      operations: [
        {
          kind: 'createItem',
          name: 'Office',
          template: 'Shared',
          values: { 'fields.user_admin': 'root' },
          secretValues: { 'fields.user_dev': 'hunter2' },
        },
      ],
    },
  ])
})

test('a filled optional pattern needs a name that fits it', async () => {
  const { dialog, fetchMock, onClose } = openTemplate(shared)

  await pick(dialog, 'Start from', 'Shared (Secure note)')
  fireEvent.change(field(dialog, 'Name'), { target: { value: 'Office' } })
  fireEvent.change(labelled(dialog, 'fields.user_admin'), { target: { value: 'root' } })
  fireEvent.change(labelled(dialog, 'fields.user_*'), { target: { value: 'hunter2' } })
  fireEvent.change(labelled(dialog, 'Field name for fields.user_*'), { target: { value: 'user_a' } })
  fireEvent.change(labelled(dialog, 'fields.pc_* (optional)'), { target: { value: 'desk' } })
  fireEvent.change(labelled(dialog, 'Field name for fields.pc_*'), { target: { value: 'laptop' } })
  expect(create(dialog)).toBeDisabled()
  fireEvent.change(labelled(dialog, 'Field name for fields.pc_*'), { target: { value: 'PC_desk' } })
  expect(create(dialog)).toBeEnabled()
  fireEvent.click(create(dialog))

  await waitFor(() => expect(onClose).toHaveBeenCalled())
  const [body] = posted(fetchMock) as { operations: { values: unknown }[] }[]
  expect(body.operations[0].values).toEqual({ 'fields.user_admin': 'root', 'fields.PC_desk': 'desk' })
})
