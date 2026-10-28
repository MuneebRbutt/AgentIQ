import { api, unwrap, type Schemas } from './client'

export type HealthResponse = Schemas['HealthResponse']

// A degraded backend answers 503 with a normal body, so both 200 and 503 are readable.
export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const { data, error, response } = await api.GET('/api/v1/health', { signal })
  const body = response.status === 503 && error ? (error as unknown) : await unwrap(Promise.resolve({ data, error, response }))
  if (!isHealthResponse(body)) throw new Error('unexpected response from /api/v1/health')
  return body
}

// Guards against something else answering on the API's port (a stale dev server, a proxy).
function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return typeof candidate.status === 'string' && typeof candidate.checks === 'object' && candidate.checks !== null
}
