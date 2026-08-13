import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ApiClient } from '../../api/client'

export type WorkspaceFileReference = { conversationId: string; path: string; name?: string }
export type WorkspaceFilePreviewHandler = (reference: WorkspaceFileReference) => void

type PreviewKind = 'markdown' | 'json' | 'code' | 'image' | 'pdf' | 'unsupported'
type TextPreview = { content: string; truncated: boolean; invalidJson: boolean }

const TEXT_LIMIT_BYTES = 768 * 1024
const MARKDOWN_EXTENSIONS = new Set(['md', 'mdx', 'markdown'])
const CODE_EXTENSIONS = new Set(['py', 'pyi', 'js', 'jsx', 'ts', 'tsx', 'java', 'kt', 'go', 'rs', 'rb', 'php', 'cs', 'cpp', 'c', 'h', 'hpp', 'sh', 'bash', 'ps1', 'sql', 'html', 'css', 'scss', 'xml', 'yaml', 'yml', 'toml', 'ini', 'env', 'txt', 'csv', 'log'])

export function workspaceFileUrl(reference: WorkspaceFileReference, disposition: 'inline' | 'attachment' = 'inline') {
  return `/v1/conversations/${encodeURIComponent(reference.conversationId)}/files/${reference.path.split('/').map(encodeURIComponent).join('/')}?disposition=${disposition}`
}

export function parseWorkspaceReference(href: string, conversationId: string): WorkspaceFileReference | null {
  if (!href.startsWith('workspace://')) return null
  const path = decodeURIComponent(href.slice('workspace://'.length)).replaceAll('\\', '/')
  if (!path || path.startsWith('/') || path.split('/').some((part) => !part || part === '.' || part === '..')) return null
  return { conversationId, path, name: path.split('/').at(-1) }
}

export function previewKindFor(path: string): PreviewKind {
  const extension = extensionFor(path)
  if (MARKDOWN_EXTENSIONS.has(extension)) return 'markdown'
  if (extension === 'json') return 'json'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(extension)) return 'image'
  if (extension === 'pdf') return 'pdf'
  return CODE_EXTENSIONS.has(extension) || !extension ? 'code' : 'unsupported'
}

function fileKind(path: string): string {
  const extension = extensionFor(path)
  return extension ? extension.toUpperCase() : 'ARQUIVO'
}

function extensionFor(path: string): string {
  const name = path.split('/').at(-1) ?? path
  const extension = name.split('.').at(-1)?.toLowerCase() ?? ''
  return extension === name.toLowerCase() ? '' : extension
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
  const downloadUrl = workspaceFileUrl(reference, 'attachment')
  const kind = previewKindFor(reference.path)

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
        <div className="workspace-file-preview__body" data-kind={kind}>
          <FilePreview reference={reference} name={name} kind={kind} />
        </div>
      </div>
    </div>
  )

  return typeof document === 'undefined' ? modal : createPortal(modal, document.body)
}

function FilePreview({ reference, name, kind }: { reference: WorkspaceFileReference; name: string; kind: PreviewKind }) {
  const inlineUrl = workspaceFileUrl(reference)
  if (kind === 'image') return <img className="workspace-file-preview__image" src={inlineUrl} alt={`Preview de ${name}`} />
  if (kind === 'pdf') return <iframe title={`Preview de ${name}`} sandbox="allow-scripts" src={inlineUrl} />
  if (kind === 'unsupported') {
    return <div className="workspace-file-preview__unsupported" role="status"><strong>Prévia indisponível neste navegador</strong><span>Abra ou baixe o arquivo para usar o aplicativo local compatível.</span></div>
  }
  return <TextFilePreview url={inlineUrl} path={reference.path} kind={kind} />
}

