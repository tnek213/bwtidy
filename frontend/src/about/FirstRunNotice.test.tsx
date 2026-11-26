import { screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { jsonResponse, mockFetch, renderWithQuery } from '../test-utils.tsx'
import FirstRunNotice from './FirstRunNotice.tsx'
import { CAUTION_URL, WORKS_FOR_ME_URL } from './links.ts'

afterEach(() => {
  vi.unstubAllGlobals()
})

const NO_ROUTE = { account: null, running: false, exists: false, pending: false, loggedIn: false }

function expectNewTab(link: HTMLElement, href: string) {
  expect(link).toHaveAttribute('href', href)
  expect(link).toHaveAttribute('target', '_blank')
  expect(link).toHaveAttribute('rel', 'noopener noreferrer')
}

test('shows the notice with its two links while no account ever logged in', async () => {
  mockFetch({ 'GET /gateway/account': () => jsonResponse(200, { ...NO_ROUTE, firstRun: true }) })
  renderWithQuery(<FirstRunNotice />)

  const notice = await screen.findByRole('note', { name: 'Before you start' })
  expect(notice).toHaveTextContent(
    'bwtidy is beta software made with AI, with very few users so far. Read the caution on GitHub before you use ' +
      'it on your vault. If bwtidy works for you, a thumbs up in Works for me on GitHub helps others decide.',
  )
  expectNewTab(screen.getByRole('link', { name: 'the caution on GitHub' }), CAUTION_URL)
  expectNewTab(screen.getByRole('link', { name: 'Works for me' }), WORKS_FOR_ME_URL)
  // a note, not an alert: nothing went wrong
  expect(screen.queryByRole('alert')).toBeNull()
})

test.each([
  ['an account logged in', () => jsonResponse(200, { ...NO_ROUTE, firstRun: false })],
  ['an older gateway', () => jsonResponse(200, NO_ROUTE)],
  ['a backend without the gateway', () => jsonResponse(404, { detail: 'Not Found' })],
  ['a gateway that fails', () => jsonResponse(500, { detail: 'boom' })],
])('shows nothing after %s', async (_, route) => {
  const fetchMock = mockFetch({ 'GET /gateway/account': route })
  const { container } = renderWithQuery(<FirstRunNotice />)

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/gateway/account', expect.anything()))
  await waitFor(() => expect(container).toBeEmptyDOMElement())
  expect(screen.queryByRole('note')).toBeNull()
})
