import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'

import { api, unwrap, upload } from '../../api/client'
import { Button, Card, Empty, Loading, Notice } from '../../components/ui'
import { errorText } from '../../lib/status'
import { formatBytes, formatDateTime } from '../../lib/format'
import { useProfile } from './useProfile'

const ACCEPT = '.pdf,.docx,.txt,.md'

/** Documents the assistant answers from (FR-05, FR-08-04 "knowledge resources"). */
export function KnowledgePage() {
  const profile = useProfile()
  const queryClient = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [message, setMessage] = useState<string | null>(null)
  const key = ['documents', profile.id]

  const documents = useQuery({
    queryKey: key,
    queryFn: () => unwrap(api.GET('/api/v1/profiles/{profile_id}/documents', { params: { path: { profile_id: profile.id } } })),
  })

  const add = useMutation({
    mutationFn: (file: File) => upload<{ replaced?: boolean }>(`/api/v1/profiles/${profile.id}/documents`, file),
    onSuccess: async (_, file) => {
      setMessage(`${file.name} is ready; the assistant can answer from it now.`)
      await queryClient.invalidateQueries({ queryKey: key })
    },
  })

  const remove = useMutation({
    mutationFn: (documentId: string) =>
      unwrap(
        api.DELETE('/api/v1/profiles/{profile_id}/documents/{document_id}', {
          params: { path: { profile_id: profile.id, document_id: documentId } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  })

  return (
    <div className="space-y-4">
      <Card
        title="Knowledge documents"
        actions={
          <>
            <input
              ref={input}
              type="file"
              accept={ACCEPT}
              className="sr-only"
              aria-label="Choose a document to upload"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) add.mutate(file)
                e.target.value = ''
              }}
            />
            <Button onClick={() => input.current?.click()} disabled={add.isPending}>
              {add.isPending ? 'Uploading…' : 'Upload a document'}
            </Button>
          </>
        }
      >
        <p className="mb-3 text-sm text-slate-600 dark:text-slate-400">
          PDF, Word, text or Markdown. Uploading a file with the same name replaces the old one.
        </p>
        {add.isError && <Notice>{errorText(add.error)}</Notice>}
        {remove.isError && <Notice>{errorText(remove.error)}</Notice>}
        {message && !add.isError && <Notice tone="good">{message}</Notice>}
        {documents.isPending && <Loading />}
        {documents.isError && <Notice>{errorText(documents.error)}</Notice>}
        {documents.data?.length === 0 && <Empty>No documents yet. Menus, price lists and policies work well.</Empty>}
        {documents.data && documents.data.length > 0 && (
          <table className="mt-2 w-full text-left text-sm">
            <thead className="text-xs text-slate-500 uppercase">
              <tr>
                <th className="py-2">File</th>
                <th>Size</th>
                <th>Passages</th>
                <th>Uploaded</th>
                <th />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {documents.data.map((document) => (
                <tr key={document.id}>
                  <td className="py-2 font-medium">{document.filename}</td>
                  <td>{formatBytes(document.size_bytes)}</td>
                  <td>{document.chunk_count}</td>
                  <td>{formatDateTime(document.updated_at)}</td>
                  <td className="text-right">
                    <Button variant="ghost" onClick={() => remove.mutate(document.id)} aria-label={`Delete ${document.filename}`}>
                      Delete
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
