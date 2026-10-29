import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ApiClient } from '../../api/client'
import { useSession } from '../../app/useSession'
import type { StructuredPatchHunk } from './activityTypes'

export type WorkspaceFileReference = { conversationId: string; path: string; name?: string }
export type WorkspaceFilePreviewHandler = (reference: WorkspaceFileReference) => void

type PreviewKind = 'markdown' | 'json' | 'code' | 'image' | 'audio' | 'video' | 'pdf' | 'unsupported'
type TextPreview = { content: string; truncated: boolean; invalidJson: boolean }

const TEXT_LIMIT_BYTES = 768 * 1024
const MARKDOWN_EXTENSIONS = new Set(['md', 'mdx', 'markdown'])
const CODE_EXTENSIONS = new Set(['py', 'pyi', 'js', 'jsx', 'ts', 'tsx', 'java', 'kt', 'go', 'rs', 'rb', 'php', 'cs', 'cpp', 'c', 'h', 'hpp', 'sh', 'bash', 'ps1', 'sql', 'html', 'css', 'scss', 'xml', 'yaml', 'yml', 'toml', 'ini', 'env', 'txt', 'csv', 'log'])

export function workspaceFileUrl(reference: WorkspaceFileReference, disposition: 'inline' | 'attachment' = 'inline') {
  return `/v1/conversations/${encodeURIComponent(reference.conversationId)}/files/${reference.path.split('/').map(encodeURIComponent).join('/')}?disposition=${disposition}`
}

export function parseWorkspaceReference(href: string, conversationId = ''): WorkspaceFileReference | null {
  if (!href.startsWith('workspace://')) return null
  const path = decodeURIComponent(href.slice('workspace://'.length)).replaceAll('\\', '/')
  if (!path || path.startsWith('/') || path.split('/').some((part) => !part || part === '.' || part === '..')) return null
  return { conversationId, path, name: path.split('/').at(-1) }
}

export type InlineWorkspaceFilePillProps = {
  path: string
  label?: string
  conversationId?: string
  reference?: WorkspaceFileReference
  onPreview?: ((path: string, label?: string) => void) | WorkspaceFilePreviewHandler
  onPreviewWorkspaceFile?: (path: string, label?: string) => void
}

export function InlineWorkspaceFilePill({
  path,
  label,
  conversationId,
  reference,
  onPreview,
  onPreviewWorkspaceFile,
}: InlineWorkspaceFilePillProps) {
  const resolvedPath = path || reference?.path || ''
  const fileName = resolvedPath.split('/').at(-1) || resolvedPath
  const displayLabel = label?.trim() || reference?.name || fileName

  const handleClick = () => {
    if (onPreviewWorkspaceFile) {
      onPreviewWorkspaceFile(resolvedPath, displayLabel)
      return
    }
    if (onPreview) {
      if (reference && !path) {
        ;(onPreview as WorkspaceFilePreviewHandler)(reference)
      } else {
        ;(onPreview as (p: string, l?: string) => void)(resolvedPath, displayLabel)
      }
      return
    }
    const targetConversationId = conversationId ?? reference?.conversationId
    if (targetConversationId && typeof window !== 'undefined') {
      const url = workspaceFileUrl({ conversationId: targetConversationId, path: resolvedPath, name: fileName }, 'inline')
      window.open(url, '_blank', 'noopener,noreferrer')
    }
  }

  return (
    <button
      type="button"
      data-testid="inline-workspace-file-pill"
      data-workspace-path={resolvedPath}
      title={resolvedPath}
      aria-label={`Visualizar ${fileName}`}
      onClick={handleClick}
      className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-1.5 py-0.5 font-mono text-[12px] text-[var(--text-primary)] hover:border-[var(--accent-primary)] hover:bg-[var(--surface-hover)] align-baseline transition-colors"
    >
      <span aria-hidden="true" className="text-[11px] leading-none opacity-75">▣</span>
      <span>{displayLabel}</span>
    </button>
  )
}

