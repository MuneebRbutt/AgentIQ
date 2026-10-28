// The profile being edited, checked against the API as the operator types, so problems
// show up where they are before saving (FR-08-05). The server's check is the only one:
// the dashboard never re-implements the profile rules.
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, unwrap, type FieldError } from '../api/client'
import { toErrorMap, type ErrorMap } from './errors'
import { setIn, type Path } from './paths'

const CHECK_DELAY_MS = 500

export async function validateProfile(draft: unknown): Promise<FieldError[]> {
  const report = await unwrap(
    api.POST('/api/v1/profiles/validate', { body: draft as Record<string, unknown> }),
  )
  return report.valid ? [] : (report.errors as FieldError[])
}

export function useProfileDraft(initial: unknown) {
  const [draft, setDraft] = useState<unknown>(initial)
  const [errors, setErrors] = useState<ErrorMap>(new Map())
  const [dirty, setDirty] = useState(false)
  const [checked, setChecked] = useState(false)
  const latest = useRef(0)

  const check = useCallback(async (value: unknown): Promise<boolean> => {
    const ticket = ++latest.current
    const found = await validateProfile(value)
    // Answers can arrive out of order; only the newest one counts.
    if (ticket === latest.current) {
      setErrors(toErrorMap(found))
      setChecked(true)
    }
    return found.length === 0
  }, [])

  // Re-check a while after the last change, once the operator has started editing.
  useEffect(() => {
    if (!dirty) return
    const timer = setTimeout(() => {
      check(draft).catch(() => undefined)
    }, CHECK_DELAY_MS)
    return () => clearTimeout(timer)
  }, [draft, dirty, check])

  const setValue = useCallback((path: Path, value: unknown) => {
    setDraft((current: unknown) => setIn(current, path, value))
    setDirty(true)
  }, [])

  return { draft, errors, dirty, checked, setValue, setErrors, check, setDirty }
}
