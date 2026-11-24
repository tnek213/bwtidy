import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { jsonResponse, mockFetch, renderWithQuery, waitForPolls } from '../test-utils.tsx'
import type { CheckRequest, Datatype, FormatResult } from '../api/datatypes.ts'
import { resultText, typedPattern, valueLines, whereItDiffers } from './patterns.ts'
import DatatypesTab from './DatatypesTab.tsx'
import type { Template } from '../api/templates.ts'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const pin: Datatype = {
  id: '1f0c9a3e',
  name: 'PIN',
  pattern: '\\d{4,8}',
  description: 'A short code',
  ignoreCase: false,
  wholeValue: true,
  examples: ['1234'],
  counterExamples: ['12a4'],
  states: 10,
  maxRepeat: 8,
  shortest: 4,
  broken: null,
}

const iban: Datatype = {
  ...pin,
  id: '2a7c1d04',
  name: 'IBAN',
  pattern: '(',
  description: '',
  examples: [],
  counterExamples: [],
  states: null,
  maxRepeat: null,
  shortest: null,
  broken: 'unbalanced parenthesis (column 1)',
}

const computer: Template = {
  name: 'Computer',
  type: 2,
  coordinates: [
    { kind: 'field', name: 'user', fieldType: 0 },
    { kind: 'field', name: 'bios pin', fieldType: 1, datatype: pin.id },
    { kind: 'field', name: 'disk pin', fieldType: 1, datatype: pin.id },
  ],
}

const FULL: FormatResult = { distance: 0, score: 1, firstEdit: null, expected: null, tooLong: false }

// A stand-in for the engine: four to eight digits match, anything else is one
// change at the first character that is no digit.
function judged(value: string): FormatResult {
  if (/^\d{4,8}$/.test(value)) {
    return FULL
  }
  const firstEdit = Math.max(value.search(/\D/), 0)
  return { distance: 1, score: 0.75, firstEdit, expected: 'a digit', tooLong: false }
}

function checkAnswer(init: RequestInit): Response {
  const request = JSON.parse(String(init.body)) as CheckRequest
  if (request.pattern.includes('(')) {
    return jsonResponse(200, { error: { message: 'unbalanced parenthesis', column: request.pattern.indexOf('(') + 1 } })
  }
  return jsonResponse(200, {
    states: 10,
    maxRepeat: 8,
    shortest: 4,
    examples: request.examples.map(judged),
    counterExamples: request.counterExamples.map(judged),
    samples: request.samples.map(judged),
  })
}

const queued = () => jsonResponse(202, { entryId: 7, datatypes: [] })

function renderTab(put: (init: RequestInit) => Response = queued, datatypes: () => unknown = () => ({ datatypes: [pin], saving: false })) {
  const fetchMock = mockFetch({
    'GET /api/datatypes': () => jsonResponse(200, datatypes()),
    'GET /api/templates': () => jsonResponse(200, { templates: [computer], saving: false }),
    'GET /api/queue?status=pending': () => jsonResponse(200, []),
    'GET /api/queue/auto-commit': () =>
      jsonResponse(200, { afterSeconds: 10, maxEntries: 20, pending: 0, committing: false, nextCommitAt: null }),
    'POST /api/datatypes/check': checkAnswer,
    'PUT /api/datatypes': put,
  })
  renderWithQuery(<DatatypesTab onStatistics={() => {}} />)
  return fetchMock
}

function puts(fetchMock: ReturnType<typeof mockFetch>): unknown[] {
  return fetchMock.mock.calls
    .filter(([path, init]) => path === '/api/datatypes' && init?.method === 'PUT')
    .map(([, init]) => JSON.parse(String(init?.body)))
}

function checks(fetchMock: ReturnType<typeof mockFetch>): CheckRequest[] {
  return fetchMock.mock.calls
    .filter(([path]) => path === '/api/datatypes/check')
    .map(([, init]) => JSON.parse(String(init?.body)) as CheckRequest)
}

async function openNew(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole('button', { name: 'New datatype' }))
  return screen.findByRole('dialog', { name: 'New datatype' })
}

function type(scope: HTMLElement, label: string, value: string) {
  fireEvent.change(within(scope).getByRole('textbox', { name: label }), { target: { value } })
}

const save = (scope: HTMLElement) => within(scope).getByRole('button', { name: 'Save' })

