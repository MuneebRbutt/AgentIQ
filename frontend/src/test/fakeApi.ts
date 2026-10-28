// A stand-in for the AgentIQ API in component tests: routes are "METHOD /path" with
// `:name` segments, answered from plain functions, and every call is recorded.
import { vi } from 'vitest'

export type Call = { method: string; path: string; query: string; body: unknown; headers: Headers }
type Reply = { status?: number; body?: unknown }
type Handler = (call: Call, params: Record<string, string>) => Reply | unknown

/** Wraps a handler result that needs a status other than 200. */
export function reply(status: number, body?: unknown): Reply & { [REPLY]: true } {
  return { status, body, [REPLY]: true }
}
const REPLY = Symbol('reply')

function matcher(route: string) {
  const [method, pattern] = route.split(' ')
  const names: string[] = []
  const source = pattern.replace(/:(\w+)/g, (_, name: string) => {
    names.push(name)
    return '([^/]+)'
  })
  const regex = new RegExp(`^${source}$`)
  return (call: Call) => {
    if (call.method !== method) return null
    const match = regex.exec(call.path)
    return match ? Object.fromEntries(names.map((name, index) => [name, match[index + 1]])) : null
  }
}

export function fakeApi(routes: Record<string, Handler>) {
  const calls: Call[] = []
  const table = Object.entries(routes).map(([route, handler]) => ({ match: matcher(route), handler }))

  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      const url = new URL(request.url)
      const text = request.headers.get('content-type')?.includes('json') ? await request.text() : ''
      const call: Call = { method: request.method, path: url.pathname, query: url.search.slice(1), body: text ? JSON.parse(text) : undefined, headers: request.headers }
      calls.push(call)
      for (const { match, handler } of table) {
        const params = match(call)
        if (!params) continue
        const result = handler(call, params)
        const { status, body } =
          typeof result === 'object' && result !== null && REPLY in result ? (result as Reply) : { status: 200, body: result }
        return new Response(status === 204 || body === undefined ? null : JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ title: 'Not Found', status: 404, detail: `No fake for ${call.method} ${call.path}` }), {
        status: 404,
        headers: { 'Content-Type': 'application/problem+json' },
      })
    }),
  )
  return calls
}
