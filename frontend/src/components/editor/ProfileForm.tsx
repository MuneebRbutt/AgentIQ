import { useState } from 'react'

import { ApiError } from '../../api/client'
import { toErrorMap } from '../../lib/errors'
import { useProfileDraft } from '../../lib/useProfileDraft'
import { Button, Notice } from '../ui'
import { errorText } from '../../lib/status'
import { ProfileEditor } from './ProfileEditor'

type Props = {
  initial: unknown
  saveLabel: string
  /** Saves the checked draft; throws `ApiError` to report a problem. */
  onSave: (draft: unknown) => Promise<void>
}

/** The editor plus its save bar: nothing is saved until the API says the profile is valid. */
export function ProfileForm({ initial, saveLabel, onSave }: Props) {
  const { draft, errors, dirty, setValue, setErrors, check, setDirty } = useProfileDraft(initial)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const errorCount = [...errors.values()].reduce((sum, messages) => sum + messages.length, 0)

  async function save() {
    setBusy(true)
    setProblem(null)
    setSaved(false)
    try {
      // Mandatory fields are checked before saving (FR-08-05); the save is blocked if any
      // are missing and every problem is highlighted where it is (UC-08 exception).
      if (!(await check(draft))) {
        setProblem('Fix the highlighted fields, then save again.')
        return
      }
      await onSave(draft)
      setDirty(false)
      setSaved(true)
    } catch (caught) {
      if (caught instanceof ApiError && caught.errors.length > 0) setErrors(toErrorMap(caught.errors))
      setProblem(errorText(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="sticky top-0 z-10 -mx-4 mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-slate-50/95 px-4 py-2 backdrop-blur dark:border-slate-800 dark:bg-slate-950/95">
        <p className="text-sm text-slate-600 dark:text-slate-400" aria-live="polite">
          {errorCount > 0
            ? `${errorCount} problem${errorCount === 1 ? '' : 's'} to fix`
            : dirty
              ? 'Unsaved changes'
              : saved
                ? 'Saved. The assistant uses it from the next message.'
                : 'No changes'}
        </p>
        <Button onClick={() => void save()} disabled={busy}>
          {busy ? 'Saving…' : saveLabel}
        </Button>
      </div>
      {problem && (
        <div className="mb-4">
          <Notice>{problem}</Notice>
        </div>
      )}
      <ProfileEditor draft={draft} errors={errors} onChange={setValue} />
    </div>
  )
}