// The marks in the gutter of one box, one per line that holds a value.
const marks = (scope: HTMLElement, box: string) =>
  within(within(scope).getByRole('group', { name: `${box} marks` })).queryAllByRole('img')

test('the list shows name, pattern, description and how many coordinates use it, with a broken one marked', async () => {
  renderTab(queued, () => ({ datatypes: [pin, iban], saving: false }))

  const table = await screen.findByRole('table', { name: 'Datatypes' })
  await waitFor(() => expect(within(table).getAllByRole('row')[1]).toHaveTextContent('2'))
  const [, first, second] = within(table).getAllByRole('row')
  expect(first).toHaveTextContent('PIN')
  expect(first).toHaveTextContent('\\d{4,8}')
  expect(first).toHaveTextContent('A short code')
  expect(first).not.toHaveTextContent('Broken')
  expect(second).toHaveTextContent('Broken')
  expect(second).toHaveTextContent('unbalanced parenthesis (column 1)')
  expect(screen.getByText(/no save goes through until it is fixed or deleted/)).toBeInTheDocument()
})

test('no datatypes yet says so', async () => {
  renderTab(queued, () => ({ datatypes: [], saving: false }))
  expect(await screen.findByText('No datatypes yet.')).toBeInTheDocument()
})

test('the editor warns that MCP can read the pattern and the examples', async () => {
  renderTab()
  const dialog = await openNew()
  const warning = /MCP can read the pattern, the examples and the counter-examples of a datatype that a non-secret column/
  expect(within(dialog).getByText(warning)).toBeInTheDocument()
})

