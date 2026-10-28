import { render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import { HealthStatus } from './HealthStatus'

function mockFetch(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('HealthStatus', () => {
  it('shows a loading message first', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))

    render(<HealthStatus />)

    expect(screen.getByRole('status')).toHaveTextContent('Checking services')
  })

  it('lists every dependency when the backend is healthy', async () => {
    const fetchMock = mockFetch(200, {
      status: 'ok',
      version: '0.1.0',
      checks: { database: 'ok', redis: 'ok' },
    })

    render(<HealthStatus />)

    expect(await screen.findByText(/PostgreSQL/)).toHaveTextContent('PostgreSQL: ok')
    expect(screen.getByText(/Redis/)).toHaveTextContent('Redis: ok')
    const [request] = fetchMock.mock.calls[0] as [Request]
    expect(request.url).toMatch(/\/api\/v1\/health$/)
  })

  it('shows which dependency is down when the backend is degraded (503)', async () => {
    mockFetch(503, {
      status: 'degraded',
      version: '0.1.0',
      checks: { database: 'unavailable', redis: 'ok' },
    })

    render(<HealthStatus />)

    expect(await screen.findByText(/PostgreSQL/)).toHaveTextContent('PostgreSQL: unavailable')
    expect(screen.getByText(/Backend API/)).toHaveTextContent('degraded')
  })

  it('shows an alert when the backend cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    render(<HealthStatus />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Failed to fetch')
  })

  it('shows an alert for an unexpected response shape', async () => {
    mockFetch(200, { api: 'ok' })

    render(<HealthStatus />)

    expect(await screen.findByRole('alert')).toHaveTextContent('unexpected response')
  })
})
