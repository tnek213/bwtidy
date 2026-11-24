import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { accountStorageKey } from '../shared/account.ts'

export type QueueTab = 'pending' | 'applied' | 'rejected' | 'failed'

const QUEUE_TABS: readonly QueueTab[] = ['pending', 'applied', 'rejected', 'failed']

// How long a tab must be shown without interruption before its entries count as read.
const READ_AFTER_MS = 4_000

// Per tab, the ids of the entries this browser has shown there, kept per account (ids start
// from 1 in each account).
const SEEN_KEY = 'bwtidy.seenQueueEntries'

type Seen = Record<QueueTab, ReadonlySet<number>>

function emptySeen(): Seen {
  return { pending: new Set(), applied: new Set(), rejected: new Set(), failed: new Set() }
}

function readSeen(queryClient: QueryClient): Seen {
  const seen = emptySeen()
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(accountStorageKey(queryClient, SEEN_KEY)) ?? '{}')
    if (typeof stored !== 'object' || stored === null) {
      return seen
    }
    for (const tab of QUEUE_TABS) {
      const ids: unknown = (stored as Record<string, unknown>)[tab]
      if (Array.isArray(ids)) {
        seen[tab] = new Set(ids.filter((id): id is number => typeof id === 'number'))
      }
    }
  } catch {
    // Storage that is blocked or holds something else counts as nothing seen.
  }
  return seen
}

function writeSeen(queryClient: QueryClient, seen: Seen) {
  try {
    const stored = Object.fromEntries(QUEUE_TABS.map((tab) => [tab, [...seen[tab]]]))
    localStorage.setItem(accountStorageKey(queryClient, SEEN_KEY), JSON.stringify(stored))
  } catch {
    // Without storage the counts start over on the next load, which is harmless.
  }
}

function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden')
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', update)
    return () => document.removeEventListener('visibilitychange', update)
  }, [])
  return visible
}

// Counts per tab the entries this browser has not shown yet. `ids` holds each
// tab's current entry ids, undefined while a list has not loaded. Once the
// shown tab has had the same unread entries on screen for READ_AFTER_MS, all
// its entries count as read. Only the ids still in the tab are kept, so an
// entry that leaves a tab is forgotten there.
export function useUnreadCounts(shown: QueueTab, ids: Record<QueueTab, readonly number[] | undefined>) {
  const queryClient = useQueryClient()
  const [seen, setSeen] = useState<Seen>(() => readSeen(queryClient))
  const visible = useDocumentVisible()
  const latest = useRef(ids)
  useEffect(() => {
    latest.current = ids
  })

  const unread = useCallback(
    (tab: QueueTab) => (ids[tab] ?? []).filter((id) => !seen[tab].has(id)),
    [ids, seen],
  )
  const shownUnread = unread(shown).join(',')

  useEffect(() => {
    if (!visible || shownUnread === '') {
      return
    }
    const timer = setTimeout(() => {
      const current = latest.current[shown]
      if (!current) {
        return
      }
      setSeen((old) => {
        const next = { ...old, [shown]: new Set(current) }
        writeSeen(queryClient, next)
        return next
      })
    }, READ_AFTER_MS)
    return () => clearTimeout(timer)
  }, [queryClient, shown, shownUnread, visible])

  return Object.fromEntries(QUEUE_TABS.map((tab) => [tab, unread(tab).length])) as Record<QueueTab, number>
}
