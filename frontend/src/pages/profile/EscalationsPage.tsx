import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'

import { api, unwrap, type Schemas } from '../../api/client'
import { Badge, Button, Card, Empty, Loading, Notice } from '../../components/ui'
import { errorText, toneFor } from '../../lib/status'
import { formatDateTime, humanize } from '../../lib/format'
import { useProfile } from './useProfile'

type Status = Schemas['EscalationOut']['status']
type Escalation = Schemas['EscalationOut']

// Plain words for why a conversation was handed over (NFR02-02).
const REASONS: Record<string, string> = {
  negative_sentiment: 'The customer sounded very upset',
  keyword: 'The customer used one of your escalation keywords',
  repeated_negative: 'The customer was unhappy for several messages',
  unclear: "The assistant couldn't understand the customer",
}

const NOTIFICATION: Record<string, string> = {
  none: 'Nobody was notified: add a notify phone or email under Settings → Escalation.',
  logged: 'Written to the server log only: SMS and email are not set up yet.',
  sent: 'Your team was notified.',
  failed: 'Notifying your team failed; it is tried once more automatically.',
}

/** Conversations handed to a person, to call back and mark handled (FR-06-04, FR-08-03). */
export function EscalationsPage() {
  const profile = useProfile()
  const [status, setStatus] = useState<Status | ''>('open')
  const escalations = useQuery({
    queryKey: ['escalations', profile.id, status],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/profiles/{profile_id}/escalations', {
          params: { path: { profile_id: profile.id }, query: status ? { status, limit: 100 } : { limit: 100 } },
        }),
      ),
  })

  return (
    <Card
      title="Escalations"
      actions={
        <div className="flex gap-1" role="group" aria-label="Filter escalations">
          {(['open', 'acknowledged', 'resolved', ''] as const).map((value) => (
            <Button key={value} variant={status === value ? 'primary' : 'ghost'} onClick={() => setStatus(value)}>
              {value ? humanize(value) : 'All'}
            </Button>
          ))}
        </div>
      }
    >
      {escalations.isPending && <Loading />}
      {escalations.isError && <Notice>{errorText(escalations.error)}</Notice>}
      {escalations.data?.length === 0 && <Empty>Nothing here. Escalations appear when a customer needs a person.</Empty>}
      <ul className="space-y-3">
        {escalations.data?.map((escalation) => (
          <EscalationItem key={escalation.id} escalation={escalation} profileId={profile.id} />
        ))}
      </ul>
    </Card>
  )
}

function EscalationItem({ escalation, profileId }: { escalation: Escalation; profileId: string }) {
  const queryClient = useQueryClient()
  const [note, setNote] = useState(escalation.note ?? '')
  const update = useMutation({
    mutationFn: (status: Status) =>
      unwrap(
        api.PATCH('/api/v1/profiles/{profile_id}/escalations/{escalation_id}', {
          params: { path: { profile_id: profileId, escalation_id: escalation.id } },
          body: { status, ...(note.trim() ? { note: note.trim() } : {}) },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['escalations', profileId] }),
  })

  return (
    <li className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={toneFor(escalation.status)}>{humanize(escalation.status)}</Badge>
          <span className="text-sm text-slate-500">{formatDateTime(escalation.created_at)}</span>
        </div>
        <Link to={`../conversations/${escalation.session_id}`} className="text-sm text-indigo-600 hover:underline dark:text-indigo-400">
          Read the conversation
        </Link>
      </div>
      <p className="mt-2 text-sm font-medium">
        {escalation.reasons.map((reason) => REASONS[reason] ?? humanize(reason)).join('. ')}.
      </p>
      <p className="mt-1 text-sm">
        Call back: <strong>{escalation.customer_phone ?? 'no number given'}</strong>. They said: “{escalation.customer_message}”
      </p>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{NOTIFICATION[escalation.notification]}</p>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="min-w-60 flex-1 text-sm">
          <span className="mb-1 block font-medium">Note</span>
          <input
            className="block w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-950"
            value={note}
            placeholder="What you did, e.g. called back and rebooked"
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        {escalation.status === 'open' && (
          <Button variant="secondary" onClick={() => update.mutate('acknowledged')} disabled={update.isPending}>
            Acknowledge
          </Button>
        )}
        {escalation.status !== 'resolved' && (
          <Button onClick={() => update.mutate('resolved')} disabled={update.isPending}>
            Mark resolved
          </Button>
        )}
      </div>
      {update.isError && (
        <div className="mt-2">
          <Notice>{errorText(update.error)}</Notice>
        </div>
      )}
    </li>
  )
}
