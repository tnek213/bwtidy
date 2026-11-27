import { screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { QueueEntry } from '../api/queue.ts'
import { jsonResponse, mockFetch, renderWithQuery } from '../test-utils.tsx'
import QueueButton from './QueueButton.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

function entry(id: number, source: string): QueueEntry {
  return {
    id,
    createdAt: '2026-10-01T10:00:00Z',
    source,
    summary: `Entry ${id}`,
    status: 'pending',
    decidedAt: null,
    operation: {},
    diff: [],
    diffError: null,
    result: null,
  }
}

function renderButton(pending: QueueEntry[]) {
  mockFetch({ 'GET /api/queue?status=pending': () => jsonResponse(200, pending) })
  renderWithQuery(<QueueButton current={false} onClick={() => {}} />)
}

test('an empty queue has no badge, so the name holds all the text', async () => {
  renderButton([])
  const button = await screen.findByRole('button', { name: 'Queue, nothing pending' })
  expect(button).toHaveTextContent(/^Queue$/)
})

test('with both kinds the name holds the badge number, the sum', async () => {
  renderButton([entry(1, 'ui'), entry(2, 'mcp'), entry(3, 'mcp')])
  const button = await screen.findByRole('button', { name: 'Queue, 3 pending: 1 unsaved edits, 2 MCP proposals to review' })
  expect(button).toHaveTextContent('Queue 3')
})

test('with one kind the name is its count', async () => {
  renderButton([entry(2, 'mcp'), entry(3, 'mcp')])
  expect(await screen.findByRole('button', { name: 'Queue, 2 MCP proposals to review' })).toHaveTextContent('Queue 2')
})
