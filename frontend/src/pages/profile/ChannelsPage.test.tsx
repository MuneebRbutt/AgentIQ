// Connecting Twilio numbers and email addresses to a profile (FR-02-01/02/03, ADR-0010, ADR-0011).
import { screen } from '@testing-library/react'

import { fakeApi, reply } from '../../test/fakeApi'
import restaurant from '../../test/fixtures/restaurant.json'
import { OPERATOR, renderApp } from '../../test/renderApp'

const PROFILE = {
  id: 'p1',
  name: 'Spice Garden',
  domain: 'restaurant',
  version: 3,
  created_at: '2026-10-01T09:00:00Z',
  updated_at: '2026-10-19T09:00:00Z',
  config: restaurant,
}
const SETUP = {
  incoming_webhook_url: 'https://agentiq.example.com/api/v1/voice/incoming',
  status_callback_url: 'https://agentiq.example.com/api/v1/voice/status',
  sms_webhook_url: 'https://agentiq.example.com/api/v1/sms/incoming',
  signatures_checked: true,
}
const NO_EMAIL = { addresses: [], inbound_enabled: true, mailbox_address: 'agentiq.demo@gmail.com' }

describe('channels', () => {
  it('adds a number and shows what to paste into Twilio', async () => {
    const numbers: { id: string; number: string; created_at: string }[] = []
    const calls = fakeApi({
      'GET /api/v1/auth/me': () => OPERATOR,
      'GET /api/v1/profiles/p1': () => PROFILE,
      'GET /api/v1/profiles/p1/phone-numbers': () => ({ ...SETUP, numbers }),
      'GET /api/v1/profiles/p1/email-addresses': () => NO_EMAIL,
      'POST /api/v1/profiles/p1/phone-numbers': () => {
        const added = {
          id: 'n1',
          number: '+14155550123',
          created_at: '2026-10-21T09:00:00Z',
        }
        numbers.push(added)
        return reply(201, added)
      },
    })
    const { user } = renderApp('/profiles/p1/channels')

    expect(await screen.findByText('No numbers yet.')).toBeInTheDocument()
    expect(screen.getByText(SETUP.incoming_webhook_url)).toBeInTheDocument()
    expect(screen.getByText(SETUP.sms_webhook_url)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Twilio number'), '+1 415 555 0123')
    await user.click(screen.getByRole('button', { name: 'Add number' }))

    expect(await screen.findByText('+14155550123')).toBeInTheDocument()
    expect(calls.find((call) => call.method === 'POST')?.body).toEqual({
      number: '+1 415 555 0123',
    })
    expect(screen.getByRole('button', { name: 'Remove +14155550123' })).toBeInTheDocument()
  })

  it('explains a refused number and warns when calls cannot be verified', async () => {
    fakeApi({
      'GET /api/v1/auth/me': () => OPERATOR,
      'GET /api/v1/profiles/p1': () => PROFILE,
      'GET /api/v1/profiles/p1/phone-numbers': () => ({
        ...SETUP,
        signatures_checked: false,
        numbers: [],
      }),
      'GET /api/v1/profiles/p1/email-addresses': () => NO_EMAIL,
      'POST /api/v1/profiles/p1/phone-numbers': () =>
        reply(409, {
          title: 'Conflict',
          status: 409,
          detail: 'That number is already connected to a business profile.',
        }),
    })
    const { user } = renderApp('/profiles/p1/channels')

    expect(await screen.findByText('Calls and messages are refused for now')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Twilio number'), '+14155550123')
    await user.click(screen.getByRole('button', { name: 'Add number' }))

    expect(await screen.findByText('That number is already connected to a business profile.')).toBeInTheDocument()
  })

  it('connects an email address on the mailbox the server reads', async () => {
    const addresses: { id: string; address: string; created_at: string }[] = []
    const calls = fakeApi({
      'GET /api/v1/auth/me': () => OPERATOR,
      'GET /api/v1/profiles/p1': () => PROFILE,
      'GET /api/v1/profiles/p1/phone-numbers': () => ({ ...SETUP, numbers: [] }),
      'GET /api/v1/profiles/p1/email-addresses': () => ({ ...NO_EMAIL, inbound_enabled: false, addresses }),
      'POST /api/v1/profiles/p1/email-addresses': () => {
        const added = { id: 'a1', address: 'agentiq.demo+restaurant@gmail.com', created_at: '2026-10-21T09:00:00Z' }
        addresses.push(added)
        return reply(201, added)
      },
    })
    const { user } = renderApp('/profiles/p1/channels')

    expect(await screen.findByText('No addresses yet.')).toBeInTheDocument()
    expect(screen.getByText("Emails aren't read yet")).toBeInTheDocument()
    expect(screen.getByText(/e\.g\. agentiq\.demo\+restaurant@gmail\.com/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Email address'), 'agentiq.demo+restaurant@gmail.com')
    await user.click(screen.getByRole('button', { name: 'Add address' }))

    expect(await screen.findByText('agentiq.demo+restaurant@gmail.com')).toBeInTheDocument()
    expect(calls.find((call) => call.method === 'POST')?.body).toEqual({ address: 'agentiq.demo+restaurant@gmail.com' })
  })
})