export function previewKindFor(path: string): PreviewKind {
  const extension = extensionFor(path)
  if (MARKDOWN_EXTENSIONS.has(extension)) return 'markdown'
  if (extension === 'json') return 'json'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(extension)) return 'image'
  if (['mp3', 'wav', 'ogg'].includes(extension)) return 'audio'
  if (['mp4', 'webm'].includes(extension)) return 'video'
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
  addedLines,
  removedLines,
}: {
  reference: WorkspaceFileReference
  client?: ApiClient
  onPreview?: WorkspaceFilePreviewHandler
  addedLines?: number
  removedLines?: number
}) {
  const name = reference.name ?? reference.path.split('/').at(-1) ?? reference.path
  const inlineUrl = workspaceFileUrl(reference, 'inline')
  const downloadUrl = workspaceFileUrl(reference, 'attachment')
  const kind = previewKindFor(reference.path)
  const { capabilities } = useSession()
  const hasStats = addedLines !== undefined || removedLines !== undefined
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
    <article className="workspace-file-card" data-deliverable="true" aria-label={`Arquivo ${name}`}>
      {kind === 'image' && (
        <img
          className="workspace-file-card__media workspace-file-card__image"
          src={inlineUrl}
          alt={name}
          loading="lazy"
        />
      )}
      {kind === 'audio' && (
        <audio
          className="workspace-file-card__media workspace-file-card__audio"
          controls
          preload="metadata"
          src={inlineUrl}
          aria-label={`Áudio ${name}`}
        />
      )}
      {kind === 'video' && (
        <video
          className="workspace-file-card__media workspace-file-card__video"
          controls
          preload="metadata"
          src={inlineUrl}
          aria-label={`Vídeo ${name}`}
        />
      )}
      <button
        type="button"
        className="workspace-file-card__main"
        onClick={() => onPreview?.(reference)}
        aria-label={`Abrir preview ${name}`}
      >
        <span className="workspace-file-card__glyph" aria-hidden="true">▣</span>
        <span className="workspace-file-card__details">
          <span className="workspace-file-card__name">{name}</span>
          <span className="workspace-file-card__meta">
            {fileKind(reference.path)} · workspace
            {hasStats && (
              <span className="workspace-file-card__diff-stats" aria-label={`Alterações +${addedLines ?? 0} -${removedLines ?? 0}`}>
                {' · '}
                <span className="workspace-diff__stat-add">+{addedLines ?? 0}</span>
                {' '}
                <span className="workspace-diff__stat-del">-{removedLines ?? 0}</span>
              </span>
            )}
          </span>
        </span>
      </button>
      <span className="workspace-file-card__actions">
        <button type="button" onClick={() => onPreview?.(reference)} aria-label={`Visualizar ${name}`}>Abrir ↗</button>
        <a href={downloadUrl} aria-label={`Baixar ${name}`}>Baixar</a>
        {capabilities.open_in_desktop_app && <button type="button" onClick={() => void open()} disabled={!client} aria-label={`Abrir ${name} no sistema`}>Abrir</button>}
      </span>
    </article>
  )
}

export function WorkspaceFilePreview({
  reference,
  client,
  onClose,
  structuredPatch,
  addedLines,
  removedLines,
  initialTab = 'file',
}: {
  reference: WorkspaceFileReference
  client?: ApiClient
  onClose: () => void
  structuredPatch?: StructuredPatchHunk[]
  addedLines?: number
  removedLines?: number
  initialTab?: 'file' | 'diff'
}) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const name = reference.name ?? reference.path.split('/').at(-1) ?? reference.path
  const downloadUrl = workspaceFileUrl(reference, 'attachment')
  const { capabilities } = useSession()
  const kind = previewKindFor(reference.path)
  const hasPatch = Boolean(structuredPatch && structuredPatch.length > 0)
  const [activeTab, setActiveTab] = useState<'file' | 'diff'>(hasPatch && initialTab === 'diff' ? 'diff' : 'file')
  const [diffMode, setDiffMode] = useState<'unified' | 'split'>('unified')

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
              <p>
                {reference.path}
                {(addedLines !== undefined || removedLines !== undefined) && (
                  <span className="workspace-file-preview__diff-stats">
                    {' · '}
                    <span className="workspace-diff__stat-add">+{addedLines ?? 0}</span>
                    {' '}
                    <span className="workspace-diff__stat-del">-{removedLines ?? 0}</span>
                  </span>
                )}
              </p>
            </div>
          </div>
          <div className="workspace-file-preview__actions">
            {hasPatch && (
              <div className="workspace-file-preview__tabs" role="tablist" aria-label="Modo de visualização">
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeTab === 'file'}
                  className={activeTab === 'file' ? 'is-active' : ''}
                  onClick={() => setActiveTab('file')}
                >
                  Arquivo Completo
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeTab === 'diff'}
                  className={activeTab === 'diff' ? 'is-active' : ''}
                  onClick={() => setActiveTab('diff')}
                >
                  Alterações (Diff)
                </button>
                {activeTab === 'diff' && (
                  <button
                    type="button"
                    className="workspace-file-preview__diff-mode"
                    onClick={() => setDiffMode((current) => (current === 'unified' ? 'split' : 'unified'))}
                  >
                    {diffMode === 'unified' ? 'Lado a Lado' : 'Unificado'}
                  </button>
                )}
              </div>
            )}
            <a href={downloadUrl} aria-label={`Baixar ${name}`}>Baixar</a>
            {capabilities.open_in_desktop_app && <button type="button" onClick={() => void open()} disabled={!client} aria-label={`Abrir ${name} no sistema`}>Abrir</button>}
            <button ref={closeButtonRef} type="button" className="workspace-file-preview__close" onClick={onClose}>Fechar</button>
          </div>
        </header>
        <div className="workspace-file-preview__body" data-kind={activeTab === 'diff' ? 'diff' : kind}>
          {activeTab === 'diff' && structuredPatch ? (
            <StructuredDiffView hunks={structuredPatch} mode={diffMode} />
          ) : (
            <FilePreview reference={reference} name={name} kind={kind} />
          )}
        </div>
      </div>
    </div>
  )

  return typeof document === 'undefined' ? modal : createPortal(modal, document.body)
}

