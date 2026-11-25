export class ApiError extends Error {
  readonly status: number
  readonly detail: string | undefined
  // The parsed JSON error body, for routes that answer with more than a detail.
  readonly body: unknown

  constructor(status: number, detail: string | undefined, body?: unknown) {
    super(detail ?? `Request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.body = body
  }
}

// detail is the 401's detail, the gateway's "No account" when the account's worker is gone.
type LockedListener = (detail: string | undefined) => void

const lockedListeners = new Set<LockedListener>()

// The app subscribes here to switch to the login page whenever the backend
// answers 401, so a session that timed out shows up on the next request.
export function onLocked(listener: LockedListener): () => void {
  lockedListeners.add(listener)
  return () => {
    lockedListeners.delete(listener)
  }
}

async function readBody(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    // Not JSON, the status alone has to do.
    return undefined
  }
}

function detailOf(body: unknown): string | undefined {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const { detail } = body
    return typeof detail === 'string' ? detail : undefined
  }
  return undefined
}

interface ApiRequest {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  // A request made on a timer, which the backend does not count as user activity.
  background?: boolean
  headers?: Record<string, string>
}

export async function apiFetch<T = void>(path: string, request: ApiRequest = {}): Promise<T> {
  const response = await send(path, request)
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

// Like apiFetch, for a route that also answers in a response header.
export async function apiFetchWithHeaders<T>(path: string, request: ApiRequest = {}): Promise<{ body: T; headers: Headers }> {
  const response = await send(path, request)
  return { body: (await response.json()) as T, headers: response.headers }
}

async function send(path: string, request: ApiRequest): Promise<Response> {
  const { method = 'GET', body, background = false } = request
  const headers: Record<string, string> = { ...request.headers, Accept: 'application/json' }
  if (background) {
    headers['X-Bwtidy-Background'] = '1'
  }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }
  const response = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    const body = await readBody(response)
    if (response.status === 401) {
      for (const listener of lockedListeners) {
        listener(detailOf(body))
      }
    }
    throw new ApiError(response.status, detailOf(body), body)
  }
  return response
}
