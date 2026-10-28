export type Tone = 'neutral' | 'good' | 'warn' | 'bad' | 'info'

/** Status words used across the API, mapped to a colour. */
export function toneFor(status: string): Tone {
  if (['sent', 'confirmed', 'resolved', 'closed', 'completed', 'positive', 'answered', 'booked'].includes(status))
    return 'good'
  if (['pending', 'acknowledged', 'logged', 'open', 'needs_info', 'needs_confirmation', 'neutral'].includes(status))
    return 'info'
  if (['failed', 'handed_off', 'handoff', 'negative', 'rejected', 'unavailable'].includes(status)) return 'bad'
  if (['cancelled', 'no_answer'].includes(status)) return 'warn'
  return 'neutral'
}

/** A message fit to show the operator for any failure. */
export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.'
}
