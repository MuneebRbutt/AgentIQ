import { useEffect, useState } from 'react'

import { fetchHealth, type HealthResponse } from '../api/health'

const CHECK_LABELS: Record<string, string> = {
  database: 'PostgreSQL',
  redis: 'Redis',
}

type State =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'loaded'; health: HealthResponse }

export function HealthStatus() {
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()

    fetchHealth(controller.signal)
      .then((health) => setState({ kind: 'loaded', health }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        const message = error instanceof Error ? error.message : 'Unknown error'
        setState({ kind: 'error', message })
      })

    return () => controller.abort()
  }, [])

  if (state.kind === 'loading') {
    return <p role="status">Checking services…</p>
  }

  if (state.kind === 'error') {
    return (
      <p role="alert">
        Backend API unreachable: {state.message}. Is <code>fastapi dev</code> running?
      </p>
    )
  }

  const { health } = state
  return (
    <section aria-label="Service status">
      <p>
        Backend API: <strong>{health.status}</strong> (v{health.version})
      </p>
      <ul>
        {Object.entries(health.checks).map(([name, status]) => (
          <li key={name} data-status={status}>
            {CHECK_LABELS[name] ?? name}: <strong>{status}</strong>
          </li>
        ))}
      </ul>
    </section>
  )
}
