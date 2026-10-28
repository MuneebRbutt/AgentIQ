import { createContext, useContext } from 'react'

import type { Schemas } from './client'

export type Operator = Schemas['OperatorOut']

export type AuthState = {
  operator: Operator | null
  /** True while a stored login is being checked on page load. */
  checking: boolean
  /** Why the operator was sent back to the login page, if they didn't log out themselves. */
  notice: string | null
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const state = useContext(AuthContext)
  if (!state) throw new Error('useAuth must be used inside <AuthProvider>')
  return state
}
