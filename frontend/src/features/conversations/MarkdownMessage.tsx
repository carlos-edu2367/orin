import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ApiClient } from '../../api/client'
import { parseWorkspaceReference, WorkspaceFileCard, type WorkspaceFilePreviewHandler } from './WorkspaceFileCard'

type MarkdownMessageProps = {
  content: string
  conversationId?: string
  client?: ApiClient
  onPreview?: WorkspaceFilePreviewHandler
}

export function MarkdownMessage({ content, conversationId, client, onPreview }: MarkdownMessageProps) {
  return (
    <div className="markdown-message">
      <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={(url) => url.startsWith('workspace://') ? url : defaultUrlTransform(url)} components={{ a: ({ href, children }) => {
        const reference = href && conversationId ? parseWorkspaceReference(href, conversationId) : null
          return reference ? <WorkspaceFileCard reference={reference} client={client} onPreview={onPreview} /> : <a href={href}>{children}</a>
      } }}>{content}</ReactMarkdown>
    </div>
  )
}
