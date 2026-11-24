import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactElement } from 'react'
import { vi } from 'vitest'

export function newQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

// A secret input is masked with the CSS -webkit-text-security. jsdom does not know the property,
// so it keeps React's WebkitTextSecurity only as a plain field of the style object, never in
// cssText or the style attribute: 'disc' while the value is hidden, '' when it is shown.
export function textSecurity(element: HTMLElement): string {
  return (element.style as unknown as Record<string, string | undefined>).WebkitTextSecurity ?? ''
}

export function renderWithQuery(ui: ReactElement, queryClient = newQueryClient()) {
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

export function jsonResponse(status: number, body?: unknown, headers: Record<string, string> = {}): Response {
  if (body === undefined) {
    return new Response(null, { status, headers })
  }
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

// A route may hold its answer back, to test what the page shows meanwhile.
type Route = (init: RequestInit) => Response | Promise<Response>

// A backend without the gateway, so the app goes straight to the session. Tests of the
// account form route GET /gateway/account themselves.
const noGateway: Record<string, Route> = {
  'GET /gateway/account': () => jsonResponse(404, { detail: 'Not Found' }),
}

// The display settings, which the app loads once unlocked. Tests of them route it themselves.
const systemDisplay: Record<string, Route> = {
  'GET /api/settings/display': () => jsonResponse(200, { dateFormat: 'system', hideToolNote: false, rareFieldsMax: 0 }),
}

// Mocks fetch with one handler per "METHOD path". Unknown routes fail the test.
export function mockFetch(routes: Record<string, Route>) {
  const all = { ...noGateway, ...systemDisplay, ...routes }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const key = `${init.method ?? 'GET'} ${String(input)}`
    const route = all[key]
    if (!route) {
      throw new Error(`Unexpected fetch: ${key}`)
    }
    return route(init)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

// Waits for the assertion under vi.useFakeTimers, moving the fake clock a step at a time, so the
// polls and delays it needs fire at once instead of in real time. Answers what the assertion
// returns. It fails like waitFor once the clock has moved by the timeout without a pass.
export async function waitForPolls<T>(assertion: () => T, { step = 100, timeout = 60_000 } = {}): Promise<T> {
  for (let waited = 0; ; waited += step) {
    try {
      return assertion()
    } catch (error) {
      if (waited >= timeout) {
        throw error
      }
    }
    await act(() => vi.advanceTimersByTimeAsync(step))
  }
}

// Types a day (YYYY-MM-DD) into a date field (shared/DateField.tsx) key by key, a section at a time,
// whatever the order of its format. Leaving out the end types part of a day ('2026-11').
export function typeDay(label: string, day: string) {
  const [year, month, date] = day.split('-')
  const typed: Record<string, string | undefined> = { Year: year, Month: month, Day: date }
  const field = within(screen.getByRole('group', { name: label }))
  for (const section of field.getAllByRole('spinbutton')) {
    const digits = typed[section.getAttribute('aria-label') ?? '']
    if (digits === undefined) {
      continue
    }
    act(() => section.focus())
    fireEvent.click(section)
    for (const digit of digits) {
      const active = document.activeElement as HTMLElement
      active.textContent = digit
      fireEvent.input(active)
    }
  }
}

// Empties a date field with its clear button.
export function clearDay(label: string) {
  const field = within(screen.getByRole('group', { name: label }))
  fireEvent.click(field.getByRole('button', { name: 'Clear' }))
}
