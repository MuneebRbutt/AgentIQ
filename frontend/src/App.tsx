import { Route, Routes } from 'react-router'

import { Layout } from './components/Layout'
import { LoginPage } from './pages/LoginPage'
import { NewProfilePage } from './pages/NewProfilePage'
import { ProfilesPage } from './pages/ProfilesPage'
import { BookingsPage } from './pages/profile/BookingsPage'
import { ChannelsPage } from './pages/profile/ChannelsPage'
import { ConsolePage } from './pages/profile/ConsolePage'
import { ConversationDetailPage, ConversationsPage } from './pages/profile/ConversationsPage'
import { EscalationsPage } from './pages/profile/EscalationsPage'
import { FollowUpsPage } from './pages/profile/FollowUpsPage'
import { KnowledgePage } from './pages/profile/KnowledgePage'
import { OverviewPage } from './pages/profile/OverviewPage'
import { ProfileLayout } from './pages/profile/ProfileLayout'
import { SettingsPage } from './pages/profile/SettingsPage'

// The operator dashboard (FR-08). Everything but the login page needs a session.
export function AppRoutes() {
  return (
    <Routes>
      <Route path="login" element={<LoginPage />} />
      <Route element={<Layout />}>
        <Route index element={<ProfilesPage />} />
        <Route path="profiles/new" element={<NewProfilePage />} />
        <Route path="profiles/:profileId" element={<ProfileLayout />}>
          <Route index element={<OverviewPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="knowledge" element={<KnowledgePage />} />
          <Route path="channels" element={<ChannelsPage />} />
          <Route path="console" element={<ConsolePage />} />
          <Route path="conversations" element={<ConversationsPage />} />
          <Route path="conversations/:sessionId" element={<ConversationDetailPage />} />
          <Route path="escalations" element={<EscalationsPage />} />
          <Route path="bookings" element={<BookingsPage />} />
          <Route path="follow-ups" element={<FollowUpsPage />} />
        </Route>
        <Route path="*" element={<p className="p-6">This page does not exist.</p>} />
      </Route>
    </Routes>
  )
}
