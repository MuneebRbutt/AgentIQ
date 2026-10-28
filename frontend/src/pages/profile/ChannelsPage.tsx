import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api, unwrap } from '../../api/client'
import { Button, Card, Empty, Field, Loading, Notice } from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { errorText } from '../../lib/status'
import { useProfile } from './useProfile'

/** How customers reach this profile's assistant: phone calls and text messages to Twilio
 * numbers, and emails to connected addresses (FR-02-01/02/03, ADR-0010, ADR-0011). */
export function ChannelsPage() {
  return (
    <div className="space-y-4">
      <PhoneSection />
      <EmailSection />
    </div>
  )
}

type Connected = { id: string; created_at: string; label: string }

/** Adds and removes the numbers or addresses that reach the profile. */
function ConnectedList({
  title,
  intro,
  fieldLabel,
  fieldHint,
  inputType,
  addLabel,
  emptyText,
  items,
  loading,
  error,
  onAdd,
  onRemove,
}: {
  title: string
  intro: string
  fieldLabel: string
  fieldHint: string
  inputType: 'tel' | 'email'
  addLabel: string
  emptyText: string
  items: Connected[] | undefined
  loading: boolean
  error: unknown
  onAdd: (value: string) => Promise<unknown>
  onRemove: (id: string) => Promise<unknown>
}) {
  const [value, setValue] = useState('')
  const add = useMutation({ mutationFn: onAdd, onSuccess: () => setValue('') })
  const remove = useMutation({ mutationFn: onRemove })

  return (
    <Card title={title}>
      <p className="mb-3 text-sm text-slate-600 dark:text-slate-400">{intro}</p>
      <form
        className="mb-4 flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (value.trim()) add.mutate(value.trim())
        }}
      >
        <Field label={fieldLabel} hint={fieldHint} className="w-80">
          {(props) => <input {...props} type={inputType} value={value} onChange={(e) => setValue(e.target.value)} />}
        </Field>
        <Button type="submit" disabled={add.isPending || !value.trim()}>
          {add.isPending ? 'Adding…' : addLabel}
        </Button>
      </form>
      {add.isError && <Notice>{errorText(add.error)}</Notice>}
      {remove.isError && <Notice>{errorText(remove.error)}</Notice>}
      {loading && <Loading />}
      {error != null && <Notice>{errorText(error)}</Notice>}
      {items?.length === 0 && <Empty>{emptyText}</Empty>}
      {items && items.length > 0 && (
        <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {items.map((item) => (
            <li key={item.id} className="flex items-center justify-between py-2">
              <span>
                <span className="font-medium">{item.label}</span>
                <span className="ml-3 text-slate-500">added {formatDateTime(item.created_at)}</span>
              </span>
              <Button variant="ghost" onClick={() => remove.mutate(item.id)} aria-label={`Remove ${item.label}`}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function PhoneSection() {
  const profile = useProfile()
  const queryClient = useQueryClient()
  const key = ['phone-numbers', profile.id]
  const path = { params: { path: { profile_id: profile.id } } }
  const refresh = () => queryClient.invalidateQueries({ queryKey: key })

  const setup = useQuery({
    queryKey: key,
    queryFn: () => unwrap(api.GET('/api/v1/profiles/{profile_id}/phone-numbers', path)),
  })

  return (
    <>
      <ConnectedList
        title="Phone numbers"
        intro="Calls and text messages to these numbers are answered by this profile's assistant. Buy a number with voice and SMS in the Twilio console, then add it here."
        fieldLabel="Twilio number"
        fieldHint="With the country code, e.g. +14155550123"
        inputType="tel"
        addLabel="Add number"
        emptyText="No numbers yet."
        items={setup.data?.numbers.map((n) => ({ id: n.id, created_at: n.created_at, label: n.number }))}
        loading={setup.isPending}
        error={setup.error}
        onAdd={async (number) => {
          await unwrap(api.POST('/api/v1/profiles/{profile_id}/phone-numbers', { ...path, body: { number } }))
          await refresh()
        }}
        onRemove={async (numberId) => {
          await unwrap(
            api.DELETE('/api/v1/profiles/{profile_id}/phone-numbers/{number_id}', {
              params: { path: { profile_id: profile.id, number_id: numberId } },
            }),
          )
          await refresh()
        }}
      />
      {setup.data && (
        <Card title="Twilio settings for each number">
          {!setup.data.signatures_checked && (
            <Notice title="Calls and messages are refused for now">
              Set TWILIO_AUTH_TOKEN in the server&apos;s .env so it can check that requests really come from Twilio.
            </Notice>
          )}
          <dl className="space-y-2 text-sm">
            <Setting label="A call comes in (Webhook, HTTP POST)" value={setup.data.incoming_webhook_url} />
            <Setting label="Call status changes" value={setup.data.status_callback_url} />
            <Setting label="A message comes in (Webhook, HTTP POST)" value={setup.data.sms_webhook_url} />
          </dl>
        </Card>
      )}
    </>
  )
}

function EmailSection() {
  const profile = useProfile()
  const queryClient = useQueryClient()
  const key = ['email-addresses', profile.id]
  const path = { params: { path: { profile_id: profile.id } } }
  const refresh = () => queryClient.invalidateQueries({ queryKey: key })

  const setup = useQuery({
    queryKey: key,
    queryFn: () => unwrap(api.GET('/api/v1/profiles/{profile_id}/email-addresses', path)),
  })
  const mailbox = setup.data?.mailbox_address
  const [name, domain] = mailbox ? mailbox.split('@') : ['name', 'gmail.com']

  return (
    <>
      <ConnectedList
        title="Email addresses"
        intro="Emails to these addresses are answered by this profile's assistant, from the same address and in the same thread."
        fieldLabel="Email address"
        fieldHint={`An address that arrives in the server's mailbox, e.g. ${name}+${profile.domain}@${domain}`}
        inputType="email"
        addLabel="Add address"
        emptyText="No addresses yet."
        items={setup.data?.addresses.map((a) => ({ id: a.id, created_at: a.created_at, label: a.address }))}
        loading={setup.isPending}
        error={setup.error}
        onAdd={async (address) => {
          await unwrap(api.POST('/api/v1/profiles/{profile_id}/email-addresses', { ...path, body: { address } }))
          await refresh()
        }}
        onRemove={async (addressId) => {
          await unwrap(
            api.DELETE('/api/v1/profiles/{profile_id}/email-addresses/{address_id}', {
              params: { path: { profile_id: profile.id, address_id: addressId } },
            }),
          )
          await refresh()
        }}
      />
      {setup.data && !setup.data.inbound_enabled && (
        <Notice title="Emails aren't read yet" tone="info">
          Set IMAP_HOST, IMAP_USERNAME, IMAP_PASSWORD and SMTP_HOST in the server&apos;s .env so it can read the mailbox
          and send replies.
        </Notice>
      )}
    </>
  )
}

function Setting({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-slate-500">{label}</dt>
      <dd className="font-mono break-all">{value}</dd>
    </div>
  )
}
