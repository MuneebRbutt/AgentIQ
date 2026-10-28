import { useQueryClient } from '@tanstack/react-query'

import { ApiError, api, unwrap, type Schemas } from '../../api/client'
import { ProfileForm } from '../../components/editor/ProfileForm'
import { useProfile } from './useProfile'

export function SettingsPage() {
  const profile = useProfile()
  const queryClient = useQueryClient()

  async function save(draft: unknown) {
    try {
      const updated = await unwrap(
        api.PUT('/api/v1/profiles/{profile_id}', {
          params: { path: { profile_id: profile.id } },
          // The version this edit started from: a save over someone else's is refused.
          body: { version: profile.version, config: draft as Schemas['BusinessProfileConfig-Input'] },
        }),
      )
      queryClient.setQueryData(['profile', profile.id], updated)
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        throw new ApiError(409, 'Someone saved this profile while you were editing. Reload the page to see their changes, then make yours again.')
      }
      throw caught
    }
  }

  return <ProfileForm initial={profile.config} saveLabel="Save changes" onSave={save} />
}