export function StructuredDiffView({
  hunks,
  mode = 'unified',
}: {
  hunks: StructuredPatchHunk[]
  mode?: 'unified' | 'split'
}) {
  if (mode === 'split') {
    return (
      <section className="workspace-diff workspace-diff--split" aria-label="Visualização de diff lado a lado">
        {hunks.map((hunk, hunkIdx) => {
          let oldLine = hunk.oldStart
          let newLine = hunk.newStart
          const rows: Array<{ oldNo: string; oldText: string; oldKind: string; newNo: string; newText: string; newKind: string }> = []
          for (const line of hunk.lines) {
            if (line.startsWith('-')) {
              rows.push({ oldNo: String(oldLine++), oldText: line.slice(1), oldKind: 'del', newNo: '', newText: '', newKind: 'empty' })
            } else if (line.startsWith('+')) {
              rows.push({ oldNo: '', oldText: '', oldKind: 'empty', newNo: String(newLine++), newText: line.slice(1), newKind: 'add' })
            } else {
              const text = line.startsWith(' ') ? line.slice(1) : line
              rows.push({ oldNo: String(oldLine++), oldText: text, oldKind: 'ctx', newNo: String(newLine++), newText: text, newKind: 'ctx' })
            }
          }
          return (
            <div key={hunkIdx} className="workspace-diff__hunk">
              <div className="workspace-diff__header">
                {`@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@`}
              </div>
              {rows.map((row, rowIdx) => (
                <div key={rowIdx} className="workspace-diff__split-row">
                  <div className={`workspace-diff__cell is-${row.oldKind}`} data-diff-line={row.oldKind}>
                    <span className="workspace-diff__lineno">{row.oldNo}</span>
                    <span className="workspace-diff__code">{row.oldText}</span>
                  </div>
                  <div className={`workspace-diff__cell is-${row.newKind}`} data-diff-line={row.newKind}>
                    <span className="workspace-diff__lineno">{row.newNo}</span>
                    <span className="workspace-diff__code">{row.newText}</span>
                  </div>
                </div>
              ))}
            </div>
          )
        })}
      </section>
    )
  }

  return (
    <section className="workspace-diff workspace-diff--unified" aria-label="Visualização de diff unificado">
      {hunks.map((hunk, hunkIdx) => {
        let oldLine = hunk.oldStart
        let newLine = hunk.newStart
        return (
          <div key={hunkIdx} className="workspace-diff__hunk">
            <div className="workspace-diff__header">
              {`@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@`}
            </div>
            {hunk.lines.map((line, lineIdx) => {
              const isAdd = line.startsWith('+')
              const isDel = line.startsWith('-')
              const kind = isAdd ? 'add' : isDel ? 'del' : 'ctx'
              const oldNo = isAdd ? '' : String(oldLine++)
              const newNo = isDel ? '' : String(newLine++)
              return (
                <div key={lineIdx} className={`workspace-diff__line is-${kind}`} data-diff-line={kind}>
                  <span className="workspace-diff__lineno">{oldNo}</span>
                  <span className="workspace-diff__lineno">{newNo}</span>
                  <span className="workspace-diff__code">{line}</span>
                </div>
              )
            })}
          </div>
        )
      })}
    </section>
  )
}

function FilePreview({ reference, name, kind }: { reference: WorkspaceFileReference; name: string; kind: PreviewKind }) {
  const inlineUrl = workspaceFileUrl(reference)
  if (kind === 'image') return <img className="workspace-file-preview__image" src={inlineUrl} alt={`Preview de ${name}`} />
  if (kind === 'audio') return <audio className="workspace-file-preview__audio" controls preload="metadata" src={inlineUrl} aria-label={`Preview de ${name}`} />
  if (kind === 'video') return <video className="workspace-file-preview__video" controls preload="metadata" src={inlineUrl} aria-label={`Preview de ${name}`} />
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
