import type { QueryClient } from '@tanstack/react-query'
import { accountQueryKey } from '../api/account.ts'
import { type BwLogin, lockedSession, type Session, sessionQueryKey } from '../api/session.ts'

// A note the page shows above the unlock form. It is the page's own, never from
// the backend, and showLocked drops it with the other queries.
export const unlockNoteQueryKey = ['unlockNote'] as const

// Drops every cached vault query so nothing from the old session stays in
// memory, then shows the login page. The CLI's login state is kept from the
// last answer unless the caller knows better. The account stays.
export function showLocked(queryClient: QueryClient, bwLogin?: Partial<BwLogin>) {
  const kept: unknown[] = [sessionQueryKey[0], accountQueryKey[0]]
  queryClient.removeQueries({ predicate: (query) => !kept.includes(query.queryKey[0]) })
  const previous = queryClient.getQueryData<Session>(sessionQueryKey)?.bwLogin ?? lockedSession.bwLogin
  queryClient.setQueryData<Session>(sessionQueryKey, { ...lockedSession, bwLogin: { ...previous, ...bwLogin } })
}
