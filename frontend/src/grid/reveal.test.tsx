import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { jsonResponse, mockFetch } from '../test-utils.tsx'
import { REVEAL_MS, useRevealedSecret } from './reveal.ts'

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

test('a revealed value is hidden again after the delay', async () => {
  mockFetch({
    'GET /api/items/item-1/secret?column=login.password': () => jsonResponse(200, { value: 'hunter2' }),
  })
  const { result } = renderHook(() => useRevealedSecret('item-1', 'login.password'))
  expect(result.current.value).toBeNull()

  await act(() => result.current.toggle())
  expect(result.current.value === 'hunter2').toBe(true)

  await act(() => vi.advanceTimersByTimeAsync(REVEAL_MS - 1))
  expect(result.current.value === 'hunter2').toBe(true)

  await act(() => vi.advanceTimersByTimeAsync(1))
  expect(result.current.value).toBeNull()
})

test('a failed fetch is marked and a retry clears the mark', async () => {
  let status = 404
  mockFetch({
    'GET /api/items/item-1/secret?column=login.password': () =>
      status === 200 ? jsonResponse(200, { value: 'hunter2' }) : jsonResponse(status, { detail: 'No such secret' }),
  })
  const { result } = renderHook(() => useRevealedSecret('item-1', 'login.password'))

  await act(() => result.current.toggle())
  expect(result.current.failed).toBe(true)
  expect(result.current.value).toBeNull()

  status = 200
  await act(() => result.current.toggle())
  expect(result.current.failed).toBe(false)
  expect(result.current.value === 'hunter2').toBe(true)
})

test('toggling a revealed value hides it at once', async () => {
  mockFetch({
    'GET /api/items/item-1/secret?column=login.password': () => jsonResponse(200, { value: 'hunter2' }),
  })
  const { result } = renderHook(() => useRevealedSecret('item-1', 'login.password'))
  await act(() => result.current.toggle())
  await act(() => result.current.toggle())
  expect(result.current.value).toBeNull()
})

function pendingSecret() {
  let answer: (response: Response) => void = () => {}
  mockFetch({
    'GET /api/items/item-1/secret?column=login.password': () =>
      new Promise<Response>((resolve) => {
        answer = resolve
      }),
  })
  return { answer: () => answer(jsonResponse(200, { value: 'hunter2' })) }
}

test.each([
  ['item', { itemId: 'item-2', column: 'login.password' }],
  ['column', { itemId: 'item-1', column: 'login.totp' }],
])('an answer pending while the %s changed is dropped', async (_name, next) => {
  const pending = pendingSecret()
  const { result, rerender } = renderHook((p) => useRevealedSecret(p.itemId, p.column), {
    initialProps: { itemId: 'item-1', column: 'login.password' },
  })
  let done: Promise<void> = Promise.resolve()
  act(() => {
    done = result.current.toggle()
  })
  expect(result.current.loading).toBe(true)

  rerender(next)
  await act(async () => {
    pending.answer()
    await done
  })
  expect(result.current.value).toBeNull()
  expect(result.current.loading).toBe(false)
})

test('hiding while the answer is pending drops it', async () => {
  const pending = pendingSecret()
  const { result } = renderHook(() => useRevealedSecret('item-1', 'login.password'))
  let done: Promise<void> = Promise.resolve()
  act(() => {
    done = result.current.toggle()
  })

  act(() => result.current.hide())
  await act(async () => {
    pending.answer()
    await done
  })
  expect(result.current.value).toBeNull()
  expect(result.current.loading).toBe(false)
})
