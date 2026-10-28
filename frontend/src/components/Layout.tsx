import { Link, Navigate, Outlet, useLocation } from 'react-router'

import { useAuth } from '../api/auth-context'
import { Button, Loading } from './ui'

/** Every page after login: the top bar, then the page. */
export function Layout() {
  const { operator, checking, logout } = useAuth()
  const location = useLocation()

  if (checking) return <Loading label="Checking your login…" />
  if (!operator) return <Navigate to="/login" replace state={{ from: location.pathname }} />

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3">
          <Link to="/" className="text-lg font-semibold text-slate-900 dark:text-slate-50">
            AgentIQ
          </Link>
          <div className="flex items-center gap-3 text-sm">
            <span className="hidden text-slate-600 sm:inline dark:text-slate-400">{operator.full_name}</span>
            <Button variant="secondary" onClick={() => void logout()}>
              Log out
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