function TextFilePreview({ url, path, kind }: { url: string; path: string; kind: Extract<PreviewKind, 'markdown' | 'json' | 'code'> }) {
  const [preview, setPreview] = useState<(TextPreview & { url: string }) | null>(null)
  const [failedUrl, setFailedUrl] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const response = await fetch(url, { credentials: 'same-origin', signal: controller.signal })
        if (!response.ok) throw new Error('file preview request failed')
        const text = await readPreviewText(response, controller.signal)
        if (controller.signal.aborted) return
        if (kind === 'json') {
          try {
            setPreview({ ...text, url, content: JSON.stringify(JSON.parse(text.content), null, 2), invalidJson: false })
          } catch {
            setPreview({ ...text, url, invalidJson: true })
          }
          return
        }
        setPreview({ ...text, url, invalidJson: false })
      } catch {
        if (!controller.signal.aborted) setFailedUrl(url)
      }
    })()
    return () => controller.abort()
  }, [kind, url])

  if (failedUrl === url) return <div className="workspace-file-preview__unsupported" role="alert"><strong>Não foi possível carregar esta prévia</strong><span>Tente abrir ou baixar o arquivo.</span></div>
  if (!preview || preview.url !== url) return <p className="workspace-file-preview__loading" role="status">Carregando prévia…</p>

  if (kind === 'markdown') {
    return <article className="workspace-file-preview__markdown markdown-message">
      <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={defaultUrlTransform}>{preview.content}</ReactMarkdown>
      <PreviewFootnote truncated={preview.truncated} />
    </article>
  }

  const label = kind === 'json' ? 'JSON' : codeLanguageLabel(path)
  return <section className="workspace-file-preview__code" aria-label={kind === 'json' ? 'Conteúdo JSON' : `Código ${label}`}>
    <header>
      <span>{label}</span>
      {preview.invalidJson && <span className="workspace-file-preview__warning">JSON inválido; exibindo conteúdo original</span>}
    </header>
    <pre><code>{preview.content}</code></pre>
    <PreviewFootnote truncated={preview.truncated} />
  </section>
}

function codeLanguageLabel(path: string): string {
  const extension = extensionFor(path)
  const labels: Record<string, string> = {
    py: 'Python', pyi: 'Python', js: 'JavaScript', jsx: 'JavaScript', ts: 'TypeScript', tsx: 'TypeScript',
    java: 'Java', kt: 'Kotlin', go: 'Go', rs: 'Rust', rb: 'Ruby', php: 'PHP', cs: 'C#',
    cpp: 'C++', c: 'C', h: 'C', hpp: 'C++', sh: 'Shell', bash: 'Shell', ps1: 'PowerShell',
    sql: 'SQL', html: 'HTML', css: 'CSS', scss: 'SCSS', xml: 'XML', yaml: 'YAML', yml: 'YAML',
    toml: 'TOML', ini: 'INI', env: 'ENV', csv: 'CSV', log: 'Log', txt: 'Texto',
  }
  return labels[extension] ?? 'Texto'
}

function PreviewFootnote({ truncated }: { truncated: boolean }) {
  return truncated ? <p className="workspace-file-preview__truncated" role="status">A prévia mostra os primeiros 768 KB. Baixe ou abra o arquivo para ver o conteúdo completo.</p> : null
}

async function readPreviewText(response: Response, signal: AbortSignal): Promise<Pick<TextPreview, 'content' | 'truncated'>> {
  if (!response.body) {
    const content = await response.text()
    return { content: content.slice(0, TEXT_LIMIT_BYTES), truncated: new TextEncoder().encode(content).byteLength > TEXT_LIMIT_BYTES }
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let read = 0
  let content = ''
  while (true) {
    const next = await reader.read()
    if (next.done) return { content: content + decoder.decode(), truncated: false }
    if (signal.aborted) throw new DOMException('The request was aborted', 'AbortError')
    const remaining = TEXT_LIMIT_BYTES - read
    const chunk = next.value.slice(0, Math.max(remaining, 0))
    content += decoder.decode(chunk, { stream: true })
    read += chunk.byteLength
    if (chunk.byteLength !== next.value.byteLength) {
      await reader.cancel()
      return { content: content + decoder.decode(), truncated: true }
    }
  }
}
