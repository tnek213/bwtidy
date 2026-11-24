import { type QueryClient, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api.ts'
import { type AccountState, type ReadyAccount, accountQueryKey } from './account.ts'

export interface BackupReencryption {
  done: number
  failed: string[]
}

// Whether the Bitwarden CLI in the container is logged in, and the server and
// email to prefill the CLI login with.
export interface BwLogin {
  loggedIn: boolean
  server: string
  email: string
  // Only from a worker, whose server and email cannot be changed.
  accountFixed?: boolean
}

export interface Session {
  state: 'locked' | 'unlocked'
  mcpEnabled: boolean
  // Whether an MCP token exists. The token itself is only in the answer that made it.
  mcpToken: boolean
  bwLogin: BwLogin
  // Only after a login that re-encrypted the backups with a changed master password.
  backupReencryption?: BackupReencryption
}

export const sessionQueryKey = ['session'] as const

const defaultBwLogin: BwLogin = { loggedIn: true, server: 'https://vault.bitwarden.com', email: '' }

export const lockedSession: Session = {
  state: 'locked',
  mcpEnabled: false,
  mcpToken: false,
  bwLogin: defaultBwLogin,
}

// The locked session of an account whose worker does not run, from what the gateway knows.
function gatewaySession(state: ReadyAccount): Session {
  return {
    ...lockedSession,
    bwLogin: {
      loggedIn: state.loggedIn ?? true,
      server: state.account.server,
      email: state.account.email,
      accountFixed: true,
    },
  }
}

// Asking a worker that does not run would only answer 401, and nothing starts one before an
// unlock or a Bitwarden login.
function fetchSession(queryClient: QueryClient): Promise<Session> {
  const state = queryClient.getQueryData<AccountState>(accountQueryKey)
  if (state?.kind === 'ready' && state.running === false) {
    return Promise.resolve(gatewaySession(state))
  }
  return apiFetch<Session>('/api/session')
}

export function useSession() {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: sessionQueryKey,
    queryFn: () => fetchSession(queryClient),
  })
}

export function login(password: string): Promise<Session> {
  return apiFetch<Session>('/api/session', { method: 'POST', body: { password } })
}

export function logout(): Promise<void> {
  return apiFetch('/api/session', { method: 'DELETE' })
}

export function setMcpEnabled(enabled: boolean): Promise<{ mcpEnabled: boolean }> {
  return apiFetch<{ mcpEnabled: boolean }>('/api/mcp', { method: 'POST', body: { enabled } })
}

export interface NewMcpToken {
  token: string
  createdAt: string
  // null for a token without an expiry
  expiresAt: string | null
}

// What the backend tells of the token, never the token itself.
export interface McpTokenInfo {
  createdAt: string
  expiresAt: string | null
  lastUsedAt: string | null
}

export const mcpTokenQueryKey = ['mcpToken'] as const

// A new token replaces the old one. This answer is the only place the token
// ever shows. days null makes a token without an expiry.
export function createMcpToken(days: number | null): Promise<NewMcpToken> {
  return apiFetch<NewMcpToken>('/api/mcp/token', { method: 'POST', body: { days } })
}

export function useMcpTokenInfo(exists: boolean) {
  return useQuery({
    queryKey: mcpTokenQueryKey,
    queryFn: () => apiFetch<McpTokenInfo>('/api/mcp/token'),
    enabled: exists,
  })
}

export function removeMcpToken(): Promise<void> {
  return apiFetch('/api/mcp/token', { method: 'DELETE' })
}

export function heartbeat(): Promise<void> {
  return apiFetch('/api/session/heartbeat', { method: 'POST' })
}

// Ends the session and logs the CLI out too, so the next login has to log it in
// again. It needs the session (401 without one) and this header, which another
// site's page cannot send without a CORS preflight.
export function bwLogout(): Promise<void> {
  return apiFetch('/api/bw-logout', { method: 'POST', headers: { 'X-Bwtidy-Request': '1' } })
}

interface BwLoginStart {
  server: string
  email: string
  password: string
}

interface BwLoginOutput {
  text: string
  next: number
  running: boolean
  exitCode: number | null
}

function loginHeaders(loginId: string): Record<string, string> {
  return { 'X-Bwtidy-Login': loginId }
}

// Starts `bw login` in the container. The answer is the token for every later call of this login.
export async function startBwLogin(start: BwLoginStart): Promise<string> {
  const { loginId } = await apiFetch<{ loginId: string }>('/api/bw-login', { method: 'POST', body: start })
  return loginId
}

export function bwLoginOutput(loginId: string, after: number): Promise<BwLoginOutput> {
  return apiFetch<BwLoginOutput>(`/api/bw-login/output?after=${after}`, {
    headers: loginHeaders(loginId),
    background: true,
  })
}

// Either a line of text for the CLI, or the master password kept by the backend.
export type BwLoginInput = { text: string } | { password: true }

export function sendBwLoginInput(loginId: string, input: BwLoginInput): Promise<void> {
  return apiFetch('/api/bw-login/input', { method: 'POST', body: input, headers: loginHeaders(loginId) })
}

export function cancelBwLogin(loginId: string): Promise<void> {
  return apiFetch('/api/bw-login', { method: 'DELETE', headers: loginHeaders(loginId) })
}

// Unlocks with the password the login kept, answers like login().
export function finishBwLogin(loginId: string): Promise<Session> {
  return apiFetch<Session>('/api/bw-login/finish', { method: 'POST', headers: loginHeaders(loginId) })
}

