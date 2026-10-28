import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, unwrap } from '../../api/client'
import { Badge, Button, Card, Empty, Loading, Notice } from '../../components/ui'
import { errorText, toneFor } from '../../lib/status'
import { formatDateTime, humanize } from '../../lib/format'
import { useProfile } from './useProfile'

/** Bookings made by the assistant or on the dashboard (FR-04, FR-09-01). */
export function BookingsPage() {
  const profile = useProfile()
  const queryClient = useQueryClient()
  const services = new Map(profile.config.services.map((service) => [service.id, service.name]))
  const bookings = useQuery({
    queryKey: ['bookings', profile.id],
    queryFn: () =>
      unwrap(api.GET('/api/v1/profiles/{profile_id}/bookings', { params: { path: { profile_id: profile.id } } })),
  })
  const cancel = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST('/api/v1/profiles/{profile_id}/bookings/{booking_id}/cancel', {
          params: { path: { profile_id: profile.id, booking_id: id } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['bookings', profile.id] }),
  })

  return (
    <Card title="Bookings">
      {bookings.isPending && <Loading />}
      {bookings.isError && <Notice>{errorText(bookings.error)}</Notice>}
      {cancel.isError && <Notice>{errorText(cancel.error)}</Notice>}
      {bookings.data?.length === 0 && <Empty>No bookings yet.</Empty>}
      {bookings.data && bookings.data.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-slate-500 uppercase">
            <tr>
              <th className="py-2">When</th>
              <th>Reference</th>
              <th>Customer</th>
              <th>Service</th>
              <th>Party</th>
              <th>Made by</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {bookings.data.map((booking) => (
              <tr key={booking.id}>
                <td className="py-2">{formatDateTime(booking.starts_at)}</td>
                <td className="font-mono">{booking.reference}</td>
                <td>
                  {booking.customer.name}
                  <span className="block text-xs text-slate-500">{booking.customer.phone}</span>
                </td>
                <td>{services.get(booking.service_id) ?? booking.service_id}</td>
                <td>{booking.party_size}</td>
                <td>{booking.source === 'agent' ? 'Assistant' : 'Dashboard'}</td>
                <td>
                  <Badge tone={toneFor(booking.status)}>{humanize(booking.status)}</Badge>
                </td>
                <td className="text-right">
                  {booking.status === 'confirmed' && (
                    <Button variant="ghost" onClick={() => cancel.mutate(booking.id)} aria-label={`Cancel booking ${booking.reference}`}>
                      Cancel
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}
