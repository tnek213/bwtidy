import type { QueryClient } from '@tanstack/react-query'
import { type Account, ACCOUNT_KEY, type AccountState, accountQueryKey, loadAccount, type ReadyAccount, storeAccount } from '../api/account.ts'
import { DEFAULT_SERVER } from '../shared/account.ts'

// What the form was filled with last, to prefill it.
const FORM_KEY = 'bwtidy.accountForm'

export function formValues(): Account {
  return loadAccount(FORM_KEY) ?? { server: DEFAULT_SERVER, email: '' }
}

export function saveFormValues(account: Account) {
  storeAccount(FORM_KEY, account)
}

// What the browser remembers about the account to log in to: the last values typed into a form,
// else the account in use. Null when it knows nothing.
export function browserAccount(): Account | null {
  return loadAccount(FORM_KEY) ?? loadAccount(ACCOUNT_KEY)
}

// Whether the account's worker runs, as far as the page knows. Without one the next unlock or
// Bitwarden login waits for its start.
export function workerRunning(queryClient: QueryClient): boolean {
  const state = queryClient.getQueryData<AccountState>(accountQueryKey)
  return state?.kind !== 'ready' || state.running !== false
}

// Updates what the page knows of the worker after an unlock or a login reached it.
export function noteWorkerRunning(queryClient: QueryClient) {
  const state = queryClient.getQueryData<AccountState>(accountQueryKey)
  if (state?.kind === 'ready' && state.running === false) {
    const running: ReadyAccount = { ...state, running: true }
    queryClient.setQueryData<AccountState>(accountQueryKey, running)
  }
}
