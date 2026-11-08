import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import type { StructuredPatchHunk } from './activityTypes'
import { StructuredDiffView } from './WorkspaceFileCard'
import { WorkbenchFileTree } from './WorkbenchFileTree'
import { fileIconFor } from './workbenchTreeModel'
import { isLikelyHmrUrl, type CodeWorkbenchPreviewState } from './workbenchPreview'
import './codeWorkbench.css'

export type { CodeWorkbenchPreviewState } from './workbenchPreview'

export type CodeWorkbenchFileItem = {
  path: string
  status?: 'modified' | 'added' | 'deleted' | 'inspected'
  addedLines?: number
  removedLines?: number
  structuredPatch?: StructuredPatchHunk[]
}

export type CodeWorkbenchTerminalEntry = {
  id: string
  command: string
  output?: string
  status?: 'running' | 'completed' | 'failed'
}

export type CodeWorkbenchTab = 'preview' | 'files' | 'terminal'

export type CodeWorkbenchPanelProps = {
  files: readonly CodeWorkbenchFileItem[]
  terminalEntries?: readonly CodeWorkbenchTerminalEntry[]
  activeTab?: CodeWorkbenchTab
  onTabChange?: (tab: CodeWorkbenchTab) => void
  initialPreviewUrl?: string | null
  /** Preview server reported by the agent (PreviewStart / PreviewOpen); wins over initialPreviewUrl. */
  preview?: CodeWorkbenchPreviewState | null
  /** Changes after every successful write/edit: drives "auto-reload on file changes". */
  fileChangeToken?: string | null
  onRetryPreview?: () => void
  /** Workspace folder name shown in the explorer header. */
  rootName?: string | null
  onRefreshFiles?: () => void
  running?: boolean
  elapsedLabel?: string | null
  selectedFilePath?: string | null
  selectedPath?: string | null
  onSelectFile?: (path: string | null) => void
  onClose?: () => void
  conversationId?: string
  onStartPreview?: () => Promise<unknown> | unknown
}

const URL_ERROR_MESSAGE = 'URL inválida. Use http:// ou https://.'
export const AUTO_RELOAD_DEBOUNCE_MS = 600

export function sanitizePreviewUrl(raw: string): string | null {
  if (typeof raw !== 'string') return null
  const trimmed = raw.trim()
  if (
    !trimmed ||
    Array.from(trimmed).some((ch) => {
      const code = ch.charCodeAt(0)
      return code <= 0x1f || code === 0x7f
    })
  ) {
    return null
  }
  try {
    const parsed = new URL(trimmed)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return null
    }
    if (!parsed.hostname || parsed.username || parsed.password) {
      return null
    }
    return trimmed
  } catch {
    return null
  }
}

