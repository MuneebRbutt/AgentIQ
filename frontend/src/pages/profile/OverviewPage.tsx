import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { api, unwrap, type Schemas } from '../../api/client'
import { Badge, Card, Empty, Loading, Notice } from '../../components/ui'
import { errorText, toneFor } from '../../lib/status'
import { formatMs, humanize } from '../../lib/format'
import { useProfile } from './useProfile'

const PERIODS = [7, 30, 90]

/** How the assistant is doing: volumes, outcomes, reply times (FR-08-03, FR-10-02).
 *  Plain numbers and tables rather than charts: each figure is one headline. */
export function OverviewPage() {
  const profile = useProfile()
  const [days, setDays] = useState(30)
  const analytics = useQuery({
    queryKey: ['analytics', profile.id, days],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/profiles/{profile_id}/analytics', {
          params: { path: { profile_id: profile.id }, query: { days } },
        }),
      ),
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm">
        <label htmlFor="period">Period</label>
        <select
          id="period"
          className="rounded-md border border-slate-300 bg-white px-2 py-1 dark:border-slate-700 dark:bg-slate-950"
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
        >
          {PERIODS.map((value) => (
            <option key={value} value={value}>
              Last {value} days
            </option>
          ))}
        </select>
      </div>
      {analytics.isPending && <Loading />}
      {analytics.isError && <Notice>{errorText(analytics.error)}</Notice>}
      {analytics.data && <Figures data={analytics.data} />}
    </div>
  )
}

type Data = Schemas['Analytics']

function Figures({ data }: { data: Data }) {
  const conversations = data.conversations.total ?? 0
  const handedOff = data.conversations.handed_off ?? 0
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Conversations" value={String(conversations)} note={`${data.customer_messages} customer messages`} />
        <Stat
          label="Handed to your team"
          value={String(handedOff)}
          note={conversations ? `${Math.round((handedOff / conversations) * 100)}% of conversations` : 'None yet'}
        />
        <Stat label="Bookings" value={String(data.bookings.total ?? 0)} note={`${data.bookings.cancelled ?? 0} cancelled`} />
        <Stat
          label="Reply time (95% within)"
          value={formatMs(data.latency.p95_ms)}
          note={`median ${formatMs(data.latency.p50_ms)} over ${data.latency.replies} replies`}
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="How replies ended">
          <Counts counts={data.outcomes} empty="No replies yet." />
        </Card>
        <Card title="What the agents did">
          {data.agents.length === 0 ? (
            <Empty>No agent tasks yet.</Empty>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-slate-500 uppercase">
                <tr>
                  <th className="py-1">Agent</th>
                  <th>Result</th>
                  <th className="text-right">Tasks</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {data.agents.map((row) => (
                  <tr key={`${row.agent}-${row.status}`}>
                    <td className="py-1.5">{humanize(row.agent)}</td>
                    <td>
                      <Badge tone={toneFor(row.status)}>{humanize(row.status)}</Badge>
                    </td>
                    <td className="text-right tabular-nums">{row.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="How customers sounded">
          <Counts counts={data.sentiment} empty="No messages yet." />
          {(data.conversations.needs_review ?? 0) > 0 && (
            <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">
              {data.conversations.needs_review} conversation(s) need a look: the sentiment check was unsure.
            </p>
          )}
        </Card>
        <Card title="Escalations and follow-ups">
          <h3 className="mb-1 text-xs font-semibold text-slate-500 uppercase">Escalations</h3>
          <Counts counts={withoutTotal(data.escalations)} empty="None." />
          <h3 className="mt-3 mb-1 text-xs font-semibold text-slate-500 uppercase">Follow-up messages</h3>
          <Counts counts={withoutTotal(data.follow_ups)} empty="None." />
        </Card>
      </div>
    </>
  )
}

function withoutTotal(counts: Record<string, number>): Record<string, number> {
  return Object.fromEntries(Object.entries(counts).filter(([key]) => key !== 'total'))
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-sm text-slate-600 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900 tabular-nums dark:text-slate-50">{value}</p>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{note}</p>
    </div>
  )
}

function Counts({ counts, empty }: { counts: Record<string, number>; empty: string }) {
  const entries = Object.entries(counts).sort(([, a], [, b]) => b - a)
  if (entries.length === 0) return <p className="text-sm text-slate-500">{empty}</p>
  return (
    <ul className="flex flex-wrap gap-2">
      {entries.map(([key, count]) => (
        <li key={key} className="flex items-center gap-1.5 text-sm">
          <Badge tone={toneFor(key)}>{humanize(key)}</Badge>
          <span className="tabular-nums">{count}</span>
        </li>
      ))}
    </ul>
  )
}
