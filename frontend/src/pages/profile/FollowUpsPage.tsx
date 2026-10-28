import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api, unwrap, type Schemas } from '../../api/client'
import { Badge, Button, Card, Empty, Loading, Notice } from '../../components/ui'
import { errorText, toneFor } from '../../lib/status'
import { formatDateTime, humanize } from '../../lib/format'
import { useProfile } from './useProfile'

type Status = Schemas['FollowUpOut']['status']

/** Confirmations, reminders and feedback requests, sent and upcoming (FR-07). */
export function FollowUpsPage() {
  const profile = useProfile()
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<Status | ''>('')
  const key = ['follow-ups', profile.id, status]
  const followUps = useQuery({
    queryKey: key,
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/profiles/{profile_id}/follow-ups', {
          params: { path: { profile_id: profile.id }, query: status ? { status, limit: 100 } : { limit: 100 } },
        }),
      ),
  })
  const cancel = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST('/api/v1/profiles/{profile_id}/follow-ups/{follow_up_id}/cancel', {
          params: { path: { profile_id: profile.id, follow_up_id: id } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['follow-ups', profile.id] }),
  })

  return (
    <Card
      title="Follow-ups"
      actions={
        <label className="flex items-center gap-2 text-sm">
          Show
          <select
            className="rounded-md border border-slate-300 bg-white px-2 py-1 dark:border-slate-700 dark:bg-slate-950"
            value={status}
            onChange={(e) => setStatus(e.target.value as Status | '')}
          >
            {(['', 'pending', 'sent', 'logged', 'failed', 'cancelled'] as const).map((value) => (
              <option key={value} value={value}>
                {value ? humanize(value) : 'All'}
              </option>
            ))}
          </select>
        </label>
      }
    >
      <p className="mb-3 text-sm text-slate-600 dark:text-slate-400">
        “Logged” means SMS or email isn't set up yet, so the message was only written to the server log.
      </p>
      {followUps.isPending && <Loading />}
      {followUps.isError && <Notice>{errorText(followUps.error)}</Notice>}
      {cancel.isError && <Notice>{errorText(cancel.error)}</Notice>}
      {followUps.data?.length === 0 && <Empty>No follow-ups yet. They start with the first booking.</Empty>}
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {followUps.data?.map((item) => (
          <li key={item.id} className="py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">{humanize(item.kind)}</span>
                <Badge tone={toneFor(item.status)}>{humanize(item.status)}</Badge>
                <span className="text-slate-500">
                  {item.channel.toUpperCase()} to {item.recipient} · {item.status === 'pending' ? 'due' : 'was due'}{' '}
                  {formatDateTime(item.due_at)}
                </span>
              </div>
              {item.status === 'pending' && (
                <Button variant="ghost" onClick={() => cancel.mutate(item.id)} disabled={cancel.isPending}>
                  Cancel
                </Button>
              )}
            </div>
            <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">{item.body}</p>
            {item.last_error && (
              <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">
                Last attempt failed: {item.last_error} ({item.attempts} of 2 tries)
              </p>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}
