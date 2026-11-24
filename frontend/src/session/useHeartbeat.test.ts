import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { jsonResponse, mockFetch } from '../test-utils.tsx'
import { useHeartbeat } from './useHeartbeat.ts'

let fetchMock: ReturnType<typeof mockFetch>

beforeEach(() => {
  vi.useFakeTimers()
  fetchMock = mockFetch({ 'POST /api/session/heartbeat': () => jsonResponse(204) })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

test('posts one heartbeat per 30 s while unlocked', async () => {
  renderHook(() => useHeartbeat(true))

  await vi.advanceTimersByTimeAsync(29_999)
  expect(fetchMock).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/session/heartbeat',
    expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
  )
  await vi.advanceTimersByTimeAsync(60_000)
  expect(fetchMock).toHaveBeenCalledTimes(3)
})

test('posts nothing while locked', async () => {
  renderHook(() => useHeartbeat(false))
  await vi.advanceTimersByTimeAsync(120_000)
  expect(fetchMock).not.toHaveBeenCalled()
})

test('stops when the session locks or the tab closes', async () => {
  const { rerender, unmount } = renderHook(({ unlocked }) => useHeartbeat(unlocked), {
    initialProps: { unlocked: true },
  })
  await vi.advanceTimersByTimeAsync(30_000)
  expect(fetchMock).toHaveBeenCalledTimes(1)

  rerender({ unlocked: false })
  await vi.advanceTimersByTimeAsync(90_000)
  expect(fetchMock).toHaveBeenCalledTimes(1)

  rerender({ unlocked: true })
  await vi.advanceTimersByTimeAsync(30_000)
  expect(fetchMock).toHaveBeenCalledTimes(2)

  unmount()
  await vi.advanceTimersByTimeAsync(90_000)
  expect(fetchMock).toHaveBeenCalledTimes(2)
})
