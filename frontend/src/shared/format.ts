import type { DateFormat } from '../api/settings.ts'

export const DATE_FORMATS: readonly { value: DateFormat; label: string }[] = [
  { value: 'system', label: 'System' },
  { value: 'iso', label: 'ISO 8601' },
  { value: 'europe', label: 'Europe' },
  { value: 'europe-dots', label: 'Europe (dots)' },
  { value: 'usa', label: 'USA' },
]

// The date's fields in the time zone, the browser's when none is given.
function fieldsOf(date: Date, timeZone: string | undefined) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  const field = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? ''
  return {
    year: field('year'),
    month: field('month'),
    day: field('day'),
    hour: field('hour'),
    minute: field('minute'),
    second: field('second'),
  }
}

function unpadded(digits: string): string {
  return String(Number(digits))
}

// A moment in the format, in the browser's time zone unless the tests give one.
export function formatDate(date: Date, format: DateFormat, timeZone?: string): string {
  if (format === 'system') {
    return date.toLocaleString(undefined, { timeZone })
  }
  const { year, month, day, hour, minute, second } = fieldsOf(date, timeZone)
  const time = `${hour}:${minute}:${second}`
  switch (format) {
    case 'iso':
      return `${year}-${month}-${day} ${time}`
    case 'europe':
      return `${day}/${month}/${year} ${time}`
    case 'europe-dots':
      return `${day}.${month}.${year} ${time}`
    case 'usa': {
      const hours = Number(hour)
      const hour12 = hours % 12 === 0 ? 12 : hours % 12
      const half = hours < 12 ? 'AM' : 'PM'
      return `${unpadded(month)}/${unpadded(day)}/${year}, ${hour12}:${minute}:${second} ${half}`
    }
  }
}

// A time from the backend in the format, the text itself when it is no time, nothing for no time.
// Pages take the chosen format from useFormatTime (shared/DateFormat.tsx).
export function formatTime(at: string | null, format: DateFormat = 'system', timeZone?: string): string {
  if (!at) {
    return ''
  }
  const date = new Date(at)
  return Number.isNaN(date.getTime()) ? at : formatDate(date, format, timeZone)
}

// The date-fns pattern of a date field (shared/DateField.tsx), the date part of formatDate's format.
export function datePattern(format: DateFormat, locale?: string): string {
  switch (format) {
    case 'iso':
      return 'yyyy-MM-dd'
    case 'europe':
      return 'dd/MM/yyyy'
    case 'europe-dots':
      return 'dd.MM.yyyy'
    case 'usa':
      return 'M/d/yyyy'
    case 'system':
      return localeDatePattern(locale)
  }
}

// The locale's date order and separators as toLocaleString shows them. A day and month below 10 tell
// whether the locale pads them. A part date-fns has no plain token for (an era, another calendar's
// year) gives ISO 8601.
function localeDatePattern(locale: string | undefined): string {
  const parts = new Intl.DateTimeFormat(locale).formatToParts(new Date(2001, 1, 3))
  let pattern = ''
  for (const part of parts) {
    if (part.type === 'year' && part.value === '2001') {
      pattern += 'yyyy'
    } else if (part.type === 'month' && /^0?2$/.test(part.value)) {
      pattern += part.value.length === 2 ? 'MM' : 'M'
    } else if (part.type === 'day' && /^0?3$/.test(part.value)) {
      pattern += part.value.length === 2 ? 'dd' : 'd'
    } else if (part.type === 'literal' && !/[a-zA-Z']/.test(part.value)) {
      pattern += part.value
    } else {
      return 'yyyy-MM-dd'
    }
  }
  return pattern
}
