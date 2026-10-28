import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router'

import { api, unwrap, type Schemas } from '../../api/client'
import { TurnDetails } from '../../components/TurnDetails'
import { Badge, Card, Empty, Loading, Notice } from '../../components/ui'
import { errorText, toneFor } from '../../lib/status'
import { formatDateTime, humanize } from '../../lib/format'
import { useProfile } from './useProfile'

type Status = Schemas['SessionOut']['status']
const STATUSES: (Status | '')[] = ['', 'open', 'handed_off', 'closed']

/** Interaction logs: every conversation, newest first (FR-08-03). */
export function ConversationsPage() {
  const profile = useProfile()
  const [status, setStatus] = useState<Status | ''>('')
  const sessions = useQuery({
    queryKey: ['sessions', profile.id, status],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/profiles/{profile_id}/sessions', {
          params: { path: { profile_id: profile.id }, query: status ? { status, limit: 100 } : { limit: 100 } },
        }),
      ),
  })

  return (
    <Card
      title="Conversations"
      actions={
        <label className="flex items-center gap-2 text-sm">
          Show
          <select
            className="rounded-md border border-slate-300 bg-white px-2 py-1 dark:border-slate-700 dark:bg-slate-950"
            value={status}
            onChange={(e) => setStatus(e.target.value as Status | '')}
          >
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {value ? humanize(value) : 'All'}
              </option>
            ))}
          </select>
        </label>
      }
    >
      {sessions.isPending && <Loading />}
      {sessions.isError && <Notice>{errorText(sessions.error)}</Notice>}
      {sessions.data?.length === 0 && <Empty>No conversations yet. Try one in the test console.</Empty>}
      {sessions.data && sessions.data.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-slate-500 uppercase">
            <tr>
              <th className="py-2">Last message</th>
              <th>Customer</th>
              <th>Channel</th>
              <th>Messages</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {sessions.data.map((session) => (
              <tr key={session.id}>
                <td className="py-2">
                  <Link to={session.id} className="text-indigo-600 hover:underline dark:text-indigo-400">
                    {formatDateTime(session.updated_at)}
                  </Link>
                </td>
                <td>{session.customer_phone ?? session.customer_email ?? 'Unknown'}</td>
                <td>{humanize(session.channel)}</td>
                <td>{session.turn_count}</td>
                <td className="space-x-1">
                  <Badge tone={toneFor(session.status)}>{humanize(session.status)}</Badge>
                  {session.needs_review && <Badge tone="warn">Needs review</Badge>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

/** One conversation: every message, what the agents did, and any escalation. */
export function ConversationDetailPage() {
  const profile = useProfile()
  const { sessionId = '' } = useParams()
  const detail = useQuery({
    queryKey: ['session', profile.id, sessionId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/profiles/{profile_id}/sessions/{session_id}', {
          params: { path: { profile_id: profile.id, session_id: sessionId } },
        }),
      ),
  })

  if (detail.isPending) return <Loading />
  if (detail.isError) return <Notice>{errorText(detail.error)}</Notice>
  const session = detail.data

  return (
    <div className="space-y-4">
      <Link to=".." relative="path" className="text-sm text-indigo-600 hover:underline dark:text-indigo-400">
        ← All conversations
      </Link>
      <Card
        title={`Conversation from ${formatDateTime(session.created_at)}`}
        actions={
          <div className="flex gap-1">
            <Badge tone={toneFor(session.status)}>{humanize(session.status)}</Badge>
            {session.needs_review && <Badge tone="warn">Needs review</Badge>}
          </div>
        }
      >
        <p className="mb-3 text-sm text-slate-600 dark:text-slate-400">
          Customer {session.customer_phone ?? session.customer_email ?? 'unknown'} · {humanize(session.channel)} · {session.turn_count} messages
        </p>
        {session.needs_review && (
          <div className="mb-3">
            <Notice tone="info">The sentiment check was unsure about a message here. Read it to decide whether to follow up.</Notice>
          </div>
        )}
        {session.escalations.map((escalation) => (
          <div key={escalation.id} className="mb-3">
            <Notice title="Handed over to your team">
              {escalation.reasons.map(humanize).join(', ')} · notification {escalation.notification} · status {escalation.status}.{' '}
              <Link to="../../escalations" relative="path" className="underline">
                Open escalations
              </Link>
            </Notice>
          </div>
        ))}
        <ol className="space-y-3">
          {session.turns.map((turn) => (
            <li key={turn.seq} className="rounded-md border border-slate-100 p-3 dark:border-slate-800">
              <p className="text-xs font-semibold text-slate-500 uppercase">{turn.role === 'customer' ? 'Customer' : 'Assistant'}</p>
              <p className="mt-0.5 text-sm whitespace-pre-wrap">{turn.text}</p>
              {turn.role === 'assistant' && <TurnDetails data={turn.data} />}
            </li>
          ))}
        </ol>
        {session.turns.length === 0 && <Empty>No messages yet.</Empty>}
      </Card>
    </div>
  )
}
