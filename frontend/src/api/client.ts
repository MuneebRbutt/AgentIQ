// The typed API client. Types come from docs/api/openapi.json (`npm run gen:api`), so a
// backend change that breaks the dashboard fails `npm run typecheck` (CLAUDE.md §4).
import createClient, { type Middleware } from 'openapi-fetch'

import type { components, paths } from './schema'

export type Schemas = components['schemas']

export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

const TOKEN_KEY = 'agentiq.token'

// Kept for the browser tab only (sessionStorage), so closing the tab logs the operator out.
export const tokenStore = {
  get(): string | null {
    try {
      return sessionStorage.getItem(TOKEN_KEY)
    } catch {
      return null
    }
  },
  set(token: string | null): void {
    try {
      if (token) sessionStorage.setItem(TOKEN_KEY, token)
      else sessionStorage.removeItem(TOKEN_KEY)
    } catch {
      // Storage blocked: the login lasts until the page reloads.
    }
  },
}

type Listener = () => void
const sessionEndedListeners = new Set<Listener>()

/** Called when the API says the login has ended (expired, revoked or deactivated). */
export function onSessionEnded(listener: Listener): () => void {
  sessionEndedListeners.add(listener)
  return () => sessionEndedListeners.delete(listener)
}

const auth: Middleware = {
  onRequest({ request }) {
    const token = tokenStore.get()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    return request
  },
  onResponse({ request, response }) {
    if (response.status === 401 && !request.url.endsWith('/auth/login')) {
      tokenStore.set(null)
      sessionEndedListeners.forEach((listener) => listener())
    }
    return response
  },
}

export const api = createClient<paths>({
  baseUrl: API_URL,
  // Looked up on every call, so tests can replace `fetch`.
  fetch: (request) => globalThis.fetch(request),
})
api.use(auth)

/** One problem in a submitted form, as the API reports it (RFC 9457 + field errors). */
export type FieldError = { loc: (string | number)[]; msg: string; type: string }

export class ApiError extends Error {
  readonly status: number
  readonly errors: FieldError[]

  constructor(status: number, message: string, errors: FieldError[] = []) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.errors = errors
  }
}

type Problem = { detail?: unknown; errors?: unknown }

function toApiError(response: Response, body: unknown): ApiError {
  const problem = (typeof body === 'object' && body !== null ? body : {}) as Problem
  const detail = typeof problem.detail === 'string' ? problem.detail : undefined
  const errors = Array.isArray(problem.errors) ? (problem.errors as FieldError[]) : []
  return new ApiError(response.status, detail ?? fallbackMessage(response.status), errors)
}

function fallbackMessage(status: number): string {
  if (status === 0) return 'The AgentIQ server could not be reached. Is the API running?'
  if (status >= 500) return 'Something went wrong on the server. Please try again.'
  return `The request failed (HTTP ${status}).`
}

type Result<T> = { data?: T; error?: unknown; response: Response }

/** Unwraps an openapi-fetch result: the data, or an `ApiError` with a readable message. */
export async function unwrap<T>(call: Promise<Result<T>>): Promise<T> {
  let result: Result<T>
  try {
    result = await call
  } catch {
    throw new ApiError(0, fallbackMessage(0))
  }
  if (!result.response.ok) throw toApiError(result.response, result.error)
  return result.data as T
}

/** File uploads are multipart, which the generated client doesn't build for us. */
export async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const token = tokenStore.get()
  let response: Response
  try {
    response = await globalThis.fetch(`${API_URL}${path}`, {
      method: 'POST',
      body: form,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
  } catch {
    throw new ApiError(0, fallbackMessage(0))
  }
  const body: unknown = await response.json().catch(() => undefined)
  if (response.status === 401) {
    tokenStore.set(null)
    sessionEndedListeners.forEach((listener) => listener())
  }
  if (!response.ok) throw toApiError(response, body)
  return body as T
}
