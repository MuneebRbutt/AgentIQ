import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'

import { api, unwrap } from '../api/client'
import { HealthStatus } from '../components/HealthStatus'
import { Badge, Card, Empty, Loading, Notice, PageHeader } from '../components/ui'
import { errorText } from '../lib/status'
import { formatDateTime, humanize } from '../lib/format'

export function ProfilesPage() {
  const profiles = useQuery({
    queryKey: ['profiles'],
    queryFn: () => unwrap(api.GET('/api/v1/profiles')),
  })

  return (
    <>
      <PageHeader
        title="Business profiles"
        description="Each profile is one business line your assistant answers for."
        actions={
          <Link
            to="/profiles/new"
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500"
          >
            New profile
          </Link>
        }
      />
      <div className="space-y-4">
        <Card>
          {profiles.isPending && <Loading />}
          {profiles.isError && <Notice>{errorText(profiles.error)}</Notice>}
          {profiles.data?.length === 0 && (
            <Empty>No profiles yet. Create one from a hotel, restaurant or clinic template to start.</Empty>
          )}
          {profiles.data && profiles.data.length > 0 && (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {profiles.data.map((profile) => (
                <li key={profile.id}>
                  <Link
                    to={`/profiles/${profile.id}`}
                    className="flex flex-wrap items-center justify-between gap-2 py-3 hover:text-indigo-600"
                  >
                    <span className="font-medium">{profile.name}</span>
                    <span className="flex items-center gap-3 text-sm text-slate-500 dark:text-slate-400">
                      <Badge>{humanize(profile.domain)}</Badge>
                      version {profile.version} · saved {formatDateTime(profile.updated_at)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="System status">
          <HealthStatus />
        </Card>
      </div>
    </>
  )
}
