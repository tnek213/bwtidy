import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { AutoCommitContext, type AutoCommitState, useAutoCommit } from '../commit/autoCommit.ts'
import type { QueueEntry } from '../api/queue.ts'
import { jsonResponse, mockFetch, newQueryClient } from '../test-utils.tsx'
import { QueryClientProvider } from '@tanstack/react-query'
import WriteAnnouncer, { announcement, SAVED_TEXT, SAVING_TEXT } from './WriteAnnouncer.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

test('a write that starts and ends is said, and so are new proposals', () => {
  expect(announcement({ committing: false, proposals: 0 }, { committing: true, proposals: 0 })).toBe(SAVING_TEXT)
  expect(announcement({ committing: true, proposals: 0 }, { committing: false, proposals: 0 })).toBe(SAVED_TEXT)
  expect(announcement({ committing: false, proposals: 1 }, { committing: false, proposals: 2 })).toBe(
    '1 new MCP proposal to review.',
  )
  expect(announcement({ committing: true, proposals: 0 }, { committing: false, proposals: 3 })).toBe(
    `${SAVED_TEXT} 3 new MCP proposals to review.`,
  )
})

test('nothing new is said: fewer proposals, the first list, no change', () => {
  expect(announcement({ committing: false, proposals: 2 }, { committing: false, proposals: 1 })).toBeNull()
  expect(announcement({ committing: false, proposals: null }, { committing: false, proposals: 4 })).toBeNull()
  expect(announcement({ committing: true, proposals: 1 }, { committing: true, proposals: 1 })).toBeNull()
})

function proposal(id: number): QueueEntry {
  return {
    id,
    createdAt: '2026-10-01T10:00:00Z',
    source: 'mcp',
    summary: `Rename ${id}`,
    status: 'pending',
    decidedAt: null,
    operation: {},
    diff: [],
    diffError: null,
    result: null,
  }
}

function Committing({ committing }: { committing: boolean }) {
  const state: AutoCommitState = { ...useAutoCommit(), committing }
  return (
    <AutoCommitContext value={state}>
      <WriteAnnouncer />
    </AutoCommitContext>
  )
}

test('the live region says the end of a write', async () => {
  mockFetch({ 'GET /api/queue?status=pending': () => jsonResponse(200, [proposal(1)]) })
  const queryClient = newQueryClient()
  const { rerender } = render(
    <QueryClientProvider client={queryClient}>
      <Committing committing />
    </QueryClientProvider>,
  )
  const region = screen.getByRole('status')
  expect(region).toHaveTextContent('')
  rerender(
    <QueryClientProvider client={queryClient}>
      <Committing committing={false} />
    </QueryClientProvider>,
  )
  await waitFor(() => expect(region).toHaveTextContent(SAVED_TEXT))
})
