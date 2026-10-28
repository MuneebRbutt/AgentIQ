import { createContext, useContext } from 'react'

import type { ErrorMap } from '../../lib/errors'
import type { Path } from '../../lib/paths'

export type Editor = {
  draft: unknown
  errors: ErrorMap
  setValue: (path: Path, value: unknown) => void
}

export const EditorContext = createContext<Editor | null>(null)

export function useEditor(): Editor {
  const editor = useContext(EditorContext)
  if (!editor) throw new Error('editor fields must be inside <EditorContext>')
  return editor
}
