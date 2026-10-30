import { useMemo, useState, type FormEvent } from 'react'
import type { StructuredPatchHunk } from './activityTypes'
import { StructuredDiffView } from './WorkspaceFileCard'

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
  running?: boolean
  elapsedLabel?: string | null
  selectedFilePath?: string | null
  onSelectFile?: (path: string | null) => void
}

const URL_ERROR_MESSAGE = 'URL inválida. Use http:// ou https://.'

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

export function CodeWorkbenchPanel({
  files,
  terminalEntries = [],
  activeTab,
  onTabChange,
  initialPreviewUrl,
  running = false,
  elapsedLabel = null,
  selectedFilePath,
  onSelectFile,
}: CodeWorkbenchPanelProps) {
  const resolvedInitialUrl = useMemo(() => {
    const fromProp = initialPreviewUrl ? sanitizePreviewUrl(initialPreviewUrl) : null
    if (fromProp) return fromProp
    if (!initialPreviewUrl) {
      const fromTerminal = detectPreviewUrlFromTerminal(terminalEntries)
      if (fromTerminal) return fromTerminal
    }
    return ''
  }, [initialPreviewUrl, terminalEntries])

  const [internalTab, setInternalTab] = useState<CodeWorkbenchTab>(activeTab ?? 'preview')
  const [userUrlDraft, setUserUrlDraft] = useState<string | null>(null)
  const [userCommittedUrl, setUserCommittedUrl] = useState<string | null>(null)
  const [urlError, setUrlError] = useState<string | null>(null)
  const [viewport, setViewport] = useState<'desktop' | 'mobile'>('desktop')
  const [reloadCount, setReloadCount] = useState<number>(0)
  const [fileQuery, setFileQuery] = useState<string>('')
  const [userSelectedFile, setUserSelectedFile] = useState<{
    baseProp: string | null | undefined
    path: string
  } | null>(null)

  const urlInput = userUrlDraft ?? resolvedInitialUrl
  const committedUrl = userCommittedUrl ?? resolvedInitialUrl

  const currentTab: CodeWorkbenchTab = activeTab ?? internalTab

  const handleTabSelect = (nextTab: CodeWorkbenchTab) => {
    setInternalTab(nextTab)
    onTabChange?.(nextTab)
  }

  const handleUrlInputChange = (rawValue: string) => {
    setUserUrlDraft(rawValue)
    const trimmed = rawValue.trim()
    if (!trimmed) {
      setUserCommittedUrl('')
      setUrlError(null)
      return
    }
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed) && !/^https?:/i.test(trimmed)) {
      setUrlError(URL_ERROR_MESSAGE)
      return
    }
    const sanitized = sanitizePreviewUrl(rawValue)
    if (sanitized) {
      setUserCommittedUrl(sanitized)
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
    setUserCommittedUrl(sanitized)
    return sanitized
  }

  const handleUrlSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    applyUrlInput()
  }

  const handleReload = () => {
    if (!urlInput.trim()) {
      setReloadCount((count) => count + 1)
      return
    }
    const sanitized = applyUrlInput()
    if (sanitized) {
      setReloadCount((count) => count + 1)
    }
  }

  const handleOpenExternal = () => {
    const sanitized = applyUrlInput()
    if (!sanitized || typeof window === 'undefined') return
    window.open(sanitized, '_blank', 'noopener,noreferrer')
  }

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
    return modifiedOrAdded.length > 0 ? modifiedOrAdded.length : files.length
  }, [files])

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
          className={currentTab === 'preview' ? 'code-workbench__tab is-active' : 'code-workbench__tab'}
          onClick={() => handleTabSelect('preview')}
        >
          Prévia
        </button>
        <button
          type="button"
          role="tab"
          data-testid="workbench-tab-files"
          aria-selected={currentTab === 'files'}
          className={currentTab === 'files' ? 'code-workbench__tab is-active' : 'code-workbench__tab'}
          onClick={() => handleTabSelect('files')}
        >
          Arquivos
        </button>
        <button
          type="button"
          role="tab"
          data-testid="workbench-tab-terminal"
          aria-selected={currentTab === 'terminal'}
          className={currentTab === 'terminal' ? 'code-workbench__tab is-active' : 'code-workbench__tab'}
          onClick={() => handleTabSelect('terminal')}
        >
          Terminal
        </button>
      </div>

      <div className="code-workbench__body">
        {currentTab === 'preview' && (
          <div className="code-workbench__preview-pane">
            <div className="code-workbench__toolbar">
              <div className="code-workbench__nav-actions">
                <button
                  type="button"
                  data-testid="workbench-preview-back"
                  className="code-workbench__icon-btn"
                  aria-label="Voltar na prévia"
                >
                  ←
                </button>
                <button
                  type="button"
                  data-testid="workbench-preview-forward"
                  className="code-workbench__icon-btn"
                  aria-label="Avançar na prévia"
                >
                  →
                </button>
                <button
                  type="button"
                  data-testid="workbench-preview-reload"
                  className="code-workbench__icon-btn"
                  aria-label="Recarregar prévia"
                  onClick={handleReload}
                >
                  ↻
                </button>
              </div>

              <form className="code-workbench__url-form" onSubmit={handleUrlSubmit}>
                <input
                  type="text"
                  data-testid="workbench-preview-url-input"
                  className="code-workbench__url-input"
                  aria-label="Endereço da prévia"
                  value={urlInput}
                  onChange={(event) => handleUrlInputChange(event.target.value)}
                />
              </form>

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
                  className={
                    viewport === 'desktop'
                      ? 'code-workbench__viewport-btn is-active'
                      : 'code-workbench__viewport-btn'
                  }
                  onClick={() => setViewport('desktop')}
                >
                  🖥
                </button>
                <button
                  type="button"
                  data-testid="workbench-viewport-mobile"
                  aria-label="Visualização mobile"
                  aria-pressed={viewport === 'mobile'}
                  className={
                    viewport === 'mobile'
                      ? 'code-workbench__viewport-btn is-active'
                      : 'code-workbench__viewport-btn'
                  }
                  onClick={() => setViewport('mobile')}
                >
                  📱
                </button>
              </div>

              <button
                type="button"
                data-testid="workbench-preview-open-external"
                className="code-workbench__icon-btn"
                aria-label="Abrir prévia em nova aba"
                onClick={handleOpenExternal}
              >
                ↗
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
              data-reload-count={reloadCount}
              data-preview-url={committedUrl}
              className={
                viewport === 'mobile'
                  ? 'code-workbench__stage is-mobile'
                  : 'code-workbench__stage is-desktop'
              }
            >
              {sanitizePreviewUrl(committedUrl) ? (
                <iframe
                  key={`${committedUrl}-${reloadCount}`}
                  src={sanitizePreviewUrl(committedUrl)!}
                  title="Prévia da aplicação"
                  className="code-workbench__preview-iframe"
                  data-testid="workbench-preview-iframe"
                  sandbox="allow-scripts allow-forms allow-same-origin"
                />
              ) : (
                <div
                  className="code-workbench__preview-empty"
                  data-testid="workbench-preview-empty"
                >
                  Nenhuma prévia ativa no momento. Informe uma URL http(s) acima ou inicie um servidor local no terminal.
                </div>
              )}
            </div>
          </div>
        )}

        {currentTab === 'files' && (
          <div data-testid="workbench-files-view" className="code-workbench__files-pane">
            <div className="code-workbench__files-sidebar">
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

              {filteredFiles.length === 0 ? (
                <p className="code-workbench__empty">
                  {files.length === 0
                    ? 'Nenhum arquivo alterado ainda.'
                    : 'Nenhum arquivo encontrado para o filtro.'}
                </p>
              ) : (
                <ul className="code-workbench__file-list">
                  {filteredFiles.map((file) => {
                    const isSelected = selectedFile?.path === file.path
                    return (
                      <li key={file.path}>
                        <button
                          type="button"
                          data-testid={`workbench-file-item-${file.path}`}
                          className={
                            isSelected
                              ? 'code-workbench__file-item is-selected'
                              : 'code-workbench__file-item'
                          }
                          onClick={() => handleSelectFile(file.path)}
                        >
                          <span className="code-workbench__file-path">{file.path}</span>
                          <span className="code-workbench__file-meta">
                            <span
                              className={`code-workbench__file-badge is-${file.status ?? 'modified'}`}
                            >
                              {statusLabelForFile(file.status)}
                            </span>
                            {(file.addedLines !== undefined || file.removedLines !== undefined) && (
                              <span className="code-workbench__file-stats">
                                <span className="workspace-diff__stat-add">
                                  +{file.addedLines ?? 0}
                                </span>{' '}
                                <span className="workspace-diff__stat-del">
                                  -{file.removedLines ?? 0}
                                </span>
                              </span>
                            )}
                          </span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>

            <div data-testid="workbench-file-detail" className="code-workbench__file-detail">
              {selectedFile ? (
                <>
                  <header className="code-workbench__file-detail-header">
                    <strong>{`Diff · ${selectedFile.path}`}</strong>
                    <span className="code-workbench__file-badge">
                      {statusLabelForFile(selectedFile.status)}
                    </span>
                  </header>
                  {selectedFile.structuredPatch && selectedFile.structuredPatch.length > 0 ? (
                    <StructuredDiffView hunks={selectedFile.structuredPatch} />
                  ) : (
                    <p className="code-workbench__empty">
                      Sem diff estruturado disponível para este arquivo.
                    </p>
                  )}
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
                      <code className="code-workbench__terminal-cmd">$ {entry.command}</code>
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
          <span aria-hidden="true">📄</span>
          <span>
            {changedCount} {changedCount === 1 ? 'arquivo alterado' : 'arquivos alterados'}
          </span>
          <span aria-hidden="true">▾</span>
        </button>
      </footer>
    </aside>
  )
}
