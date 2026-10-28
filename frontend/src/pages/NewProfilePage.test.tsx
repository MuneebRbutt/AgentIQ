// TC-08 steps 1 and 2: an operator creates a profile from the dashboard, and a profile with
// a missing mandatory field is blocked with the field highlighted (FR-08-01/05, UC-08).
import { screen, within } from '@testing-library/react'

import { fakeApi, reply, type Call } from '../test/fakeApi'
import restaurant from '../test/fixtures/restaurant.json'
import { OPERATOR, renderApp } from '../test/renderApp'

const CREATED = {
  id: 'p9',
  name: restaurant.name,
  domain: 'restaurant',
  version: 1,
  created_at: '2026-10-20T09:00:00Z',
  updated_at: '2026-10-20T09:00:00Z',
  config: restaurant,
}

function routes(validate: (call: Call) => unknown) {
  return fakeApi({
    'GET /api/v1/auth/me': () => OPERATOR,
    'GET /api/v1/profiles/templates': () => [{ domain: 'restaurant', name: restaurant.name }],
    'GET /api/v1/profiles/templates/:domain': () => restaurant,
    'POST /api/v1/profiles/validate': validate,
    'POST /api/v1/profiles': () => reply(201, CREATED),
    'GET /api/v1/profiles/p9': () => CREATED,
    'GET /api/v1/profiles': () => [],
  })
}

describe('creating a business profile (TC-08)', () => {
  it('step 1: creates a profile from a template and opens it', async () => {
    const calls = routes(() => ({ valid: true, errors: [] }))
    const { user } = renderApp('/profiles/new')

    await user.click(await screen.findByRole('button', { name: 'Use this template' }))
    expect(await screen.findByLabelText('Business name')).toHaveValue('Spice Garden')
    await user.clear(screen.getByLabelText('Business name'))
    await user.type(screen.getByLabelText('Business name'), 'Spice Garden Gulberg')
    await user.click(screen.getByRole('button', { name: 'Create profile' }))

    expect(await screen.findByRole('heading', { name: 'Spice Garden' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute('aria-current', 'page')
    const created = calls.find((call) => call.method === 'POST' && call.path === '/api/v1/profiles')
    expect(created?.body).toMatchObject({ name: 'Spice Garden Gulberg', domain: 'restaurant', services: restaurant.services })
  })

  it('step 2: blocks a profile without a business type and highlights the field', async () => {
    const calls = routes(() => ({
      valid: false,
      errors: [
        { loc: ['domain'], msg: 'Field required', type: 'missing' },
        { loc: ['services'], msg: 'List should have at least 1 item after validation, not 0', type: 'too_short' },
      ],
    }))
    const { user } = renderApp('/profiles/new')

    await user.click(await screen.findByRole('button', { name: 'Start blank' }))
    await user.type(await screen.findByLabelText('Business name'), 'Grand Hotel')
    await user.click(screen.getByRole('button', { name: 'Create profile' }))

    expect(await screen.findByText('Fix the highlighted fields, then save again.')).toBeInTheDocument()
    const domain = screen.getByLabelText('Type of business')
    expect(domain).toHaveAttribute('aria-invalid', 'true')
    expect(domain).toHaveAccessibleDescription(/This is required\./)
    expect(screen.getByText('2 problems to fix')).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: /sections/i })
    expect(within(nav).getByRole('link', { name: /Business/ })).toHaveTextContent('1')
    expect(calls.some((call) => call.method === 'POST' && call.path === '/api/v1/profiles')).toBe(false)
  })
})