test('the regular expression field says what it supports, with Ignore case under it', async () => {
  renderTab()
  const dialog = await openNew()

  const field = within(dialog).getByRole('textbox', { name: 'Regular expression' })
  expect(within(dialog).getByText('. [ ] \\d \\w \\s ( | ) * + ? {n,m}')).toBeInTheDocument()
  const ignoreCase = within(dialog).getByRole('checkbox', { name: 'Ignore case' })
  const description = within(dialog).getByRole('textbox', { name: 'Description' })
  const name = within(dialog).getByRole('textbox', { name: 'Name' })
  expect(name.compareDocumentPosition(field) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(field.compareDocumentPosition(ignoreCase) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(ignoreCase.compareDocumentPosition(description) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

  fireEvent.mouseOver(within(dialog).getByRole('button', { name: 'Regular expression syntax' }))
  const help = await screen.findByRole('tooltip')
  expect(help).toHaveTextContent('groups, none of them capture')
  expect(help).toHaveTextContent('Not supported: lookarounds, backreferences, lazy or possessive quantifiers')
})

test('the editor shows the facts and marks each line live, debounced into one check', async () => {
  const fetchMock = renderTab()
  const dialog = await openNew()

  expect(checks(fetchMock)).toHaveLength(0)
  type(dialog, 'Regular expression', '\\')
  type(dialog, 'Regular expression', '\\d{4,8}')
  type(dialog, 'Examples', '1234\n\n56789')
  type(dialog, 'Counter-examples', '12a4')

  expect(await within(dialog).findByText('10 states, largest repeat 8, shortest match 4')).toBeInTheDocument()
  expect(checks(fetchMock)).toEqual([
    {
      pattern: '\\d{4,8}',
      ignoreCase: false,
      wholeValue: true,
      examples: ['1234', '56789'],
      counterExamples: ['12a4'],
      samples: [],
    },
  ])

  // The empty line holds no value and gets no mark.
  const examples = marks(dialog, 'Examples')
  expect(examples).toHaveLength(2)
  expect(examples[0]).toHaveAccessibleName('matches')
  expect(examples[0]).toHaveTextContent('✓')
  expect(examples[0]).toHaveAttribute('data-mark', 'right')
  expect(examples[0]).toHaveAccessibleDescription('matches format 100%, distance 0')

  const [counter] = marks(dialog, 'Counter-examples')
  expect(counter).toHaveAccessibleName('does not match')
  expect(counter).toHaveTextContent('✗')
  expect(counter).toHaveAttribute('data-mark', 'right')
  expect(counter).toHaveAccessibleDescription('matches format 75%, distance 1: 1 change at position 3, expected a digit')

  fireEvent.mouseOver(counter)
  expect(await screen.findByRole('tooltip')).toHaveTextContent('1 change at position 3, expected a digit')
})

test('an example that does not match and a counter-example that does are marked wrong, and Save stays disabled until they are right', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  renderTab()
  const dialog = await openNew()

  type(dialog, 'Name', 'Code')
  type(dialog, 'Regular expression', '\\d{4,8}')
  type(dialog, 'Examples', '1234\n12a4')
  expect(save(dialog)).toBeDisabled()

  await waitForPolls(() => expect(marks(dialog, 'Examples')[1]).toHaveAttribute('data-mark', 'wrong'))
  const [right, wrong] = marks(dialog, 'Examples')
  expect(right).toHaveAttribute('data-mark', 'right')
  expect(wrong).toHaveAccessibleName('wrong: does not match but is an example')
  expect(within(dialog).getAllByTestId('wrong-line')).toHaveLength(1)
  expect(save(dialog)).toBeDisabled()

  type(dialog, 'Examples', '1234')
  await waitForPolls(() => expect(marks(dialog, 'Examples')[0]).toHaveAttribute('data-mark', 'right'))
  expect(within(dialog).queryByTestId('wrong-line')).not.toBeInTheDocument()
  await waitForPolls(() => expect(save(dialog)).toBeEnabled())

  type(dialog, 'Counter-examples', '5678')
  // Disabled at once and grey, before the check of the new line has answered.
  expect(save(dialog)).toBeDisabled()
  expect(marks(dialog, 'Counter-examples')[0]).toHaveAttribute('data-mark', 'pending')
  await waitForPolls(() => expect(marks(dialog, 'Counter-examples')[0]).toHaveAttribute('data-mark', 'wrong'))
  expect(marks(dialog, 'Counter-examples')[0]).toHaveAccessibleName('wrong: matches but is a counter-example')
  expect(save(dialog)).toBeDisabled()

  type(dialog, 'Counter-examples', '56a8')
  await waitForPolls(() => expect(save(dialog)).toBeEnabled())
})

test('the marks are grey while the regular expression has an error', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  renderTab()
  const dialog = await openNew()

  type(dialog, 'Regular expression', '\\d{4,8}')
  type(dialog, 'Examples', '1234')
  await waitForPolls(() => expect(marks(dialog, 'Examples')[0]).toHaveAttribute('data-mark', 'right'))

  type(dialog, 'Regular expression', '\\d{4,8}(')
  await within(dialog).findByRole('alert', { name: 'Pattern error' })
  const [mark] = marks(dialog, 'Examples')
  expect(mark).toHaveAttribute('data-mark', 'pending')
  expect(mark).toHaveAccessibleName('not checked yet')
  expect(mark).toHaveAccessibleDescription('Not checked: the regular expression has an error.')
})

test('a pattern error shows its column marked under the pattern, and Save stays disabled', async () => {
  renderTab()
  const dialog = await openNew()

  type(dialog, 'Name', 'Code')
  type(dialog, 'Regular expression', 'ab(')

  const error = await within(dialog).findByRole('alert', { name: 'Pattern error' })
  expect(error).toHaveTextContent('unbalanced parenthesis (column 3)')
  expect(within(error).getByTestId('pattern-caret').textContent).toBe('ab(\n  ^')
  expect(save(dialog)).toBeDisabled()
  expect(within(dialog).queryByText(/states, largest repeat/)).not.toBeInTheDocument()
})

test('every Try line gets a mark, and Try never decides Save', async () => {
  const fetchMock = renderTab()
  const dialog = await openNew()

  type(dialog, 'Name', 'Code')
  type(dialog, 'Regular expression', '\\d{4,8}')
  await waitFor(() => expect(save(dialog)).toBeEnabled())

  type(dialog, 'Try', '12x\n1234')
  // The rules did not change, so Save stays enabled while the check runs.
  expect(save(dialog)).toBeEnabled()
  await waitFor(() => expect(checks(fetchMock).at(-1)?.samples).toEqual(['12x', '1234']))
  await waitFor(() => expect(marks(dialog, 'Try')[0]).toHaveAttribute('data-mark', 'right'))
  const [miss, full] = marks(dialog, 'Try')
  expect(miss).toHaveAccessibleName('does not match')
  expect(miss).toHaveAccessibleDescription('matches format 75%, distance 1: 1 change at position 3, expected a digit')
  expect(full).toHaveAccessibleName('matches')
  expect(full).toHaveAttribute('data-mark', 'right')
  expect(save(dialog)).toBeEnabled()
})

test('Save puts the new datatype alone, without an id, closes at once and shows Saving', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  let stored: unknown = { datatypes: [pin], saving: false }
  let answer: (response: Response) => void = () => {}
  const fetchMock = renderTab(
    () => {
      stored = { datatypes: [pin, { ...pin, id: '3b8d2e15', name: 'Code', pattern: '\\d{4}' }], saving: true }
      return new Promise<Response>((resolve) => {
        answer = resolve
      }) as unknown as Response
    },
    () => stored,
  )
  const dialog = await openNew()

  type(dialog, 'Name', ' Code ')
  type(dialog, 'Regular expression', '\\d{4}')
  type(dialog, 'Description', 'Four digits')
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Ignore case' }))
  type(dialog, 'Examples', '1234')
  await waitFor(() => expect(save(dialog)).toBeEnabled())
  fireEvent.click(save(dialog))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(puts(fetchMock)[0]).toEqual({
    changes: [
      {
        op: 'addDatatype',
        datatype: {
          name: 'Code',
          pattern: '\\d{4}',
          description: 'Four digits',
          ignoreCase: true,
          examples: ['1234'],
          counterExamples: [],
        },
      },
    ],
  })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  answer(jsonResponse(202, { entryId: 7, datatypes: [] }))

  expect(await screen.findByText('Saving')).toBeInTheDocument()
  expect(await screen.findByRole('cell', { name: 'Code' })).toBeInTheDocument()
  stored = { datatypes: (stored as { datatypes: Datatype[] }).datatypes, saving: false }
  await waitForPolls(() => expect(screen.queryByText('Saving')).not.toBeInTheDocument())
})

