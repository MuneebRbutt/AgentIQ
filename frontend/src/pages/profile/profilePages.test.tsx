// The operator's views of one profile: logs (TC-08 step 3, FR-08-03), the test console
// (FR-08-02) and escalation handling (FR-06-04).
import { screen, within } from '@testing-library/react'

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
const SESSION = {
  id: 's1',
  profile_id: 'p1',
  channel: 'text',
  customer_phone: '+92 300 1234567',
  status: 'closed',
  turn_count: 2,
  needs_review: false,
  created_at: '2026-10-19T10:00:00Z',
  updated_at: '2026-10-19T10:05:00Z',
}
const BOOKED = {
  outcome: 'completed',
  results: [{ agent: 'booking', status: 'completed', message: 'Booked K7M2Q9 for 4 on Wed 21 Oct at 7:00 PM.' }],
  timings: { understand: 410, 'agent.booking.t1': 95, reply: 380, total: 905 },
}

function routes(extra: Parameters<typeof fakeApi>[0]) {
  return fakeApi({
    'GET /api/v1/auth/me': () => OPERATOR,
    'GET /api/v1/profiles/p1': () => PROFILE,
    ...extra,
  })
}

describe('conversation logs (TC-08 step 3)', () => {
  it('lists conversations and shows what the agents did in one', async () => {
    routes({
      'GET /api/v1/profiles/p1/sessions': () => [SESSION],
      'GET /api/v1/profiles/p1/sessions/s1': () => ({
        ...SESSION,
        turns: [
          { seq: 1, role: 'customer', text: 'Table for 4 on Wednesday at 7pm', data: {}, created_at: SESSION.created_at },
          { seq: 2, role: 'assistant', text: 'Booked! Your reference is K7M2Q9.', data: BOOKED, created_at: SESSION.created_at },
        ],
        sentiment: [],
        escalations: [],
      }),
    })
    const { user } = renderApp('/profiles/p1/conversations')

    const table = await screen.findByRole('table')
    expect(within(table).getByText('+92 300 1234567')).toBeInTheDocument()
    await user.click(within(table).getAllByRole('link')[0])

    expect(await screen.findByText('Table for 4 on Wednesday at 7pm')).toBeInTheDocument()
    expect(screen.getByText('Booked! Your reference is K7M2Q9.')).toBeInTheDocument()
    expect(screen.getByText(/Booked K7M2Q9 for 4/)).toBeInTheDocument()
    expect(screen.getByText(/in total/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '← All conversations' })).toHaveAttribute('href', '/profiles/p1/conversations')
  })
})

describe('test console (FR-08-02)', () => {
  it('starts a conversation, sends a message and shows the hand-off', async () => {
    const calls = routes({
      'POST /api/v1/profiles/p1/sessions': () =>
        reply(201, { ...SESSION, status: 'open', turn_count: 1, greeting: 'Welcome to Spice Garden! How can I help?' }),
      'POST /api/v1/profiles/p1/sessions/s1/messages': () => ({
        session_id: 's1',
        reply: 'I am sorry. I have asked our manager to call you back.',
        outcome: 'handoff',
        handed_off: true,
        results: [],
        escalation_id: 'e1',
        timings: { total: 640 },
      }),
    })
    const { user } = renderApp('/profiles/p1/console')

    await user.type(await screen.findByLabelText(/Pretend to call from/), '+92 300 1234567')
    await user.click(screen.getByRole('button', { name: 'Start a conversation' }))
    expect(await screen.findByText('Welcome to Spice Garden! How can I help?')).toBeInTheDocument()

    await user.type(screen.getByLabelText('Message'), 'This is the worst service, I want a manager')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('I am sorry. I have asked our manager to call you back.')).toBeInTheDocument()
    expect(screen.getByText(/Handed over to your team/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Message')).not.toBeInTheDocument()
    expect(calls.find((call) => call.path === '/api/v1/profiles/p1/sessions')?.body).toEqual({ customer_phone: '+92 300 1234567' })
    expect(calls.find((call) => call.path.endsWith('/messages'))?.body).toEqual({ text: 'This is the worst service, I want a manager' })
  })
})

describe('escalations (FR-06-04)', () => {
  it('marks an escalation resolved with the operator note', async () => {
    let status = 'open'
    const calls = routes({
      'GET /api/v1/profiles/p1/escalations': (call) =>
        new URLSearchParams(call.query).get('status') === status
          ? [
              {
                id: 'e1',
                profile_id: 'p1',
                session_id: 's1',
                turn_seq: 3,
                reasons: ['negative_sentiment'],
                customer_message: 'This is the worst service',
                customer_phone: '+92 300 1234567',
                sentiment_label: 'negative',
                sentiment_confidence: 0.97,
                status,
                notification: 'sent',
                notified_at: '2026-10-19T10:01:00Z',
                note: null,
                handled_by: null,
                created_at: '2026-10-19T10:01:00Z',
                updated_at: '2026-10-19T10:01:00Z',
              },
            ]
          : [],
      'PATCH /api/v1/profiles/p1/escalations/e1': (call) => {
        status = (call.body as { status: string }).status
        return { id: 'e1', status }
      },
    })
    const { user } = renderApp('/profiles/p1/escalations')

    expect(await screen.findByText(/This is the worst service/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Note'), 'Called back and apologised')
    await user.click(screen.getByRole('button', { name: 'Mark resolved' }))

    expect(await screen.findByText(/Nothing here/)).toBeInTheDocument()
    expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({ status: 'resolved', note: 'Called back and apologised' })
  })
})

describe('overview (FR-08-03)', () => {
  it('shows the period figures and asks again when the period changes', async () => {
    const calls = routes({
      'GET /api/v1/profiles/p1/analytics': () => ({
        period_days: 30,
        since: '2026-09-20T00:00:00Z',
        conversations: { total: 8, closed: 6, handed_off: 2, needs_review: 1 },
        customer_messages: 21,
        outcomes: { completed: 15, handoff: 2, total: 17 },
        agents: [{ agent: 'booking', status: 'completed', count: 5 }],
        latency: { replies: 17, average_ms: 1200, p50_ms: 1100, p95_ms: 2400 },
        sentiment: { positive: 9, neutral: 10, negative: 2 },
        escalations: { open: 1, resolved: 1, total: 2 },
        bookings: { confirmed: 4, cancelled: 1, total: 5 },
        follow_ups: { sent: 6, failed: 0, total: 6 },
      }),
    })
    const { user } = renderApp('/profiles/p1')

    expect(await screen.findByText('25% of conversations')).toBeInTheDocument()
    expect(screen.getByText('21 customer messages')).toBeInTheDocument()
    expect(screen.getByText(/1 conversation\(s\) need a look/)).toBeInTheDocument()

    await user.selectOptions(screen.getByRole('combobox'), '7')
    await screen.findByText('25% of conversations')
    const periods = calls.filter((call) => call.path.endsWith('/analytics')).map((call) => new URLSearchParams(call.query).get('days'))
    expect(periods).toEqual(['30', '7'])
  })
})
