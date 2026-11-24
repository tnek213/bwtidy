import { type QueryClient, useQuery } from '@tanstack/react-query'
import { ApiError, apiFetch } from './api.ts'

// The gateway (docs/spec.md, Gateway) runs one worker per Bitwarden account. The browser
// picks its account here before anything under /api/ answers.

export interface Account {
  server: string
  email: string
}

// What the gateway knows of the browser's account before a worker runs (docs/spec.md, Gateway).
interface GatewayState {
  account: Account | null
  // A worker runs and answers. Only an unlock or a Bitwarden login starts one.
  running: boolean
  exists?: boolean
  pending?: boolean
  // Whether the account's CLI is logged in, as its last worker noted it.
  loggedIn?: boolean
}

// The browser's account. running false: no worker runs, the page shows the unlock or the
// Bitwarden login from loggedIn without asking one. Undefined counts as running.
export interface ReadyAccount {
  kind: 'ready'
  account: Account
  running?: boolean
  loggedIn?: boolean
}

// none: the account form, ready: the account is chosen, busy: switching account,
// single: a backend without a gateway, which needs no account.
export type AccountState = { kind: 'none'; error?: string } | ReadyAccount | { kind: 'busy' } | { kind: 'single' }

export const accountQueryKey = ['gatewayAccount'] as const

// The account in use, opened again by itself when its worker is gone.
export const ACCOUNT_KEY = 'bwtidy.account'

function isAccount(value: unknown): value is Account {
  return (
    typeof value === 'object' &&
    value !== null &&
    'server' in value &&
    'email' in value &&
    typeof value.server === 'string' &&
    typeof value.email === 'string'
  )
}

export function parseAccount(text: string | null): Account | null {
  try {
    const value: unknown = JSON.parse(text ?? 'null')
    return isAccount(value) ? { server: value.server, email: value.email } : null
  } catch {
    return null
  }
}

// localStorage can throw (blocked site data) or hold anything, the app works without it.
export function loadAccount(key: string): Account | null {
  try {
    return parseAccount(localStorage.getItem(key))
  } catch {
    return null
  }
}

export function storeAccount(key: string, account: Account | null) {
  try {
    if (account === null) {
      localStorage.removeItem(key)
    } else {
      localStorage.setItem(key, JSON.stringify({ server: account.server, email: account.email }))
    }
  } catch {
    // Only a convenience, the form is simply empty next time.
  }
}

function rememberedAccount(): Account | null {
  return loadAccount(ACCOUNT_KEY)
}

export function sameAccount(a: Account | null, b: Account | null): boolean {
  return a === b || (a !== null && b !== null && a.server === b.server && a.email === b.email)
}

function readyState(state: GatewayState, account: Account): ReadyAccount {
  return {
    kind: 'ready',
    account: state.account ?? account,
    running: state.running,
    loggedIn: state.loggedIn,
  }
}

// Makes it the browser's account, without starting its worker (an unlock or a Bitwarden login
// does that). A missing account is a 404 with {exists: false} unless create is set, a created
// one gets its directory at its first Bitwarden login.
export async function openAccount(account: Account, create = false): Promise<ReadyAccount> {
  const state = await apiFetch<GatewayState>('/gateway/account', {
    method: 'POST',
    body: { server: account.server, email: account.email, create },
  })
  const opened = readyState(state, account)
  storeAccount(ACCOUNT_KEY, opened.account)
  return opened
}

export function openErrorText(error: unknown): string {
  // 422: the gateway's reason (a server or an email it refuses).
  if (error instanceof ApiError && error.status === 422 && error.detail) {
    return error.detail
  }
  return 'Could not open the account, try again'
}

export async function reopenState(account: Account): Promise<AccountState> {
  try {
    return await openAccount(account)
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 404) {
      storeAccount(ACCOUNT_KEY, null)
      return { kind: 'none' }
    }
    return { kind: 'none', error: openErrorText(caught) }
  }
}

async function resolveAccount(): Promise<AccountState> {
  let state: GatewayState
  try {
    state = await apiFetch<GatewayState>('/gateway/account')
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 404) {
      return { kind: 'single' }
    }
    throw caught
  }
  if (state.account !== null) {
    storeAccount(ACCOUNT_KEY, state.account)
    return readyState(state, state.account)
  }
  // The browser was closed or the gateway restarted: open the remembered account again, so a
  // returning user types only the master password.
  const account = rememberedAccount()
  return account === null ? { kind: 'none' } : await reopenState(account)
}

export function useAccount() {
  return useQuery({
    queryKey: accountQueryKey,
    queryFn: resolveAccount,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  })
}

// Asks the gateway again after an unlock or a login failed: the click may have started the
// worker, or the CLI turned out to be logged out. Changes nothing when that fails.
export async function refreshAccount(queryClient: QueryClient): Promise<void> {
  const current = queryClient.getQueryData<AccountState>(accountQueryKey)
  if (current?.kind !== 'ready') {
    return
  }
  let state: GatewayState
  try {
    state = await apiFetch<GatewayState>('/gateway/account')
  } catch {
    return
  }
  const now = queryClient.getQueryData<AccountState>(accountQueryKey)
  if (now?.kind === 'ready' && sameAccount(state.account, now.account)) {
    queryClient.setQueryData<AccountState>(accountQueryKey, readyState(state, now.account))
  }
}

