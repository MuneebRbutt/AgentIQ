import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'

import { api, unwrap, type Schemas } from '../../api/client'
import { TurnDetails } from '../../components/TurnDetails'
import { Button, Card, Field, Notice } from '../../components/ui'
import { errorText } from '../../lib/status'
import { useProfile } from './useProfile'

type Line =
  | { role: 'assistant'; text: string; reply?: Schemas['MessageOut'] }
  | { role: 'customer'; text: string }

/** Talk to the assistant as a customer would, to try scenarios before going live (FR-08-02). */
export function ConsolePage() {
  const profile = useProfile()
  const queryClient = useQueryClient()
  const [phone, setPhone] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [text, setText] = useState('')
  const [ended, setEnded] = useState<string | null>(null)
  const path = { profile_id: profile.id }

  const start = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/profiles/{profile_id}/sessions', {
          params: { path },
          body: phone.trim() ? { customer_phone: phone.trim() } : {},
        }),
      ),
    onSuccess: (session) => {
      setSessionId(session.id)
      setLines([{ role: 'assistant', text: session.greeting }])
      setEnded(null)
    },
  })

  const send = useMutation({
    mutationFn: (message: string) =>
      unwrap(
        api.POST('/api/v1/profiles/{profile_id}/sessions/{session_id}/messages', {
          params: { path: { ...path, session_id: sessionId ?? '' } },
          body: { text: message },
        }),
      ),
    onMutate: (message) => setLines((current) => [...current, { role: 'customer', text: message }]),
    onSuccess: (reply) => {
      setLines((current) => [...current, { role: 'assistant', text: reply.reply, reply }])
      if (reply.handed_off) setEnded('Handed over to your team: see Escalations. Start a new conversation to keep testing.')
      void queryClient.invalidateQueries({ queryKey: ['sessions', profile.id] })
    },
  })

  const close = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/profiles/{profile_id}/sessions/{session_id}/close', {
          params: { path: { ...path, session_id: sessionId ?? '' } },
        }),
      ),
    onSuccess: () => setEnded('Conversation closed.'),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    const message = text.trim()
    if (!message || !sessionId) return
    setText('')
    send.mutate(message)
  }

  if (!sessionId) {
    return (
      <Card title="Test console">
        <p className="mb-3 text-sm text-slate-600 dark:text-slate-400">
          Chat with your assistant the way a customer would. Conversations here are real: bookings are made and show up in
          the logs.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Pretend to call from (optional)" hint="Lets you test bookings and follow-ups for this number." className="w-72">
            {(props) => <input {...props} type="tel" value={phone} placeholder="+92 300 1234567" onChange={(e) => setPhone(e.target.value)} />}
          </Field>
          <Button onClick={() => start.mutate()} disabled={start.isPending}>
            Start a conversation
          </Button>
        </div>
        {start.isError && (
          <div className="mt-3">
            <Notice>{errorText(start.error)}</Notice>
          </div>
        )}
      </Card>
    )
  }

  return (
    <Card
      title="Test console"
      actions={
        <div className="flex gap-2">
          <Link to={`../conversations/${sessionId}`} className="px-2 py-1.5 text-sm text-indigo-600 hover:underline dark:text-indigo-400">
            Open in logs
          </Link>
          {!ended && (
            <Button variant="secondary" onClick={() => close.mutate()}>
              Close conversation
            </Button>
          )}
          <Button variant="secondary" onClick={() => setSessionId(null)}>
            New conversation
          </Button>
        </div>
      }
    >
      <ol aria-label="Conversation" className="mb-3 max-h-[60vh] space-y-3 overflow-y-auto">
        {lines.map((line, index) => (
          <li key={index} className={line.role === 'customer' ? 'flex justify-end' : 'flex justify-start'}>
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                line.role === 'customer'
                  ? 'bg-indigo-600 text-white'
                  : 'bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-slate-100'
              }`}
            >
              <p className="whitespace-pre-wrap">{line.text}</p>
              {line.role === 'assistant' && line.reply && (
                <TurnDetails data={line.reply} />
              )}
            </div>
          </li>
        ))}
        {send.isPending && (
          <li role="status" className="text-sm text-slate-500">
            The assistant is replying…
          </li>
        )}
      </ol>
      {send.isError && <Notice>{errorText(send.error)}</Notice>}
      {ended ? (
        <Notice tone="info">{ended}</Notice>
      ) : (
        <form onSubmit={submit} className="flex gap-2">
          <label htmlFor="console-message" className="sr-only">
            Message
          </label>
          <input
            id="console-message"
            className="block w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950"
            value={text}
            placeholder="Type as the customer…"
            onChange={(e) => setText(e.target.value)}
            disabled={send.isPending}
          />
          <Button type="submit" disabled={send.isPending || !text.trim()}>
            Send
          </Button>
        </form>
      )}
    </Card>
  )
}
