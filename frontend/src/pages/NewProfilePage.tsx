import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import { api, unwrap, type Schemas } from '../api/client'
import { ProfileForm } from '../components/editor/ProfileForm'
import { Button, Card, Loading, Notice, PageHeader } from '../components/ui'
import { errorText } from '../lib/status'
import { humanize } from '../lib/format'

type Domain = Schemas['Domain']

// A blank profile with the few settings every business needs, left empty to fill in.
const BLANK = {
  name: '',
  timezone: 'Asia/Karachi',
  currency: 'PKR',
  opening_hours: {},
  services: [],
  resources: [],
  booking_rules: { mode: 'time_slot', slot_minutes: 30, default_duration_minutes: 60, max_party_size: 10 },
  assistant: { greeting: '' },
}

export function NewProfilePage() {
  const [start, setStart] = useState<Domain | 'blank' | null>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const templates = useQuery({
    queryKey: ['templates'],
    queryFn: () => unwrap(api.GET('/api/v1/profiles/templates')),
  })
  const template = useQuery({
    queryKey: ['templates', start],
    queryFn: () => unwrap(api.GET('/api/v1/profiles/templates/{domain}', { params: { path: { domain: start as Domain } } })),
    enabled: start !== null && start !== 'blank',
  })

  const create = useMutation({
    mutationFn: (config: unknown) =>
      unwrap(api.POST('/api/v1/profiles', { body: config as Schemas['BusinessProfileConfig-Input'] })),
    onSuccess: async (profile) => {
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
      navigate(`/profiles/${profile.id}/settings`)
    },
  })

  if (start === null) {
    return (
      <>
        <PageHeader title="New profile" description="Start from an example and change what's different, or from a blank form." />
        {templates.isError && <Notice>{errorText(templates.error)}</Notice>}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {(templates.data ?? []).map((item) => (
            <Card key={item.domain} title={humanize(item.domain)}>
              <p className="mb-3 text-sm text-slate-600 dark:text-slate-400">Example: {item.name}</p>
              <Button onClick={() => setStart(item.domain)}>Use this template</Button>
            </Card>
          ))}
          <Card title="Blank">
            <p className="mb-3 text-sm text-slate-600 dark:text-slate-400">Fill in every section yourself.</p>
            <Button variant="secondary" onClick={() => setStart('blank')}>
              Start blank
            </Button>
          </Card>
        </div>
      </>
    )
  }

  const initial = start === 'blank' ? BLANK : template.data
  return (
    <>
      <PageHeader
        title="New profile"
        actions={
          <Button variant="ghost" onClick={() => setStart(null)}>
            Choose another start
          </Button>
        }
      />
      {template.isError && <Notice>{errorText(template.error)}</Notice>}
      {initial ? (
        <ProfileForm initial={initial} saveLabel="Create profile" onSave={async (draft) => void (await create.mutateAsync(draft))} />
      ) : (
        <Loading />
      )}
    </>
  )
}
