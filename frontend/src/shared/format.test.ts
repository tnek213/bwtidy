import { expect, test } from 'vitest'
import { DATE_FORMATS, formatDate, formatTime } from './format.ts'

// Times in UTC, so the test does not depend on the machine's time zone.
const AFTERNOON = '2026-11-12T14:05:09Z'
const MORNING = '2026-01-05T00:07:03Z'

test('each format of an afternoon', () => {
  expect(formatTime(AFTERNOON, 'iso', 'UTC')).toBe('2026-11-12 14:05:09')
  expect(formatTime(AFTERNOON, 'europe', 'UTC')).toBe('12/11/2026 14:05:09')
  expect(formatTime(AFTERNOON, 'europe-dots', 'UTC')).toBe('12.11.2026 14:05:09')
  expect(formatTime(AFTERNOON, 'usa', 'UTC')).toBe('11/12/2026, 2:05:09 PM')
})

test('each format just after midnight, with single digit days and months', () => {
  expect(formatTime(MORNING, 'iso', 'UTC')).toBe('2026-01-05 00:07:03')
  expect(formatTime(MORNING, 'europe', 'UTC')).toBe('05/01/2026 00:07:03')
  expect(formatTime(MORNING, 'europe-dots', 'UTC')).toBe('05.01.2026 00:07:03')
  expect(formatTime(MORNING, 'usa', 'UTC')).toBe('1/5/2026, 12:07:03 AM')
  expect(formatTime('2026-01-05T12:00:00Z', 'usa', 'UTC')).toBe('1/5/2026, 12:00:00 PM')
})

test('System is the browser locale, which is also the default', () => {
  const expected = new Date(AFTERNOON).toLocaleString(undefined, { timeZone: 'UTC' })
  expect(formatTime(AFTERNOON, 'system', 'UTC')).toBe(expected)
  expect(formatTime(AFTERNOON)).toBe(new Date(AFTERNOON).toLocaleString())
})

test('the time zone moves the fields', () => {
  expect(formatTime(AFTERNOON, 'iso', 'Asia/Tokyo')).toBe('2026-11-12 23:05:09')
  expect(formatTime(AFTERNOON, 'iso', 'America/New_York')).toBe('2026-11-12 09:05:09')
})

test('no time is nothing, a text that is no time is shown as it is', () => {
  for (const { value } of DATE_FORMATS) {
    expect(formatTime(null, value)).toBe('')
    expect(formatTime('', value)).toBe('')
    expect(formatTime('soon', value)).toBe('soon')
  }
})

test('every format has a label', () => {
  expect(DATE_FORMATS.map(({ label }) => label)).toEqual(['System', 'ISO 8601', 'Europe', 'Europe (dots)', 'USA'])
  expect(formatDate(new Date(AFTERNOON), 'iso', 'UTC')).toBe('2026-11-12 14:05:09')
})
