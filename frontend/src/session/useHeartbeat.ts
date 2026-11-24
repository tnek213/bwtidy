import { useEffect } from 'react'
import { heartbeat } from '../api/session.ts'

const HEARTBEAT_INTERVAL_MS = 30_000

// The backend ends the session after 120 s without a heartbeat. Browsers may
// throttle timers in hidden tabs to once a minute, which still fits.
export function useHeartbeat(unlocked: boolean) {
  useEffect(() => {
    if (!unlocked) {
      return
    }
    const timer = setInterval(() => {
      // A 401 already switches the app to locked through apiFetch, and a
      // network error is retried by the next tick.
      heartbeat().catch(() => {})
    }, HEARTBEAT_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [unlocked])
}
