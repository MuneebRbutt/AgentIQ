import { useQuery } from '@tanstack/react-query'
import { useOutletContext } from 'react-router'

import { api, unwrap, type Schemas } from '../../api/client'

export type Profile = Schemas['ProfileOut']

export function useProfileQuery(id: string) {
  return useQuery({
    queryKey: ['profile', id],
    queryFn: () => unwrap(api.GET('/api/v1/profiles/{profile_id}', { params: { path: { profile_id: id } } })),
  })
}

/** The profile whose page is open, from `ProfileLayout`. */
export function useProfile(): Profile {
  return useOutletContext<Profile>()
}
