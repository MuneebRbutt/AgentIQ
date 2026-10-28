import { NavLink, Outlet, useParams } from 'react-router'

import { Badge, Loading, Notice } from '../../components/ui'
import { errorText } from '../../lib/status'
import { humanize } from '../../lib/format'
import { useProfileQuery } from './useProfile'

const TABS = [
  { to: '', label: 'Overview', end: true },
  { to: 'settings', label: 'Settings' },
  { to: 'knowledge', label: 'Knowledge' },
  { to: 'channels', label: 'Calls, SMS & email' },
  { to: 'console', label: 'Test console' },
  { to: 'conversations', label: 'Conversations' },
  { to: 'escalations', label: 'Escalations' },
  { to: 'bookings', label: 'Bookings' },
  { to: 'follow-ups', label: 'Follow-ups' },
]

/** One profile: its name, the tabs, then the tab's page. */
export function ProfileLayout() {
  const { profileId = '' } = useParams()
  const profile = useProfileQuery(profileId)

  if (profile.isPending) return <Loading />
  if (profile.isError) return <Notice>{errorText(profile.error)}</Notice>

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-50">{profile.data.name}</h1>
        <Badge>{humanize(profile.data.domain)}</Badge>
      </div>
      <nav aria-label="Profile pages" className="mb-5 flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800">
        {TABS.map((tab) => (
          <NavLink
            key={tab.label}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) =>
              `-mb-px border-b-2 px-3 py-2 text-sm whitespace-nowrap ${
                isActive
                  ? 'border-indigo-600 font-medium text-indigo-700 dark:text-indigo-300'
                  : 'border-transparent text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100'
              }`
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet context={profile.data} />
    </>
  )
}