test('Edit starts from the datatype and sets it under its id, without the facts', async () => {
  const fetchMock = renderTab()

  fireEvent.click(await screen.findByRole('button', { name: 'Edit PIN' }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit PIN' })
  expect(within(dialog).getByRole('textbox', { name: 'Regular expression' })).toHaveValue('\\d{4,8}')
  expect(within(dialog).getByRole('textbox', { name: 'Examples' })).toHaveValue('1234')
  expect(within(dialog).getByRole('textbox', { name: 'Counter-examples' })).toHaveValue('12a4')

  type(dialog, 'Description', 'Changed')
  await waitFor(() => expect(save(dialog)).toBeEnabled())
  fireEvent.click(save(dialog))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(puts(fetchMock)[0]).toEqual({
    changes: [
      {
        op: 'setDatatype',
        datatype: {
          id: pin.id,
          name: pin.name,
          pattern: pin.pattern,
          description: 'Changed',
          ignoreCase: false,
          examples: pin.examples,
          counterExamples: pin.counterExamples,
        },
      },
    ],
  })
})

const wholeValue = (scope: HTMLElement) => within(scope).getByRole('checkbox', { name: 'Whole value' })
const pattern = (scope: HTMLElement) => within(scope).getByRole('textbox', { name: 'Regular expression' })

test('Whole value is ticked for a new datatype, with a fixed ^ and $ around the pattern', async () => {
  const fetchMock = renderTab()
  const dialog = await openNew()

  expect(wholeValue(dialog)).toBeChecked()
  expect(within(dialog).getByTestId('pattern-start-anchor')).toHaveTextContent('^')
  expect(within(dialog).getByTestId('pattern-end-anchor')).toHaveTextContent('$')
  type(dialog, 'Regular expression', '\\d{4}')
  await waitFor(() => expect(checks(fetchMock).at(-1)?.wholeValue).toBe(true))

  fireEvent.click(wholeValue(dialog))
  expect(within(dialog).queryByTestId('pattern-start-anchor')).not.toBeInTheDocument()
  expect(within(dialog).queryByTestId('pattern-end-anchor')).not.toBeInTheDocument()
  await waitFor(() => expect(checks(fetchMock).at(-1)?.wholeValue).toBe(false))
})

test('typing a ^ first and a $ last ticks Whole value and takes them out', async () => {
  renderTab()
  const dialog = await openNew()
  fireEvent.click(wholeValue(dialog))

  type(dialog, 'Regular expression', '^\\d+')
  expect(pattern(dialog)).toHaveValue('^\\d+')
  expect(wholeValue(dialog)).not.toBeChecked()
  type(dialog, 'Regular expression', '^\\d+$')
  expect(pattern(dialog)).toHaveValue('\\d+')
  expect(wholeValue(dialog)).toBeChecked()

  // Ticked, a ^ or $ alone says nothing more and goes too, but an escaped $ is a literal.
  type(dialog, 'Regular expression', '^\\d+')
  expect(pattern(dialog)).toHaveValue('\\d+')
  type(dialog, 'Regular expression', '\\d+\\$')
  expect(pattern(dialog)).toHaveValue('\\d+\\$')
  expect(wholeValue(dialog)).toBeChecked()
})

test('the caret stays where the user types when a typed ^ goes', async () => {
  renderTab()
  const dialog = await openNew()
  type(dialog, 'Regular expression', 'ab')

  const input = pattern(dialog) as HTMLInputElement
  // Past React's own setter, so the change event sees a new value with the caret after the ^.
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '^ab')
  input.setSelectionRange(1, 1)
  fireEvent.change(input)
  expect(input).toHaveValue('ab')
  expect(input.selectionStart).toBe(0)
})

test('the typing rules keep a lone anchor unticked and know an escaped $', () => {
  expect(typedPattern('^ab', false)).toEqual({ pattern: '^ab', wholeValue: false, removedAtStart: 0 })
  expect(typedPattern('ab$', false)).toEqual({ pattern: 'ab$', wholeValue: false, removedAtStart: 0 })
  expect(typedPattern('^ab\\$', false)).toEqual({ pattern: '^ab\\$', wholeValue: false, removedAtStart: 0 })
  expect(typedPattern('^ab\\\\$', false)).toEqual({ pattern: 'ab\\\\', wholeValue: true, removedAtStart: 1 })
  expect(typedPattern('^ab$', false)).toEqual({ pattern: 'ab', wholeValue: true, removedAtStart: 1 })
  expect(typedPattern('ab$', true)).toEqual({ pattern: 'ab', wholeValue: true, removedAtStart: 0 })
  expect(typedPattern('[^a]', true)).toEqual({ pattern: '[^a]', wholeValue: true, removedAtStart: 0 })
})

test('a pattern stored as ^x$ opens as x with Whole value ticked', async () => {
  const anchored = { ...pin, pattern: '^\\d{4,8}$', wholeValue: false }
  renderTab(queued, () => ({ datatypes: [anchored], saving: false }))

  fireEvent.click(await screen.findByRole('button', { name: 'Edit PIN' }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit PIN' })
  expect(pattern(dialog)).toHaveValue('\\d{4,8}')
  expect(wholeValue(dialog)).toBeChecked()
})

test('Save sends wholeValue false only when Whole value is unticked', async () => {
  const fetchMock = renderTab()
  fireEvent.click(await screen.findByRole('button', { name: 'Edit PIN' }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit PIN' })
  fireEvent.click(wholeValue(dialog))
  await waitFor(() => expect(checks(fetchMock).at(-1)?.wholeValue).toBe(false))
  await waitFor(() => expect(save(dialog)).toBeEnabled())
  fireEvent.click(save(dialog))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  const [change] = (puts(fetchMock)[0] as { changes: { datatype: Record<string, unknown> }[] }).changes
  expect(change.datatype.wholeValue).toBe(false)
})

test('a name another datatype has is refused before any request', async () => {
  const fetchMock = renderTab()
  const dialog = await openNew()

  type(dialog, 'Name', ' pin ')
  type(dialog, 'Regular expression', '\\d{4,8}')
  await within(dialog).findByText('10 states, largest repeat 8, shortest match 4')

  expect(within(dialog).getByText('There is already a datatype named pin.')).toBeInTheDocument()
  expect(save(dialog)).toBeDisabled()
  expect(puts(fetchMock)).toHaveLength(0)
})

test('more examples than the backend takes are refused without a check', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const fetchMock = renderTab()
  const dialog = await openNew()

  type(dialog, 'Name', 'Code')
  type(dialog, 'Regular expression', '\\d{4,8}')
  type(dialog, 'Examples', Array.from({ length: 51 }, () => '1234').join('\n'))

  expect(within(dialog).getByText('At most 50 examples.')).toBeInTheDocument()
  await act(() => vi.advanceTimersByTimeAsync(400))
  expect(checks(fetchMock)).toHaveLength(0)
  expect(save(dialog)).toBeDisabled()
})

test('more Try lines than the backend takes are refused without a check', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const fetchMock = renderTab()
  const dialog = await openNew()

  type(dialog, 'Regular expression', '\\d{4,8}')
  type(dialog, 'Try', Array.from({ length: 51 }, () => '1234').join('\n'))

  expect(within(dialog).getByText('At most 50 lines.')).toBeInTheDocument()
  await act(() => vi.advanceTimersByTimeAsync(400))
  expect(checks(fetchMock)).toHaveLength(0)
})

test('a failing check says so and keeps Save disabled', async () => {
  const fetchMock = renderTab()
  const dialog = await openNew()
  const routed = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (input, init) =>
    String(input) === '/api/datatypes/check' ? jsonResponse(500, { detail: 'the engine is away' }) : routed(input, init),
  )

  type(dialog, 'Name', 'Code')
  type(dialog, 'Regular expression', '\\d{4,8}')

  expect(await within(dialog).findByText('Could not check the pattern: the engine is away')).toBeInTheDocument()
  expect(save(dialog)).toBeDisabled()
})

test('a refused save opens the editor again with the draft and the reason', async () => {
  renderTab(() => jsonResponse(422, { detail: 'datatypes[0] (IBAN): unbalanced parenthesis' }))
  const dialog = await openNew()

  type(dialog, 'Name', 'Code')
  type(dialog, 'Regular expression', '\\d{4,8}')
  await waitFor(() => expect(save(dialog)).toBeEnabled())
  fireEvent.click(save(dialog))

  const reason = await screen.findByText(/Nothing was saved: datatypes\[0\] \(IBAN\)/)
  const reopened = reason.closest<HTMLElement>('[role="dialog"]')!
  expect(within(reopened).getByRole('textbox', { name: 'Name' })).toHaveValue('Code')
  expect(within(reopened).getByRole('textbox', { name: 'Regular expression' })).toHaveValue('\\d{4,8}')
})

test('Delete of a datatype in use is refused with the names of the templates, without a request', async () => {
  const fetchMock = renderTab()
  const table = await screen.findByRole('table', { name: 'Datatypes' })
  await waitFor(() => expect(within(table).getAllByRole('row')[1]).toHaveTextContent('2'))

  fireEvent.click(screen.getByRole('button', { name: 'Delete PIN' }))

  expect(await screen.findByText(/PIN cannot be deleted: it is used by the template Computer\./)).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(puts(fetchMock)).toHaveLength(0)
})

test('Delete of an unused datatype asks first, then puts its removal by id', async () => {
  const fetchMock = renderTab(queued, () => ({ datatypes: [pin, iban], saving: false }))

  fireEvent.click(await screen.findByRole('button', { name: 'Delete IBAN' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete datatype' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))

  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(puts(fetchMock)[0]).toEqual({ changes: [{ op: 'removeDatatype', id: iban.id }] })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
})

test('a refused delete shows the reason on the tab', async () => {
  renderTab(() => jsonResponse(422, { detail: "The datatype 'IBAN' is used by the templates Router" }), () => ({
    datatypes: [pin, iban],
    saving: false,
  }))

  fireEvent.click(await screen.findByRole('button', { name: 'Delete IBAN' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete datatype' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))

  expect(await screen.findByText(/Nothing was saved: The datatype 'IBAN' is used by the templates Router/)).toBeInTheDocument()
})

test('the result text covers two changes, the end of the value and a value too long', () => {
  const two: FormatResult = { distance: 2, score: 0.5, firstEdit: 0, expected: 'a digit', tooLong: false }
  expect(whereItDiffers(two)).toBe('2 changes, the first at position 1, expected a digit')
  expect(whereItDiffers(FULL)).toBe('')
  expect(resultText({ distance: 1, score: 0.8, firstEdit: 4, expected: 'the end of the value', tooLong: false })).toBe(
    'matches format 80%, distance 1: 1 change at position 5, expected the end of the value',
  )
  expect(resultText({ distance: null, score: 0, firstEdit: null, expected: null, tooLong: true })).toBe('too long to check')
})

test('value lines leave out empty lines and keep spaces', () => {
  expect(valueLines(' 12\r\n\n34 \n')).toEqual([' 12', '34 '])
})
