import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import {
  type Account,
  ACCOUNT_KEY,
  type AccountState,
  accountQueryKey,
  parseAccount,
  reopenState,
  sameAccount,
  storeAccount,
} from '../api/account.ts'
import { ApiError, apiFetch } from '../api/api.ts'

export const DEFAULT_SERVER = 'https://vault.bitwarden.com'

// What the gateway answers 401 with when the browser has no account or its worker exited.
export const NO_ACCOUNT = 'No account'

// What a 502 says when the worker did not take the request (the gateway's answer) or is on its
// way out (the worker's answer to a login).
const WORKER_GONE = "The account's worker does not answer"

// A localStorage key for something whose ids belong to one account (each account has its own
// database, so ids start from 1 in every one). Without a ready account (the single-user
// backend) it is the key itself.
export function accountStorageKey(queryClient: QueryClient, key: string): string {
  const state = queryClient.getQueryData<AccountState>(accountQueryKey)
  return state?.kind === 'ready' ? `${key}:${state.account.server}:${state.account.email}` : key
}

// Written with the time on every Log out, so the other tabs of the account reload.
const LOGGED_OUT_KEY = 'bwtidy.loggedOut'

// The remembered account, or undefined when localStorage cannot be read at all.
function readableRememberedAccount(): Account | null | undefined {
  let text: string | null
  try {
    text = localStorage.getItem(ACCOUNT_KEY)
  } catch {
    return undefined
  }
  return parseAccount(text)
}

let reloadPage = () => window.location.reload()

// Tests cannot spy on window.location.reload, so they swap it here.
export function setReloadPage(reload: () => void) {
  reloadPage = reload
}

// The account this tab works with, null on the form or while it switches. Undefined for the
// single-user backend, which has no account to change.
function tabAccount(queryClient: QueryClient): Account | null | undefined {
  const state = queryClient.getQueryData<AccountState>(accountQueryKey)
  if (state?.kind === 'single') {
    return undefined
  }
  return state?.kind === 'ready' ? state.account : null
}

// All tabs of a browser profile share the routing cookie. A tab that kept its old account
// after another tab switched would send the old account's ids to the new account's worker,
// so it reloads. A tab never gets the storage events of its own writes.
export function useOtherTabs() {
  const queryClient = useQueryClient()
  useEffect(() => {
    function handleStorage(event: StorageEvent) {
      if (event.storageArea !== localStorage) {
        return
      }
      const mine = tabAccount(queryClient)
      if (mine === undefined) {
        return
      }
      // A null key is a localStorage.clear(), which forgets the account too.
      if (event.key === ACCOUNT_KEY || event.key === null) {
        if (!sameAccount(mine, parseAccount(event.newValue))) {
          reloadPage()
        }
      } else if (event.key === LOGGED_OUT_KEY && event.newValue !== null && mine !== null) {
        reloadPage()
      }
    }
    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [queryClient])
}

// Tells the other tabs that this one logged out, once its account runs again.
export function announceLogout() {
  try {
    localStorage.setItem(LOGGED_OUT_KEY, String(Date.now()))
  } catch {
    // The other tabs lock on their next request instead.
  }
}

// The server's host when it is not the default one, for showing the account.
export function accountLabel(account: Account): string {
  let host: string
  try {
    host = new URL(account.server).host
  } catch {
    host = account.server
  }
  return host === new URL(DEFAULT_SERVER).host ? account.email : `${account.email} (${host})`
}

// A switch of account makes every reopen that started before it moot.
let generation = 0

let reopening: Promise<boolean> | null = null

// Opens the current account again after its worker exited or the gateway forgot the routing
// cookie, and learns what the gateway knows of it now. Answers whether the account is ready
// again. Calls at the same time share one request.
export function reopenAccount(queryClient: QueryClient): Promise<boolean> {
  if (reopening !== null) {
    return reopening
  }
  const current = queryClient.getQueryData<AccountState>(accountQueryKey)
  if (current?.kind !== 'ready') {
    return Promise.resolve(false)
  }
  // Another tab switched account. Opening the old one again would route the shared cookie
  // back to it in the middle of that switch.
  const remembered = readableRememberedAccount()
  if (remembered !== undefined && !sameAccount(remembered, current.account)) {
    reloadPage()
    return Promise.resolve(false)
  }
  const started = generation
  reopening = reopenState(current.account)
    .then((next) => {
      if (generation !== started) {
        return false
      }
      queryClient.setQueryData(accountQueryKey, next)
      return next.kind === 'ready'
    })
    .finally(() => {
      reopening = null
    })
  return reopening
}

export function workerGone(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    ((error.status === 401 && error.detail === NO_ACCOUNT) || (error.status === 502 && error.detail === WORKER_GONE))
  )
}

// Runs a call that needs the account's worker. The unlock and the start of a Bitwarden login
// start it themselves (the gateway does), so this only opens the account again once when the
// call found no worker: the gateway forgot the routing cookie, or the worker was on its way out.
export async function withWorker<T>(queryClient: QueryClient, call: () => Promise<T>): Promise<T> {
  try {
    return await call()
  } catch (caught) {
    if (!workerGone(caught) || !(await reopenAccount(queryClient))) {
      throw caught
    }
    return await call()
  }
}

// Ends the session (when one runs), forgets the browser's account and shows the account form.
export async function switchAccount(queryClient: QueryClient, endSession: boolean) {
  generation += 1
  queryClient.setQueryData<AccountState>(accountQueryKey, { kind: 'busy' })
  if (endSession) {
    try {
      await apiFetch('/api/session', { method: 'DELETE' })
    } catch {
      // The session ends without heartbeats anyway.
    }
  }
  try {
    await apiFetch('/gateway/account', { method: 'DELETE' })
  } catch {
    // The form opens another account, which replaces the routing cookie anyway.
  }
  storeAccount(ACCOUNT_KEY, null)
  showAccount(queryClient, { kind: 'none' })
}

// A new account state with nothing of the old account's session or vault kept.
export function showAccount(queryClient: QueryClient, state: AccountState) {
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== accountQueryKey[0] })
  queryClient.setQueryData<AccountState>(accountQueryKey, state)
}
