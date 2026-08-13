import type { ApiClient } from '../../api/client'
import type { MessageAttachment } from '../../api/conversations'
import { WorkspaceFileCard, type WorkspaceFilePreviewHandler } from './WorkspaceFileCard'

/** The files a person attached, shown under their own message. */
export function MessageAttachments({
  conversationId,
  items,
  client,
  onPreview,
}: {
  conversationId: string
  items: MessageAttachment[]
  client?: ApiClient
  onPreview?: WorkspaceFilePreviewHandler
}) {
  if (items.length === 0) return null
  return (
    <div className="message-attachments">
      {items.map((item) => (
        <WorkspaceFileCard
          key={item.path}
          reference={{ conversationId, path: item.path, name: item.original_name }}
          client={client}
          onPreview={onPreview}
        />
      ))}
    </div>
  )
}
