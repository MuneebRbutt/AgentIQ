import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { AuthContext, type Operator } from './auth-context'
import { api, onSessionEnded, tokenStore, unwrap } from './client'

const SESSION_ENDED = 'Your session has ended. Please log in again.'

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [operator, setOperator] = useState<Operator | null>(null)
  const [checking, setChecking] = useState(() => tokenStore.get() !== null)
  const [notice, setNotice] = useState<string | null>(null)

  // A login from earlier in this tab: ask the API who it belongs to.
  useEffect(() => {
    if (!tokenStore.get()) return
    let cancelled = false
    unwrap(api.GET('/api/v1/auth/me'))
      .then((me) => !cancelled && setOperator(me))
      .catch(() => !cancelled && tokenStore.set(null))
      .finally(() => !cancelled && setChecking(false))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(
    () =>
      onSessionEnded(() => {
        setOperator(null)
        setNotice(SESSION_ENDED)
        queryClient.clear()
      }),
    [queryClient],
  )

  const login = useCallback(async (email: string, password: string) => {
    const token = await unwrap(api.POST('/api/v1/auth/login', { body: { email, password } }))
    tokenStore.set(token.access_token)
    queryClient.clear()
    setNotice(null)
    setOperator(token.operator)
  }, [queryClient])

  const logout = useCallback(async () => {
    try {
      await api.POST('/api/v1/auth/logout')
    } finally {
      tokenStore.set(null)
      setOperator(null)
      queryClient.clear()
    }
  }, [queryClient])

  const value = useMemo(
    () => ({ operator, checking, notice, login, logout }),
    [operator, checking, notice, login, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
