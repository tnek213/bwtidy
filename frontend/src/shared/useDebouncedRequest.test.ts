import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { useDebouncedRequest } from './useDebouncedRequest.ts'

const DELAY_MS = 250

// One request per call, answered or failed by the test when it chooses.
function requests() {
  const sent: { key: string | null; answer: (value: string) => void; fail: (error: Error) => void }[] = []
  const run = (key: string | null) =>
    new Promise<string>((answer, fail) => {
      sent.push({ key, answer, fail })
    })
  return { sent, run }
}

function renderRequest(run: (key: string | null) => Promise<string>, key: string | null) {
  return renderHook(({ key }) => useDebouncedRequest(key, () => run(key), DELAY_MS), { initialProps: { key } })
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

test('asks once the delay has passed and answers for its key', async () => {
  const { sent, run } = requests()
  const { result } = renderRequest(run, 'a')
  expect(result.current).toEqual({ state: 'pending', forKey: null })

  await act(() => vi.advanceTimersByTimeAsync(DELAY_MS - 1))
  expect(sent).toHaveLength(0)
  await act(() => vi.advanceTimersByTimeAsync(1))
  expect(sent.map((request) => request.key)).toEqual(['a'])

  await act(async () => sent[0].answer('answer a'))
  expect(result.current).toEqual({ state: 'answered', forKey: 'a', value: 'answer a' })
})

test('a key change before the delay cancels the first run', async () => {
  const { sent, run } = requests()
  const { result, rerender } = renderRequest(run, 'a')
  await act(() => vi.advanceTimersByTimeAsync(DELAY_MS - 1))
  rerender({ key: 'b' })
  await act(() => vi.advanceTimersByTimeAsync(DELAY_MS - 1))
  expect(sent).toHaveLength(0)

  await act(() => vi.advanceTimersByTimeAsync(1))
  expect(sent.map((request) => request.key)).toEqual(['b'])
  await act(async () => sent[0].answer('answer b'))
  expect(result.current).toEqual({ state: 'answered', forKey: 'b', value: 'answer b' })
})

test('a late answer for an old key is dropped', async () => {
  const { sent, run } = requests()
  const { result, rerender } = renderRequest(run, 'a')
  await act(() => vi.advanceTimersByTimeAsync(DELAY_MS))
  rerender({ key: 'b' })
  await act(() => vi.advanceTimersByTimeAsync(DELAY_MS))
  expect(sent.map((request) => request.key)).toEqual(['a', 'b'])

  await act(async () => sent[0].answer('answer a'))
  expect(result.current).toEqual({ state: 'pending', forKey: null })
  await act(async () => sent[1].answer('answer b'))
  expect(result.current).toEqual({ state: 'answered', forKey: 'b', value: 'answer b' })
})

test('a failure is failed, and the last answer or failure stays while a new key is pending', async () => {
  const { sent, run } = requests()
  const { result, rerender } = renderRequest(run, 'a')
  await act(() => vi.advanceTimersByTimeAsync(DELAY_MS))
  await act(async () => sent[0].answer('answer a'))

  rerender({ key: 'b' })
  expect(result.current).toEqual({ state: 'pending', forKey: 'a', value: 'answer a' })
  await act(() => vi.advanceTimersByTimeAsync(DELAY_MS))
  const error = new Error('refused')
  await act(async () => sent[1].fail(error))
  expect(result.current).toEqual({ state: 'failed', forKey: 'b', error })

  rerender({ key: 'c' })
  expect(result.current).toEqual({ state: 'pending', forKey: 'b', error })
  // Back to a key the last answer is for: that answer counts again.
  rerender({ key: 'b' })
  expect(result.current).toEqual({ state: 'failed', forKey: 'b', error })
})

test('a null key is idle and asks nothing, and unmount cancels the wait', async () => {
  const { sent, run } = requests()
  const { result, rerender, unmount } = renderRequest(run, null)
  expect(result.current).toEqual({ state: 'idle', forKey: null })
  await act(() => vi.advanceTimersByTimeAsync(DELAY_MS * 4))
  expect(sent).toHaveLength(0)

  rerender({ key: 'a' })
  rerender({ key: null })
  await act(() => vi.advanceTimersByTimeAsync(DELAY_MS * 4))
  expect(sent).toHaveLength(0)

  rerender({ key: 'a' })
  unmount()
  await act(() => vi.advanceTimersByTimeAsync(DELAY_MS * 4))
  expect(sent).toHaveLength(0)
})
