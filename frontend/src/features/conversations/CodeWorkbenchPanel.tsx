import { useEffect, useMemo, useState, type FormEvent } from 'react'
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

const DEFAULT_PREVIEW_URL = 'http://localhost:3000/trilhas/ferramentas-do-hype'
const URL_ERROR_MESSAGE = 'URL inválida. Use http:// ou https://.'

export function sanitizePreviewUrl(raw: string): string | null {
  if (typeof raw !== 'string') return null
  const trimmed = raw.trim()
  if (!trimmed || /[\u0000-\u001F\u007F]/.test(trimmed)) return null
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

const AURYLY_LESSONS = [
  {
    number: 1,
    title: 'Panorama das ferramentas',
    description: 'Conheça o ecossistema e escolha as ferramentas certas para o seu projeto.',
    duration: '12 min',
  },
  {
    number: 2,
    title: 'Do prompt ao protótipo',
    description: 'Aprenda a criar, iterar e validar ideias de forma prática.',
    duration: '18 min',
  },
  {
    number: 3,
    title: 'Projeto prático: seu primeiro app',
    description: 'Mão na massa com um projeto completo, do zero ao ar.',
    duration: '25 min',
  },
] as const

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
  const resolvedInitialUrl = useMemo(
    () => (initialPreviewUrl ? sanitizePreviewUrl(initialPreviewUrl) : null) ?? DEFAULT_PREVIEW_URL,
    [initialPreviewUrl],
  )

  const [internalTab, setInternalTab] = useState<CodeWorkbenchTab>(activeTab ?? 'preview')
  const [urlInput, setUrlInput] = useState<string>(resolvedInitialUrl)
  const [committedUrl, setCommittedUrl] = useState<string>(resolvedInitialUrl)
  const [urlError, setUrlError] = useState<string | null>(null)
  const [viewport, setViewport] = useState<'desktop' | 'mobile'>('desktop')
  const [reloadCount, setReloadCount] = useState<number>(0)
  const [fileQuery, setFileQuery] = useState<string>('')
  const [internalSelectedFile, setInternalSelectedFile] = useState<string | null>(
    selectedFilePath ?? files[0]?.path ?? null,
  )

  useEffect(() => {
    const sanitized = initialPreviewUrl ? sanitizePreviewUrl(initialPreviewUrl) : null
    if (sanitized) {
      setUrlInput(sanitized)
      setCommittedUrl(sanitized)
      setUrlError(null)
    }
  }, [initialPreviewUrl])

  const currentTab: CodeWorkbenchTab = activeTab ?? internalTab

  const handleTabSelect = (nextTab: CodeWorkbenchTab) => {
    setInternalTab(nextTab)
    onTabChange?.(nextTab)
  }

  const handleUrlInputChange = (rawValue: string) => {
    setUrlInput(rawValue)
    const trimmed = rawValue.trim()
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed) && !/^https?:/i.test(trimmed)) {
      setUrlError(URL_ERROR_MESSAGE)
      return
    }
    const sanitized = sanitizePreviewUrl(rawValue)
    if (sanitized) {
      setCommittedUrl(sanitized)
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
    setCommittedUrl(sanitized)
    return sanitized
  }

  const handleUrlSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    applyUrlInput()
  }

  const handleReload = () => {
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

  useEffect(() => {
    if (selectedFilePath) {
      setInternalSelectedFile(selectedFilePath)
    }
  }, [selectedFilePath])

  const filteredFiles = useMemo(() => {
    const normalizedQuery = fileQuery.trim().toLowerCase()
    if (!normalizedQuery) return files
    return files.filter((item) => item.path.toLowerCase().includes(normalizedQuery))
  }, [fileQuery, files])

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
    setInternalSelectedFile(path)
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
              <div className="auryly-preview">
                <header className="auryly-preview__nav">
                  <div className="auryly-preview__brand">
                    <span className="auryly-preview__logo" aria-hidden="true">
                      ✦
                    </span>
                    <strong className="auryly-preview__brand-name">Auryly</strong>
                  </div>
                  <nav className="auryly-preview__links" aria-label="Navegação da prévia">
                    <span>Trilhas</span>
                    <span>Comunidade</span>
                    <span>Preços</span>
                    <span className="auryly-preview__avatar" aria-hidden="true">
                      👤
                    </span>
                  </nav>
                </header>

                <div className="auryly-preview__hero">
                  <div className="auryly-preview__hero-copy">
                    <span className="auryly-preview__pill">Trilha de aprendizado</span>
                    <div className="auryly-preview__title">
                      Transforme ideias em projetos com IA
                    </div>
                    <p className="auryly-preview__lead">
                      Aprenda a usar as principais ferramentas do ecossistema Hype para tirar suas
                      ideias do papel, com prática, exemplos reais e projetos guiados.
                    </p>
                    <div className="auryly-preview__actions">
                      <span className="auryly-preview__cta-primary">Explorar a trilha →</span>
                      <span className="auryly-preview__cta-secondary">▶ Assistir ao trailer</span>
                    </div>
                  </div>

                  <div className="auryly-preview__hero-art" aria-hidden="true">
                    <div className="auryly-preview__art-card">
                      <span className="auryly-preview__art-tile">&gt;_</span>
                      <span className="auryly-preview__art-tile">❃</span>
                      <span className="auryly-preview__art-tile">▲</span>
                      <span className="auryly-preview__art-tile">N</span>
                    </div>
                  </div>
                </div>

                <div className="auryly-preview__curriculum">
                  <div className="auryly-preview__curriculum-header">
                    <strong className="auryly-preview__curriculum-title">
                      O que você vai aprender
                    </strong>
                    <span className="auryly-preview__curriculum-count">3 aulas</span>
                  </div>

                  <ol className="auryly-preview__lessons">
                    {AURYLY_LESSONS.map((lesson) => (
                      <li key={lesson.number} className="auryly-preview__lesson">
                        <span className="auryly-preview__lesson-number">{lesson.number}</span>
                        <div className="auryly-preview__lesson-body">
                          <strong className="auryly-preview__lesson-title">{lesson.title}</strong>
                          <p className="auryly-preview__lesson-desc">{lesson.description}</p>
                        </div>
                        <span className="auryly-preview__lesson-duration">{lesson.duration}</span>
                        <span className="auryly-preview__lesson-play" aria-hidden="true">
                          ▶
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              </div>
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