function detectPreviewUrlFromTerminal(
  terminalEntries: readonly CodeWorkbenchTerminalEntry[],
): string | null {
  for (let i = terminalEntries.length - 1; i >= 0; i -= 1) {
    const entry = terminalEntries[i]
    const text = `${entry.output ?? ''} ${entry.command ?? ''}`
    const match = text.match(/https?:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?(?:\/[^\s"'<>]*)?/i)
    if (match?.[0]) {
      const sanitized = sanitizePreviewUrl(match[0])
      if (sanitized) return sanitized
    }
  }
  return null
}

function sameOrigin(a: string, b: string): boolean {
  try {
    return new URL(a).origin === new URL(b).origin
  } catch {
    return false
  }
}

/** Current address of the preview frame when the browser lets us read it (same origin only). */
function readFrameHref(frame: HTMLIFrameElement | null): string | null {
  try {
    const href = frame?.contentWindow?.location.href
    return href && href !== 'about:blank' ? href : null
  } catch {
    return null
  }
}

function statusLabelForFile(status?: CodeWorkbenchFileItem['status']): string {
  switch (status) {
    case 'added':
      return 'Adicionado'
    case 'deleted':
      return 'Removido'
    case 'inspected':
      return 'Inspecionado'
    default:
      return 'Modificado'
  }
}

function statusLabelForTerminal(status?: CodeWorkbenchTerminalEntry['status']): string {
  switch (status) {
    case 'running':
      return 'Em execução'
    case 'failed':
      return 'Falhou'
    default:
      return 'Concluído'
  }
}

function describeFile(file: CodeWorkbenchFileItem): string {
  const stats =
    file.addedLines !== undefined || file.removedLines !== undefined
      ? ` · +${file.addedLines ?? 0} −${file.removedLines ?? 0}`
      : ''
  return `${file.path} · ${statusLabelForFile(file.status)}${stats}`
}

type UserUrlState = { token: string | null; draft: string | null; committed: string | null }
type ReloadState = { count: number; base: string; href: string | null }

function ReloadIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" focusable="false">
      <path d="M13 8a5 5 0 1 1-1.5-3.55M13 2.5v2.5h-2.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function ExternalIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" focusable="false">
      <path d="M9 2.5h4.5V7M13.5 2.5 7.5 8.5M11.5 9.5v4h-9v-9h4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function DesktopIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" focusable="false">
      <path d="M2 3h12v8H2zM6 14h4M8 11v3" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  )
}

function MobileIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" focusable="false">
      <path d="M4.5 1.5h7v13h-7zM7 12.5h2" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  )
}

