import { screen } from '@testing-library/react'

import { tokenStore } from '../api/client'
import { fakeApi, reply } from '../test/fakeApi'
import { OPERATOR, renderApp } from '../test/renderApp'

const PROFILES = [{ id: 'p1', name: 'Spice Garden', domain: 'restaurant', version: 1, updated_at: '2026-10-19T09:00:00Z' }]

describe('logging in (FR-01, TC-01 on the dashboard)', () => {
  it('sends a logged-out visitor to the login page and shows the API refusal as it is', async () => {
    fakeApi({
      'POST /api/v1/auth/login': () => reply(401, { title: 'Unauthorized', status: 401, detail: 'Invalid email or password.' }),
    })
    const { user } = renderApp('/', { loggedIn: false })

    await user.type(await screen.findByLabelText('Email'), 'sara@grand.test')
    await user.type(screen.getByLabelText('Password'), 'wrong-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.')
    expect(tokenStore.get()).toBeNull()
  })

  it('opens the profile list after a good login and sends the token with later calls', async () => {
    const calls = fakeApi({
      'POST /api/v1/auth/login': () => ({ access_token: 'tok-1', expires_at: '2026-10-20T10:00:00Z', operator: OPERATOR }),
      'GET /api/v1/profiles': () => PROFILES,
      'GET /api/v1/health': () => ({ status: 'ok', version: '0.1.0', checks: { database: 'ok', redis: 'ok' } }),
    })
    const { user } = renderApp('/', { loggedIn: false })

    await user.type(await screen.findByLabelText('Email'), 'sara@grand.test')
    await user.type(screen.getByLabelText('Password'), 'correct-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByRole('link', { name: /Spice Garden/ })).toBeInTheDocument()
    expect(screen.getByText('Sara Khan')).toBeInTheDocument()
    const list = calls.find((call) => call.path === '/api/v1/profiles')
    expect(list?.headers.get('authorization')).toBe('Bearer tok-1')
  })

  it('returns to the login page with a notice when the session ends', async () => {
    fakeApi({
      'GET /api/v1/auth/me': () => OPERATOR,
      'GET /api/v1/profiles': () => reply(401, { title: 'Unauthorized', status: 401, detail: 'Session expired' }),
      'GET /api/v1/health': () => ({ status: 'ok', version: '0.1.0', checks: {} }),
    })
    renderApp('/')

    expect(await screen.findByText('Your session has ended. Please log in again.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Log in' })).toBeInTheDocument()
  })
})
