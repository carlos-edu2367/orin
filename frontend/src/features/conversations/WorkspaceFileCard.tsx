import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import type { ApiClient } from '../../api/client'

export type WorkspaceFileReference = { conversationId: string; path: string; name?: string }
export type WorkspaceFilePreviewHandler = (reference: WorkspaceFileReference) => void

export function workspaceFileUrl(reference: WorkspaceFileReference, disposition: 'inline' | 'attachment' = 'inline') {
  return `/v1/conversations/${encodeURIComponent(reference.conversationId)}/files/${reference.path.split('/').map(encodeURIComponent).join('/')}?disposition=${disposition}`
}

export function parseWorkspaceReference(href: string, conversationId: string): WorkspaceFileReference | null {
  if (!href.startsWith('workspace://')) return null
  const path = decodeURIComponent(href.slice('workspace://'.length)).replaceAll('\\', '/')
  if (!path || path.startsWith('/') || path.split('/').some((part) => !part || part === '.' || part === '..')) return null
  return { conversationId, path, name: path.split('/').at(-1) }
}

function fileKind(path: string): string {
  const extension = path.split('.').at(-1)?.toLowerCase()
  if (!extension || extension === path.toLowerCase()) return 'arquivo'
  return extension.toUpperCase()
}

export function WorkspaceFileCard({
  reference,
  client,
  onPreview,
}: {
  reference: WorkspaceFileReference
  client?: ApiClient
  onPreview?: WorkspaceFilePreviewHandler
}) {
  const name = reference.name ?? reference.path.split('/').at(-1) ?? reference.path
  const downloadUrl = workspaceFileUrl(reference, 'attachment')
  const open = async () => {
    if (!client) return
    await client.request({
      path: `/v1/conversations/${encodeURIComponent(reference.conversationId)}/files/${reference.path.split('/').map(encodeURIComponent).join('/')}/open`,
      method: 'POST',
      body: {},
      parse: (value) => value,
    })
  }

  return (
    <span className="workspace-file-card" aria-label={`Arquivo ${name}`}>
      <button
        type="button"
        className="workspace-file-card__main"
        onClick={() => onPreview?.(reference)}
        aria-label={`Abrir preview ${name}`}
      >
        <span className="workspace-file-card__glyph" aria-hidden="true">▣</span>
        <span className="workspace-file-card__details">
          <span className="workspace-file-card__name">{name}</span>
          <span className="workspace-file-card__meta">{fileKind(reference.path)} · workspace</span>
        </span>
      </button>
      <span className="workspace-file-card__actions">
        <button type="button" onClick={() => onPreview?.(reference)} aria-label={`Visualizar ${name}`}>Prévia</button>
        <a href={downloadUrl} aria-label={`Baixar ${name}`}>Baixar</a>
        <button type="button" onClick={() => void open()} disabled={!client} aria-label={`Abrir ${name} no sistema`}>Abrir</button>
      </span>
    </span>
  )
}

export function WorkspaceFilePreview({
  reference,
  client,
  onClose,
}: {
  reference: WorkspaceFileReference
  client?: ApiClient
  onClose: () => void
}) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const name = reference.name ?? reference.path.split('/').at(-1) ?? reference.path
  const inlineUrl = workspaceFileUrl(reference)
  const downloadUrl = workspaceFileUrl(reference, 'attachment')

  useEffect(() => {
    const previous = document.activeElement
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    document.body.classList.add('has-workspace-file-preview')
    closeButtonRef.current?.focus()
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.classList.remove('has-workspace-file-preview')
      if (previous instanceof HTMLElement) previous.focus()
    }
  }, [onClose])

  const open = async () => {
    if (!client) return
    await client.request({
      path: `/v1/conversations/${encodeURIComponent(reference.conversationId)}/files/${reference.path.split('/').map(encodeURIComponent).join('/')}/open`,
      method: 'POST',
      body: {},
      parse: (value) => value,
    })
  }

  const modal = (
    <div
      className="workspace-file-preview"
      role="dialog"
      aria-modal="true"
      aria-labelledby="workspace-file-preview-title"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="workspace-file-preview__panel">
        <header className="workspace-file-preview__header">
          <div className="workspace-file-preview__identity">
            <span className="workspace-file-preview__glyph" aria-hidden="true">▣</span>
            <div>
              <h2 id="workspace-file-preview-title">{name}</h2>
              <p>{reference.path}</p>
            </div>
          </div>
          <div className="workspace-file-preview__actions">
            <a href={downloadUrl} aria-label={`Baixar ${name}`}>Baixar</a>
            <button type="button" onClick={() => void open()} disabled={!client} aria-label={`Abrir ${name} no sistema`}>Abrir</button>
            <button ref={closeButtonRef} type="button" className="workspace-file-preview__close" onClick={onClose}>Fechar</button>
          </div>
        </header>
        <div className="workspace-file-preview__body">
          <iframe title={`Preview de ${name}`} sandbox="allow-scripts" src={inlineUrl} />
        </div>
      </div>
    </div>
  )

  return typeof document === 'undefined' ? modal : createPortal(modal, document.body)
}
