import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'

import { AuthProvider } from '../api/auth'
import { tokenStore } from '../api/client'
import { AppRoutes } from '../App'

export const OPERATOR = {
  id: 'op-1',
  tenant_id: 'tenant-1',
  email: 'sara@grand.test',
  full_name: 'Sara Khan',
  is_active: true,
  last_login_at: null,
}

/** The whole dashboard at `path`, logged in unless `loggedIn` is false. */
export function renderApp(path: string, { loggedIn = true } = {}) {
  tokenStore.set(loggedIn ? 'test-token' : null)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const user = userEvent.setup()
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { user, ...view }
}
