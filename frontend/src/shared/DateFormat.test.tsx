import { act, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { formatTime } from './format.ts'
import { jsonResponse, mockFetch, newQueryClient, renderWithQuery } from '../test-utils.tsx'
import { displayQueryKey } from '../api/settings.ts'
import { DateFormatProvider, useFormatTime } from './DateFormat.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

const AT = '2026-11-12T14:05:09Z'

function Probe() {
  const format = useFormatTime()
  return <p data-testid="probe">{format(AT)}</p>
}

test('every page follows the stored format and a new choice at once', async () => {
  mockFetch({ 'GET /api/settings/display': () => jsonResponse(200, { dateFormat: 'iso', hideToolNote: false }) })
  const queryClient = newQueryClient()
  renderWithQuery(
    <DateFormatProvider>
      <Probe />
    </DateFormatProvider>,
    queryClient,
  )
  expect(await screen.findByText(formatTime(AT, 'iso'))).toBeInTheDocument()
  act(() => queryClient.setQueryData(displayQueryKey, { dateFormat: 'europe-dots', hideToolNote: false }))
  expect(await screen.findByText(formatTime(AT, 'europe-dots'))).toBeInTheDocument()
})

test('System when the setting cannot be loaded, and outside the provider', async () => {
  mockFetch({ 'GET /api/settings/display': () => jsonResponse(500, { detail: 'broken' }) })
  renderWithQuery(
    <DateFormatProvider>
      <Probe />
    </DateFormatProvider>,
  )
  expect(screen.getByTestId('probe')).toHaveTextContent(formatTime(AT))
  renderWithQuery(<Probe />)
  for (const probe of screen.getAllByTestId('probe')) {
    expect(probe).toHaveTextContent(formatTime(AT))
  }
})
