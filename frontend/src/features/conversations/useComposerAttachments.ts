import { useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { deleteUpload, uploadFile } from '../../api/uploads'
import type { ComposerAttachment } from './AttachmentChips'

function guessKind(file: File): ComposerAttachment['kind'] {
  if (file.type.startsWith('image/')) return 'image'
  if (file.type === 'application/pdf') return 'pdf'
  if (file.type.includes('officedocument')) return 'office'
  return 'text'
}

export type ComposerAttachmentsController = {
  attachments: ComposerAttachment[]
  onAttach: (files: File[]) => void
  onRemoveAttachment: (id: string) => void
  /** False while any attachment is still uploading — the composer must block sending. */
  canSend: boolean
  /** The `upload_id`s ready to send, in attachment order. */
  readyUploadIds: () => string[]
  /** Clears the staged attachments and revokes their preview URLs, after a successful send. */
  reset: () => void
}

/**
 * Staging for files picked before they belong to any message yet.
 *
 * A file is uploaded the moment it is picked so the person can see and remove
 * it before sending; the conversation (or message) it ends up on is only
 * decided at submit time, by which `upload_id`s are still staged and ready.
 * Shared by every entry point that has a composer — the home screen, where a
 * conversation does not exist yet, and an existing chat — so the two cannot
 * drift apart.
 */
export function useComposerAttachments(client: ApiClient): ComposerAttachmentsController {
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([])
  const attachmentsRef = useRef(attachments)
  useEffect(() => { attachmentsRef.current = attachments }, [attachments])

  // Leaving mid-upload must not leak the preview blob: nothing else ever
  // revokes a chip's object URL once the page it lives on is gone.
  useEffect(() => () => {
    attachmentsRef.current.forEach((item) => { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl) })
  }, [])

  function onAttach(files: File[]) {
    for (const file of files) {
      const id = crypto.randomUUID()
      const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined
      setAttachments((current) => [...current, { id, filename: file.name, kind: guessKind(file), bytes: file.size, state: 'uploading', previewUrl }])
      uploadFile(client, file)
        .then((uploaded) => {
          setAttachments((current) => current.map((item) => item.id === id
            ? { ...item, state: 'ready', upload_id: uploaded.upload_id, kind: uploaded.kind, bytes: uploaded.bytes }
            : item))
        })
        .catch(() => {
          setAttachments((current) => current.map((item) => item.id === id ? { ...item, state: 'failed', error: 'Não foi possível enviar.' } : item))
        })
    }
  }

  function onRemoveAttachment(id: string) {
    setAttachments((current) => {
      const target = current.find((item) => item.id === id)
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl)
      if (target?.upload_id) void deleteUpload(client, target.upload_id).catch(() => undefined)
      return current.filter((item) => item.id !== id)
    })
  }

  function readyUploadIds(): string[] {
    return attachments.filter((item) => item.state === 'ready').map((item) => item.upload_id!)
  }

  function reset() {
    setAttachments((current) => {
      current.forEach((item) => { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl) })
      return []
    })
  }

  return {
    attachments,
    onAttach,
    onRemoveAttachment,
    canSend: attachments.every((item) => item.state !== 'uploading'),
    readyUploadIds,
    reset,
  }
}
