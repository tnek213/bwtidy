import { screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, expect, test, vi } from 'vitest'
import type { DateFormat } from '../api/settings.ts'
import { datePattern } from './format.ts'
import { clearDay, jsonResponse, mockFetch, renderWithQuery, typeDay } from '../test-utils.tsx'
import DateField from './DateField.tsx'
import { DateFormatProvider } from './DateFormat.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

// The field as a page holds it, so what it reports comes back as its value.
function Holder({ initial, onChange }: { initial: string; onChange: (value: string) => void }) {
  const [value, setValue] = useState(initial)
  return (
    <DateField
      label="From"
      value={value}
      onChange={(next) => {
        onChange(next)
        setValue(next)
      }}
    />
  )
}

async function renderField(format: DateFormat, initial = '') {
  mockFetch({
    'GET /api/settings/display': () => jsonResponse(200, { dateFormat: format, hideToolNote: false, rareFieldsMax: 0 }),
  })
  const onChange = vi.fn()
  renderWithQuery(
    <DateFormatProvider>
      <Holder initial={initial} onChange={onChange} />
    </DateFormatProvider>,
  )
  const placeholder = { iso: 'YYYY-MM-DD', europe: 'DD/MM/YYYY', 'europe-dots': 'DD.MM.YYYY', usa: 'MM/DD/YYYY' }
  if (format !== 'system') {
    // The format has loaded once the field shows its sections in it.
    await waitFor(() => expect(shown()).toBe(initial ? expected[format] : placeholder[format]))
  }
  return onChange
}

// What the field shows, without the invisible marks that keep unpadded sections apart.
function shown(): string {
  const sections = screen.getByRole('group', { name: 'From' }).querySelector('.MuiPickersSectionList-root')
  return (sections?.textContent ?? '').replace(/‎/g, '')
}

const expected: Record<Exclude<DateFormat, 'system'>, string> = {
  iso: '2026-11-16',
  europe: '16/11/2026',
  'europe-dots': '16.11.2026',
  usa: '11/16/2026',
}

test.each(Object.entries(expected))('%s shows the day as %s', async (format, text) => {
  await renderField(format as DateFormat, '2026-11-16')
  expect(shown()).toBe(text)
})

test('USA shows a month and day below 10 without a leading zero', async () => {
  mockFetch({
    'GET /api/settings/display': () => jsonResponse(200, { dateFormat: 'usa', hideToolNote: false, rareFieldsMax: 0 }),
  })
  renderWithQuery(
    <DateFormatProvider>
      <Holder initial="2026-03-05" onChange={() => {}} />
    </DateFormatProvider>,
  )
  await waitFor(() => expect(shown()).toBe('3/5/2026'))
})

test('System shows the day in the order and separators of the locale', async () => {
  await renderField('system', '2026-11-16')
  const text = new Date(2026, 10, 16).toLocaleDateString()
  await waitFor(() => expect(shown()).toBe(text))
})

test('the date part of the locale is the pattern of System', () => {
  expect(datePattern('system', 'en-US')).toBe('M/d/yyyy')
  expect(datePattern('system', 'en-GB')).toBe('dd/MM/yyyy')
  expect(datePattern('system', 'de-DE')).toBe('d.M.yyyy')
  expect(datePattern('system', 'sv-SE')).toBe('yyyy-MM-dd')
})

test.each(Object.keys(expected))('a day typed in %s reaches the page as YYYY-MM-DD', async (format) => {
  const onChange = await renderField(format as DateFormat)
  typeDay('From', '2026-11-16')
  expect(onChange).toHaveBeenLastCalledWith('2026-11-16')
  expect(shown()).toBe(expected[format as Exclude<DateFormat, 'system'>])
})

test('a month and day below 10 typed in USA', async () => {
  const onChange = await renderField('usa')
  typeDay('From', '2026-03-05')
  expect(onChange).toHaveBeenLastCalledWith('2026-03-05')
  expect(shown()).toBe('3/5/2026')
})

test('a half typed day does not change the value', async () => {
  const onChange = await renderField('iso', '2026-11-16')
  clearDay('From')
  expect(onChange).toHaveBeenLastCalledWith('')
  onChange.mockClear()
  typeDay('From', '2026-11')
  expect(onChange).not.toHaveBeenCalled()
  expect(shown()).toBe('2026-11-DD')
})

test('clearing the field gives no day', async () => {
  const onChange = await renderField('europe', '2026-11-16')
  clearDay('From')
  expect(onChange).toHaveBeenLastCalledWith('')
  expect(shown()).toBe('DD/MM/YYYY')
})
