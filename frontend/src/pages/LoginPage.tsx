import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router'

import { useAuth } from '../api/auth-context'
import { Button, Field, Notice } from '../components/ui'
import { errorText } from '../lib/status'

export function LoginPage() {
  const { operator, notice, login } = useAuth()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (operator) {
    const from = (location.state as { from?: string } | null)?.from ?? '/'
    return <Navigate to={from} replace />
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email, password)
    } catch (caught) {
      // The API gives one message for every failure (FR-01-03); show it as it is.
      setError(errorText(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900"
      >
        <div>
          <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-50">AgentIQ</h1>
          <p className="text-sm text-slate-600 dark:text-slate-400">Log in to manage your business.</p>
        </div>
        {notice && !error && <Notice tone="info">{notice}</Notice>}
        {error && <Notice>{error}</Notice>}
        <Field label="Email">
          {(props) => (
            <input {...props} type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          )}
        </Field>
        <Field label="Password">
          {(props) => (
            <input
              {...props}
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
        </Field>
        <Button type="submit" disabled={busy} className="w-full">
          {busy ? 'Logging in…' : 'Log in'}
        </Button>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Accounts are created by your administrator with <code>python -m app.cli create-operator</code>.
        </p>
      </form>
    </main>
  )
}
