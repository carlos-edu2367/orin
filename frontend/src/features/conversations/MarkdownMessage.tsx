import { isValidElement, useEffect, useRef, useState, type ReactNode } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ApiClient } from '../../api/client'
import {
  InlineWorkspaceFilePill,
  parseWorkspaceReference,
  previewKindFor,
  workspaceFileUrl,
  type WorkspaceFilePreviewHandler,
} from './WorkspaceFileCard'

export type MarkdownMessageProps = {
  content: string
  conversationId?: string
  client?: ApiClient
  onPreview?: WorkspaceFilePreviewHandler
  onPreviewWorkspaceFile?: (path: string, label?: string) => void
}

function extractTextContent(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(extractTextContent).join('')
  if (isValidElement<{ children?: ReactNode }>(node)) return extractTextContent(node.props.children)
  return ''
}

function extractCodeLanguage(node: ReactNode): string {
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = extractCodeLanguage(child)
      if (found !== 'código') return found
    }
    return 'código'
  }
  if (isValidElement<{ className?: string; children?: ReactNode }>(node)) {
    const className = node.props.className ?? ''
    const match = /language-([\w+-]+)/i.exec(className)
    if (match?.[1]) {
      return match[1].toLowerCase()
    }
    if (node.props.children) {
      return extractCodeLanguage(node.props.children)
    }
  }
  return 'código'
}

function MarkdownCodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<number | null>(null)
  const language = extractCodeLanguage(children)
  const codeText = extractTextContent(children).replace(/\n$/, '')

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
      }
    }
  }, [])

  const handleCopy = async () => {
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(codeText)
      }
    } catch {
      // Ignore clipboard write errors in restricted environments
    }
    setCopied(true)
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
    }
    timerRef.current = window.setTimeout(() => {
      setCopied(false)
      timerRef.current = null
    }, 1500)
  }

  return (
    <div className="markdown-code-block" data-testid="markdown-code-block" data-language={language}>
      <div className="markdown-code-block__header">
        <span className="markdown-code-block__lang" data-testid="markdown-code-language">
          {language}
        </span>
        <button
          type="button"
          className="markdown-code-block__copy"
          aria-label="Copiar código"
          onClick={() => void handleCopy()}
        >
          {copied ? 'Copiado!' : 'Copiar'}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  )
}

export function MarkdownMessage({
  content,
  conversationId,
  onPreview,
  onPreviewWorkspaceFile,
}: MarkdownMessageProps) {
  return (
    <div className="markdown-message">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => (url.startsWith('workspace://') ? url : defaultUrlTransform(url))}
        components={{
          pre: ({ children }) => <MarkdownCodeBlock>{children}</MarkdownCodeBlock>,
          img: ({ src, alt }) => {
            const reference = src ? parseWorkspaceReference(src, conversationId ?? '') : null
            if (!reference) {
              return <img src={src} alt={alt ?? ''} />
            }
            const inlineUrl = workspaceFileUrl(reference, 'inline')
            const kind = previewKindFor(reference.path)
            if (kind === 'audio') {
              return <audio className="workspace-file-card__media workspace-file-card__audio" controls preload="metadata" src={inlineUrl} aria-label={alt || reference.name || reference.path} />
            }
            if (kind === 'video') {
              return <video className="workspace-file-card__media workspace-file-card__video" controls preload="metadata" src={inlineUrl} aria-label={alt || reference.name || reference.path} />
            }
            return <img className="workspace-file-card__media workspace-file-card__image" src={inlineUrl} alt={alt || reference.name || reference.path} loading="lazy" />
          },
          a: ({ href, children }) => {
            const reference = href ? parseWorkspaceReference(href, conversationId ?? '') : null
            if (!reference) {
              return <a href={href}>{children}</a>
            }
            const workspacePath = reference.path
            const labelText = extractTextContent(children).trim() || reference.name || workspacePath
            const handlePreview = (path: string, label?: string) => {
              if (onPreviewWorkspaceFile) {
                onPreviewWorkspaceFile(path, label)
              } else if (onPreview) {
                onPreview({
                  conversationId: conversationId ?? '',
                  path,
                  name: label || reference.name,
                })
              }
            }
            return (
              <InlineWorkspaceFilePill
                path={workspacePath}
                label={labelText}
                conversationId={conversationId}
                onPreview={onPreviewWorkspaceFile || onPreview ? handlePreview : undefined}
              />
            )
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
