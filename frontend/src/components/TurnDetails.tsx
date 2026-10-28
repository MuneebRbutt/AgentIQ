// What happened behind one assistant reply: the outcome, what each agent did, how the
// customer sounded and how long each step took (FR-08-03, FR-10-02).
import { Badge } from './ui'
import { toneFor } from '../lib/status'
import { formatMs, humanize } from '../lib/format'

type AgentResult = { agent?: unknown; status?: unknown; message?: unknown }

export type TurnData = {
  outcome?: unknown
  results?: unknown
  sentiment?: unknown
  escalation?: unknown
  timings?: unknown
}

export function TurnDetails({ data }: { data: TurnData }) {
  const results = Array.isArray(data.results) ? (data.results as AgentResult[]) : []
  const timings = (typeof data.timings === 'object' && data.timings !== null ? data.timings : {}) as Record<string, number>
  const sentiment = (data.sentiment ?? null) as { data?: { label?: string; confidence?: number; low_confidence?: boolean } } | null
  const escalation = Array.isArray(data.escalation) ? (data.escalation as string[]) : []
  const steps = Object.entries(timings).filter(([name]) => name !== 'total')

  return (
    <div className="mt-1.5 space-y-1.5 text-xs text-slate-600 dark:text-slate-400">
      <div className="flex flex-wrap items-center gap-1.5">
        {typeof data.outcome === 'string' && <Badge tone={toneFor(data.outcome)}>{humanize(data.outcome)}</Badge>}
        {sentiment?.data?.label && (
          <Badge tone={toneFor(sentiment.data.label)}>
            {sentiment.data.label} {sentiment.data.confidence?.toFixed(2)}
            {sentiment.data.low_confidence ? ' (unsure)' : ''}
          </Badge>
        )}
        {escalation.length > 0 && <Badge tone="bad">Escalated: {escalation.map(humanize).join(', ')}</Badge>}
        {timings.total !== undefined && <span>{formatMs(timings.total)} in total</span>}
      </div>
      {results.length > 0 && (
        <ul className="space-y-0.5">
          {results.map((result, index) => (
            <li key={index}>
              <span className="font-medium">{humanize(String(result.agent ?? 'agent'))}</span>{' '}
              <Badge tone={toneFor(String(result.status ?? ''))}>{humanize(String(result.status ?? ''))}</Badge>{' '}
              {String(result.message ?? '')}
            </li>
          ))}
        </ul>
      )}
      {steps.length > 0 && (
        <p>
          {steps.map(([name, ms]) => `${name.replace(/^agent\./, '')} ${formatMs(ms)}`).join(' · ')}
        </p>
      )}
    </div>
  )
}
