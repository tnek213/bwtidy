import { useEffect, useEffectEvent, useState } from 'react'

type Answered<T> = { forKey: string; value: T }
type Failed = { forKey: string; error: unknown }
// The last answer or failure and the key it is for.
type Settled<T> = { forKey: null } | Answered<T> | Failed

// state is about the current key. While it is idle or pending, forKey and its value or error are
// of the last request that settled, an earlier one, which a caller shows or ignores.
type DebouncedRequest<T> =
  | ({ state: 'idle' | 'pending' } & Settled<T>)
  | ({ state: 'answered' } & Answered<T>)
  | ({ state: 'failed' } & Failed)

// Asks the backend about what is typed once the typing pauses for delayMs. The key says what
// the request is about: a null key asks nothing, a changed key restarts the wait, and an answer
// for an earlier key is dropped. Not through the query cache: what is typed can be a secret, so
// it must be in no query key.
export function useDebouncedRequest<T>(key: string | null, run: () => Promise<T>, delayMs: number): DebouncedRequest<T> {
  const [settled, setSettled] = useState<Settled<T>>({ forKey: null })
  // The run of the latest render, which asks about the same thing as the key it renders with.
  const request = useEffectEvent(run)
  useEffect(() => {
    if (key === null) {
      return
    }
    let current = true
    const timer = setTimeout(() => {
      request().then(
        (value) => {
          if (current) {
            setSettled({ forKey: key, value })
          }
        },
        (error: unknown) => {
          if (current) {
            setSettled({ forKey: key, error })
          }
        },
      )
    }, delayMs)
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [key, delayMs])
  return withState(key, settled)
}

function withState<T>(key: string | null, settled: Settled<T>): DebouncedRequest<T> {
  if (key === null) {
    return { state: 'idle', ...settled }
  }
  if (settled.forKey === null || settled.forKey !== key) {
    return { state: 'pending', ...settled }
  }
  return 'error' in settled ? { state: 'failed', ...settled } : { state: 'answered', ...settled }
}