export function CodeWorkbenchPanel({
  files,
  terminalEntries = [],
  activeTab,
  onTabChange,
  initialPreviewUrl,
  preview: previewProp = null,
  fileChangeToken = null,
  onRetryPreview,
  rootName = null,
  onRefreshFiles,
  running = false,
  elapsedLabel = null,
  selectedFilePath,
  onSelectFile,
  onClose,
  conversationId,
  onStartPreview,
}: CodeWorkbenchPanelProps) {
  const [manualPreview, setManualPreview] = useState<{
    state: CodeWorkbenchPreviewState
    overriddenToken: string | null
  } | null>(null)
  const preview =
    manualPreview && (previewProp?.token ?? null) === manualPreview.overriddenToken
      ? manualPreview.state
      : (previewProp ?? manualPreview?.state ?? null)

  const handleStartPreview = async () => {
    const overriddenToken = previewProp?.token ?? null
    const startToken = `manual:${Date.now()}`
    setManualPreview({ state: { status: 'starting', token: startToken }, overriddenToken })
    try {
      let payload: unknown = null
      if (onStartPreview) {
        payload = await onStartPreview()
      } else {
        const res = await fetch(
          `/v1/conversations/${encodeURIComponent(conversationId ?? 'default')}/preview/start`,
          { method: 'POST', credentials: 'same-origin' },
        )
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`)
        }
        try {
          payload = await res.json()
        } catch {
          payload = null
        }
      }
      if (payload && typeof payload === 'object') {
        const rec = payload as Record<string, unknown>
        const rawPreview =
          rec.preview && typeof rec.preview === 'object' ? (rec.preview as Record<string, unknown>) : rec
        const rawUrl = typeof rawPreview.url === 'string' ? rawPreview.url : undefined
        const rawStatus = typeof rawPreview.status === 'string' ? rawPreview.status : undefined
        const rawName = typeof rawPreview.name === 'string' ? rawPreview.name : undefined
        if (rawUrl) {
          setManualPreview({
            state: { status: 'running', url: rawUrl, name: rawName, token: `${startToken}:ok` },
            overriddenToken,
          })
        } else if (rawStatus === 'exited' || rawStatus === 'stopped' || rawStatus === 'failed') {
          setManualPreview({
            state: {
              status: 'failed',
              name: rawName,
              error: typeof rawPreview.error === 'string' ? rawPreview.error : 'Falha ao iniciar o servidor.',
              token: `${startToken}:err`,
            },
            overriddenToken,
          })
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Falha ao iniciar o servidor.'
      setManualPreview({
        state: { status: 'failed', error: message, token: `${startToken}:err` },
        overriddenToken,
      })
    }
  }
  const previewToken = preview?.token ?? null
  const agentUrl = preview?.url ? sanitizePreviewUrl(preview.url) : null

  const resolvedInitialUrl = useMemo(() => {
    if (agentUrl && preview?.status === 'running') return agentUrl
    const fromProp = initialPreviewUrl ? sanitizePreviewUrl(initialPreviewUrl) : null
    if (fromProp) return fromProp
    if (!initialPreviewUrl) {
      const fromTerminal = detectPreviewUrlFromTerminal(terminalEntries)
      if (fromTerminal) return fromTerminal
    }
    return agentUrl ?? ''
  }, [agentUrl, initialPreviewUrl, preview?.status, terminalEntries])

  const [internalTab, setInternalTab] = useState<CodeWorkbenchTab>(activeTab ?? 'preview')
  // What the person typed in the URL bar; discarded when the agent opens another preview.
  const [userUrl, setUserUrl] = useState<UserUrlState>({ token: previewToken, draft: null, committed: null })
  const [urlError, setUrlError] = useState<string | null>(null)
  const [viewport, setViewport] = useState<'desktop' | 'mobile'>('desktop')
  const [reload, setReload] = useState<ReloadState>({ count: 0, base: '', href: null })
  const [autoReloadOverride, setAutoReloadOverride] = useState<boolean | null>(null)
  const [dismissedFailure, setDismissedFailure] = useState<string | null>(null)
  const [fileQuery, setFileQuery] = useState<string>('')
  const [userSelectedFile, setUserSelectedFile] = useState<{
    baseProp: string | null | undefined
    path: string
  } | null>(null)
  const frameRef = useRef<HTMLIFrameElement | null>(null)
  const lastLoadedHref = useRef<string | null>(null)
  const seenChangeToken = useRef<string | null>(fileChangeToken)

  const userUrlApplies = userUrl.token === previewToken
  const urlInput = userUrlApplies && userUrl.draft !== null ? userUrl.draft : resolvedInitialUrl
  const committedUrl = userUrlApplies && userUrl.committed !== null ? userUrl.committed : resolvedInitialUrl
  const safeCommittedUrl = sanitizePreviewUrl(committedUrl)
  const userNavigated = userUrlApplies && userUrl.committed !== null

  const previewStarting = preview?.status === 'starting' && !userNavigated
  const previewFailed =
    preview?.status === 'failed' && !userNavigated && dismissedFailure !== preview.token
  const frameUrl =
    safeCommittedUrl && reload.base === safeCommittedUrl && reload.href && sameOrigin(reload.href, safeCommittedUrl)
      ? (sanitizePreviewUrl(reload.href) ?? safeCommittedUrl)
      : safeCommittedUrl
  const hmrDetected = preview?.hmr ?? isLikelyHmrUrl(safeCommittedUrl)
  const autoReload = autoReloadOverride ?? !hmrDetected
  const showFrame = Boolean(frameUrl) && !previewStarting && !previewFailed

  const currentTab: CodeWorkbenchTab = activeTab ?? internalTab

  const updateUserUrl = (patch: Partial<Omit<UserUrlState, 'token'>>) => {
    setUserUrl((current) => {
      const base = current.token === previewToken ? current : { token: previewToken, draft: null, committed: null }
      return { ...base, ...patch, token: previewToken }
    })
  }

  const handleTabSelect = (nextTab: CodeWorkbenchTab) => {
    setInternalTab(nextTab)
    onTabChange?.(nextTab)
  }

  const handleUrlInputChange = (rawValue: string) => {
    updateUserUrl({ draft: rawValue })
    const trimmed = rawValue.trim()
    if (!trimmed) {
      updateUserUrl({ draft: rawValue, committed: '' })
      setUrlError(null)
      return
    }
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed) && !/^https?:/i.test(trimmed)) {
      setUrlError(URL_ERROR_MESSAGE)
      return
    }
    const sanitized = sanitizePreviewUrl(rawValue)
    if (sanitized) {
      updateUserUrl({ draft: rawValue, committed: sanitized })
      setUrlError(null)
    }
  }

  const applyUrlInput = (): string | null => {
    const sanitized = sanitizePreviewUrl(urlInput)
    if (!sanitized) {
      setUrlError(URL_ERROR_MESSAGE)
      return null
    }
    setUrlError(null)
    updateUserUrl({ committed: sanitized })
    return sanitized
  }

  const handleUrlSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    applyUrlInput()
  }

  /** Reloads the frame keeping the page the person navigated to when the browser exposes it. */
  const reloadFrame = (base: string) => {
    const href = readFrameHref(frameRef.current) ?? lastLoadedHref.current
    setReload((current) => ({ count: current.count + 1, base, href }))
  }

  const handleReload = () => {
    if (!urlInput.trim()) {
      setReload((current) => ({ ...current, count: current.count + 1 }))
      return
    }
    const sanitized = applyUrlInput()
    if (sanitized) reloadFrame(sanitized)
  }

  const handleRetry = () => {
    if (preview) setDismissedFailure(preview.token)
    onRetryPreview?.()
    if (!onRetryPreview && !safeCommittedUrl) {
      void handleStartPreview()
    }
    if (safeCommittedUrl) reloadFrame(safeCommittedUrl)
  }

  const handleOpenExternal = () => {
    const sanitized = applyUrlInput()
    if (!sanitized || typeof window === 'undefined') return
    window.open(sanitized, '_blank', 'noopener,noreferrer')
  }

  // Auto-reload after the agent writes/edits files (debounced; skipped for HMR dev servers).
  useEffect(() => {
    if (fileChangeToken === seenChangeToken.current) return
    seenChangeToken.current = fileChangeToken
    if (!autoReload || !showFrame || !safeCommittedUrl) return
    const base = safeCommittedUrl
    const timer = window.setTimeout(() => {
      const href = readFrameHref(frameRef.current) ?? lastLoadedHref.current
      setReload((current) => ({ count: current.count + 1, base, href }))
    }, AUTO_RELOAD_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [autoReload, fileChangeToken, safeCommittedUrl, showFrame])

  const filteredFiles = useMemo(() => {
    const normalizedQuery = fileQuery.trim().toLowerCase()
    if (!normalizedQuery) return files
    return files.filter((item) => item.path.toLowerCase().includes(normalizedQuery))
  }, [fileQuery, files])

  const internalSelectedFile =
    userSelectedFile && userSelectedFile.baseProp === selectedFilePath
      ? userSelectedFile.path
      : (selectedFilePath ?? files[0]?.path ?? null)

  const resolvedSelectedPath =
    internalSelectedFile && files.some((item) => item.path === internalSelectedFile)
      ? internalSelectedFile
      : selectedFilePath && files.some((item) => item.path === selectedFilePath)
        ? selectedFilePath
        : (files[0]?.path ?? null)

  const selectedFile = useMemo(
    () => files.find((item) => item.path === resolvedSelectedPath) ?? filteredFiles[0] ?? null,
    [files, filteredFiles, resolvedSelectedPath],
  )

  const handleSelectFile = (path: string) => {
    setUserSelectedFile({ baseProp: selectedFilePath, path })
    onSelectFile?.(path)
  }

  const changedCount = useMemo(() => {
    const modifiedOrAdded = files.filter((item) => item.status !== 'inspected')
    return modifiedOrAdded.length
  }, [files])

  const terminalRunning = terminalEntries.some((entry) => entry.status === 'running')
  const selectedIcon = selectedFile ? fileIconFor(selectedFile.path.split('/').pop() ?? selectedFile.path) : null

  const tabClass = (tab: CodeWorkbenchTab) => (currentTab === tab ? 'code-workbench__tab is-active' : 'code-workbench__tab')

  return (
    <aside
      data-testid="code-workbench-panel"
      className="code-workbench"
      aria-label="Bancada de código e prévia"
    >
      <div
        role="tablist"
        aria-label="Abas da bancada"
        data-testid="code-workbench-tabs"
        className="code-workbench__tabs"
      >
        <button
          type="button"
          role="tab"
          data-testid="workbench-tab-preview"
          aria-selected={currentTab === 'preview'}
          className={tabClass('preview')}
          onClick={() => handleTabSelect('preview')}
        >
          <span>Prévia</span>
          {(showFrame || previewStarting) && (
            <span className={previewStarting ? 'code-workbench__tab-dot is-starting' : 'code-workbench__tab-dot is-live'} aria-hidden="true" />
          )}
        </button>
        <button
          type="button"
          role="tab"
          data-testid="workbench-tab-files"
          aria-selected={currentTab === 'files'}
          className={tabClass('files')}
          onClick={() => handleTabSelect('files')}
        >
          <span>Arquivos</span>
          {changedCount > 0 && <span className="code-workbench__tab-count">{changedCount}</span>}
        </button>
        <button
          type="button"
          role="tab"
          data-testid="workbench-tab-terminal"
          aria-selected={currentTab === 'terminal'}
          className={tabClass('terminal')}
          onClick={() => handleTabSelect('terminal')}
        >
          <span>Terminal</span>
          {terminalRunning && <span className="code-workbench__tab-dot is-starting" aria-hidden="true" />}
        </button>
        {onClose && (
          <button
            type="button"
            data-testid="code-workbench-close"
            aria-label="Recolher bancada"
            className="code-workbench__close-btn"
            onClick={onClose}
          >
            ×
          </button>
        )}
      </div>

      <div className={`code-workbench__body is-${currentTab}`}>
        {currentTab === 'preview' && (
          <div className="code-workbench__preview-pane" data-preview-status={previewStarting ? 'starting' : previewFailed ? 'failed' : showFrame ? 'running' : 'empty'}>
            <div className="code-workbench__toolbar">
              <button
                type="button"
                data-testid="workbench-preview-reload"
                className="code-workbench__icon-btn"
                aria-label="Recarregar prévia"
                title="Recarregar (mantém a página atual)"
                onClick={handleReload}
              >
                <ReloadIcon />
              </button>

              <form className="code-workbench__url-form" onSubmit={handleUrlSubmit}>
                {showFrame && <span className="code-workbench__url-live" aria-hidden="true" />}
                <input
                  type="text"
                  data-testid="workbench-preview-url-input"
                  className="code-workbench__url-input"
                  aria-label="Endereço da prévia"
                  placeholder="http://localhost:5173"
                  spellCheck={false}
                  value={urlInput}
                  onChange={(event) => handleUrlInputChange(event.target.value)}
                />
              </form>

              <button
                type="button"
                data-testid="workbench-preview-autoreload"
                className={autoReload ? 'code-workbench__chip-btn is-active' : 'code-workbench__chip-btn'}
                aria-pressed={autoReload}
                title={
                  hmrDetected
                    ? 'O servidor parece ter hot reload (HMR); a prévia já se atualiza sozinha.'
                    : 'Recarregar a prévia quando o agente alterar arquivos'
                }
                onClick={() => setAutoReloadOverride(!autoReload)}
              >
                Auto
              </button>

              <div
                data-testid="workbench-viewport-toggle"
                className="code-workbench__viewport-toggle"
                role="group"
                aria-label="Alternar tamanho da tela"
              >
                <button
                  type="button"
                  data-testid="workbench-viewport-desktop"
                  aria-label="Visualização desktop"
                  aria-pressed={viewport === 'desktop'}
                  className={viewport === 'desktop' ? 'code-workbench__viewport-btn is-active' : 'code-workbench__viewport-btn'}
                  onClick={() => setViewport('desktop')}
                >
                  <DesktopIcon />
                </button>
                <button
                  type="button"
                  data-testid="workbench-viewport-mobile"
                  aria-label="Visualização mobile"
                  aria-pressed={viewport === 'mobile'}
                  className={viewport === 'mobile' ? 'code-workbench__viewport-btn is-active' : 'code-workbench__viewport-btn'}
                  onClick={() => setViewport('mobile')}
                >
                  <MobileIcon />
                </button>
              </div>

              <button
                type="button"
                data-testid="workbench-preview-open-external"
                className="code-workbench__icon-btn"
                aria-label="Abrir prévia em nova aba"
                title="Abrir em nova aba"
                onClick={handleOpenExternal}
              >
                <ExternalIcon />
              </button>
            </div>

            {urlError && (
              <div
                role="alert"
                data-testid="workbench-preview-url-error"
                className="code-workbench__url-error"
              >
                {urlError}
              </div>
            )}

            <div
              data-testid="workbench-preview-stage"
              data-viewport={viewport}
              data-reload-count={reload.count}
              data-preview-url={committedUrl}
              data-auto-reload={autoReload ? 'on' : 'off'}
              className={viewport === 'mobile' ? 'code-workbench__stage is-mobile' : 'code-workbench__stage is-desktop'}
            >
              {previewStarting ? (
                <div className="code-workbench__preview-state is-starting" data-testid="workbench-preview-starting" role="status">
                  <span className="code-workbench__spinner" aria-hidden="true" />
                  <strong>Iniciando servidor de prévia{preview?.name ? ` · ${preview.name}` : ''}…</strong>
                  <span>A prévia abre aqui assim que a porta responder.</span>
                </div>
              ) : previewFailed ? (
                <div className="code-workbench__preview-state is-failed" data-testid="workbench-preview-failed" role="alert">
                  <strong>Não foi possível abrir a prévia{preview?.name ? ` · ${preview.name}` : ''}</strong>
                  <pre className="code-workbench__preview-error">{preview?.error || 'Falha ao iniciar o servidor.'}</pre>
                  <button type="button" className="code-workbench__retry-btn" data-testid="workbench-preview-retry" onClick={handleRetry}>
                    Tentar novamente
                  </button>
                </div>
              ) : showFrame && frameUrl ? (
                <iframe
                  key={`${safeCommittedUrl}-${reload.count}`}
                  ref={frameRef}
                  src={frameUrl}
                  title="Prévia da aplicação"
                  className="code-workbench__preview-iframe"
                  data-testid="workbench-preview-iframe"
                  sandbox="allow-scripts allow-forms allow-same-origin"
                  onLoad={() => {
                    lastLoadedHref.current = readFrameHref(frameRef.current)
                  }}
                />
              ) : (
                <div className="code-workbench__preview-empty" data-testid="workbench-preview-empty">
                  <strong>Nenhuma prévia ativa</strong>
                  <span>
                    Peça ao agente para mostrar o app: ele inicia o servidor de desenvolvimento e abre a prévia aqui
                    automaticamente. Você também pode digitar uma URL http(s) acima.
                  <button
                    type="button"
                    className="code-workbench__retry-btn code-workbench__start-btn"
                    data-testid="workbench-preview-start"
                    onClick={() => {
                      void handleStartPreview()
                    }}
                  >
                    Iniciar prévia
                  </button>
                  </span>
                </div>
              )}
            </div>
          </div>
        )}

        {currentTab === 'files' && (
          <div data-testid="workbench-files-view" className="code-workbench__files-pane">
            <div className="code-workbench__files-sidebar">
              <WorkbenchFileTree
                files={files}
                query={fileQuery}
                selectedPath={selectedFile?.path ?? null}
                onOpenFile={handleSelectFile}
                rootName={rootName}
                onRefresh={onRefreshFiles}
                describeFile={describeFile}
                emptyMessage={files.length === 0 ? 'Nenhum arquivo alterado ainda.' : 'Nenhum arquivo encontrado para o filtro.'}
                filterSlot={
                  <div className="code-workbench__files-search-wrap">
                    <input
                      type="search"
                      data-testid="workbench-files-search"
                      className="code-workbench__files-search"
                      placeholder="Filtrar arquivos..."
                      aria-label="Filtrar arquivos no workbench"
                      value={fileQuery}
                      onChange={(event) => setFileQuery(event.target.value)}
                    />
                  </div>
                }
              />
            </div>

            <div data-testid="workbench-file-detail" className="code-workbench__file-detail">
              {selectedFile ? (
                <>
                  <header className="code-workbench__file-detail-header">
                    {selectedIcon && (
                      <span className={`wb-file-icon is-${selectedIcon.tone}`} aria-hidden="true">
                        {selectedIcon.label}
                      </span>
                    )}
                    <strong className="code-workbench__file-detail-path" title={selectedFile.path}>
                      <span className="code-workbench__file-detail-prefix">{'Diff ·\u00a0'}</span>
                      {selectedFile.path.includes('/') && (
                        <span className="code-workbench__file-detail-dir">
                          {selectedFile.path.slice(0, selectedFile.path.lastIndexOf('/') + 1)}
                        </span>
                      )}
                      <span className="code-workbench__file-detail-base">
                        {selectedFile.path.slice(selectedFile.path.lastIndexOf('/') + 1)}
                      </span>
                    </strong>
                    {(selectedFile.addedLines !== undefined || selectedFile.removedLines !== undefined) && (
                      <span className="code-workbench__file-stats">
                        <span className="workspace-diff__stat-add">+{selectedFile.addedLines ?? 0}</span>{' '}
                        <span className="workspace-diff__stat-del">-{selectedFile.removedLines ?? 0}</span>
                      </span>
                    )}
                    <span className={`code-workbench__file-badge is-${selectedFile.status ?? 'modified'}`}>
                      {statusLabelForFile(selectedFile.status)}
                    </span>
                  </header>
                  <div className="code-workbench__file-detail-body">
                    {selectedFile.structuredPatch && selectedFile.structuredPatch.length > 0 ? (
                      <StructuredDiffView hunks={selectedFile.structuredPatch} />
                    ) : (
                      <p className="code-workbench__empty">
                        Sem diff estruturado disponível para este arquivo.
                      </p>
                    )}
                  </div>
                </>
              ) : (
                <p className="code-workbench__empty">
                  Selecione um arquivo para inspecionar as alterações.
                </p>
              )}
            </div>
          </div>
        )}

        {currentTab === 'terminal' && (
          <div data-testid="workbench-terminal-view" className="code-workbench__terminal-pane">
            {terminalEntries.length === 0 ? (
              <p className="code-workbench__empty">Nenhum comando executado ainda.</p>
            ) : (
              <div className="code-workbench__terminal-list">
                {terminalEntries.map((entry) => (
                  <article
                    key={entry.id}
                    data-testid={`workbench-terminal-entry-${entry.id}`}
                    className="code-workbench__terminal-entry"
                  >
                    <header className="code-workbench__terminal-header">
                      <code className="code-workbench__terminal-cmd" title={entry.command}>$ {entry.command}</code>
                      <span
                        className={`code-workbench__terminal-status is-${entry.status ?? 'completed'}`}
                      >
                        {statusLabelForTerminal(entry.status)}
                      </span>
                    </header>
                    {entry.output && (
                      <pre className="code-workbench__terminal-output">{entry.output}</pre>
                    )}
                  </article>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <footer data-testid="workbench-status-footer" className="code-workbench__footer">
        <div data-testid="workbench-status-indicator" className="code-workbench__status">
          <span
            className={
              running
                ? 'code-workbench__status-dot is-running'
                : 'code-workbench__status-dot is-ready'
            }
            aria-hidden="true"
          />
          <span>
            {running ? 'Em andamento' : 'Pronto'}
            {elapsedLabel ? ` · ${elapsedLabel}` : ''}
          </span>
        </div>

        <button
          type="button"
          data-testid="workbench-footer-files-button"
          className="code-workbench__footer-files-btn"
          onClick={() => handleTabSelect('files')}
        >
          <span>
            {changedCount} {changedCount === 1 ? 'arquivo alterado' : 'arquivos alterados'}
          </span>
        </button>
      </footer>
    </aside>
  )
}
