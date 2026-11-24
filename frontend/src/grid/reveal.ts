import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchSecret } from '../api/grid.ts'

export const REVEAL_MS = 30_000

// A secret is fetched only on request and kept only in this hook's state,
// never in the query cache. A timer hides it after REVEAL_MS and unmounting
// drops it. An answer is dropped when inView says false at arrival, and when
// the request went stale: hide, another toggle or a new item or column came
// after it started.
export function useRevealedSecret(itemId: string, column: string, inView: () => boolean = () => true) {
  const [value, setValue] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [loading, setLoading] = useState(false)
  const mounted = useRef(true)
  const token = useRef(0)

  useEffect(() => {
    token.current += 1
  }, [itemId, column])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  useEffect(() => {
    if (value === null) {
      return
    }
    const timer = setTimeout(() => setValue(null), REVEAL_MS)
    return () => clearTimeout(timer)
  }, [value])

  const hide = useCallback(() => {
    token.current += 1
    setValue(null)
  }, [])

  async function toggle() {
    if (value !== null) {
      hide()
      return
    }
    token.current += 1
    const mine = token.current
    setLoading(true)
    setFailed(false)
    try {
      const secret = await fetchSecret(itemId, column)
      if (mounted.current && token.current === mine && inView()) {
        setValue(secret)
      }
    } catch {
      if (mounted.current) {
        setFailed(true)
      }
    } finally {
      if (mounted.current) {
        setLoading(false)
      }
    }
  }

  return { value, failed, loading, toggle, hide }
}
