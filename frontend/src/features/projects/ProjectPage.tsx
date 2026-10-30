import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { type ApiClient, createBrowserApiClient } from '../../api/client'
import { ensureFriendConversation } from '../../api/conversations'
import {
  attachProjectWorkspace,
  detachProjectWorkspace,
  listProjectMemories,
  listProjectSidebar,
  type ProjectChat,
  type ProjectSidebarItem,
} from '../../api/projects'
import { inspectNewWorkspaceFolder } from '../../api/workspace'
import { OrinMascot } from '../../components/OrinMascot'
import { WorkspaceNavigation } from './WorkspaceNavigation'

export type ProjectPageProps = {
  client?: ApiClient
}

export type ProjectDashboardTab = 'conversations' | 'files' | 'instructions' | 'memory'
export type ProjectComposerMode = 'auto' | 'chat' | 'code'

const DEFAULT_PROJECT_INSTRUCTIONS = [
  'Manter tom objetivo, claro e focado no contexto do projeto.',
  'Preservar a organização dos arquivos e padrões definidos na pasta vinculada.',
  'Registrar decisões importantes na memória compartilhada do projeto.',
]

const ACTIVE_CHAT_STATES = new Set(['running', 'working', 'streaming', 'in_progress'])

function instructionsStorageKey(projectId: string): string {
  return `orin.project.instructions.v1.${projectId}`
}

export function readProjectInstructions(projectId: string): string[] {
  if (!projectId) return [...DEFAULT_PROJECT_INSTRUCTIONS]
  try {
    if (typeof window === 'undefined' || !window.localStorage) {
      return [...DEFAULT_PROJECT_INSTRUCTIONS]
    }
    const raw = window.localStorage.getItem(instructionsStorageKey(projectId))
    if (!raw) return [...DEFAULT_PROJECT_INSTRUCTIONS]
    const parsed: unknown = JSON.parse(raw)
    if (Array.isArray(parsed) && parsed.every((item): item is string => typeof item === 'string')) {
      return parsed
    }
    return [...DEFAULT_PROJECT_INSTRUCTIONS]
  } catch {
    return [...DEFAULT_PROJECT_INSTRUCTIONS]
  }
}

export function writeProjectInstructions(projectId: string, items: string[]): void {
  if (!projectId) return
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(instructionsStorageKey(projectId), JSON.stringify(items))
    }
  } catch {
    // Ignore storage failures in restricted contexts
  }
}

function formatChatRelativeTime(chat: ProjectChat): string {
  if (chat.updated_at) {
    const parsed = Date.parse(chat.updated_at)
    if (Number.isFinite(parsed)) {
      const diffMs = Math.max(0, Date.now() - parsed)
      const mins = Math.floor(diffMs / 60000)
      if (mins < 1) return 'agora'
      if (mins < 60) return `há ${mins} min`
      const hours = Math.floor(mins / 60)
      if (hours < 24) return `há ${hours} ${hours === 1 ? 'hora' : 'horas'}`
      const days = Math.floor(hours / 24)
      return `há ${days} ${days === 1 ? 'dia' : 'dias'}`
    }
  }
  return 'Recente'
}

function summarizeProjectChat(chat: ProjectChat): string {
  return chat.mode === 'code' ? 'Sessão do Orin Code' : 'Conversa do projeto'
}

export function ProjectPage({ client }: ProjectPageProps) {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [project, setProject] = useState<ProjectSidebarItem | null>(null)
  const [memories, setMemories] = useState<Array<{ memory_id: string; fact: string }>>([])
  const [error, setError] = useState(false)
  const [wsPath, setWsPath] = useState('')
  const [wsBusy, setWsBusy] = useState(false)
  const [wsError, setWsError] = useState<string | null>(null)

  const [activeTab, setActiveTab] = useState<ProjectDashboardTab>('conversations')
  const [composerPrompt, setComposerPrompt] = useState('')
  const [composerMode, setComposerMode] = useState<ProjectComposerMode>('auto')
  const [chatSearchQuery, setChatSearchQuery] = useState('')
  const [instructions, setInstructions] = useState<string[]>(() => readProjectInstructions(projectId))
  const [newInstructionText, setNewInstructionText] = useState('')
  const wsInputRef = useRef<HTMLInputElement | null>(null)

  const [prevProjectId, setPrevProjectId] = useState(projectId)
  if (projectId !== prevProjectId) {
    setPrevProjectId(projectId)
    setInstructions(readProjectInstructions(projectId))
  }

  const load = useCallback(async () => {
    try {
      const [sidebar, memory] = await Promise.all([
        listProjectSidebar(apiClient),
        listProjectMemories(apiClient, projectId),
      ])
      const found = sidebar.items.find((item) => item.project_id === projectId) ?? null
      setProject(found)
      setWsPath(found?.workspace?.path ?? '')
      setMemories(memory.items)
    } catch {
      setError(true)
    }
  }, [apiClient, projectId])

  useEffect(() => {
    queueMicrotask(() => {
      void load()
    })
  }, [load])

  const handlePickWorkspace = async () => {
    if (wsBusy) return
    setWsBusy(true)
    setWsError(null)
    try {
      const outcome = await inspectNewWorkspaceFolder(apiClient, null, projectId)
      if (outcome.kind === 'cancelled') return
      if (outcome.kind === 'unavailable') {
        setWsError('Não foi possível abrir o seletor do sistema. Cole o caminho abaixo.')
        return
      }
      setWsPath(outcome.path)
    } catch {
      setWsError('Não foi possível selecionar a pasta.')
    } finally {
      setWsBusy(false)
    }
  }

  const handleSaveWorkspace = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!wsPath.trim() || wsBusy) return
    setWsBusy(true)
    setWsError(null)
    try {
      const nextWs = await attachProjectWorkspace(apiClient, projectId, wsPath.trim(), true)
      setProject((cur) => (cur ? { ...cur, workspace: nextWs } : cur))
      setWsPath(nextWs.path ?? '')
    } catch {
      setWsError('Não foi possível vincular a pasta ao projeto.')
    } finally {
      setWsBusy(false)
    }
  }

  const handleDetachWorkspace = async () => {
    if (wsBusy) return
    setWsBusy(true)
    setWsError(null)
    try {
      const nextWs = await detachProjectWorkspace(apiClient, projectId)
      setProject((cur) => (cur ? { ...cur, workspace: nextWs } : cur))
      setWsPath('')
    } catch {
      setWsError('Não foi possível desvincular a pasta.')
    } finally {
      setWsBusy(false)
    }
  }

  const handleComposerSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const targetMode = composerMode === 'code' ? 'code' : 'chat'
    const trimmedPrompt = composerPrompt.trim()
    const query = trimmedPrompt
      ? `?mode=${targetMode}&prompt=${encodeURIComponent(trimmedPrompt)}`
      : `?mode=${targetMode}`
    navigate(`/projects/${encodeURIComponent(projectId)}/new${query}`)
  }

  const handleAddInstruction = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const trimmed = newInstructionText.trim()
    if (!trimmed) return
    const next = [...instructions, trimmed]
    setInstructions(next)
    writeProjectInstructions(projectId, next)
    setNewInstructionText('')
  }

  const handleRemoveInstruction = (indexToRemove: number) => {
    const next = instructions.filter((_, idx) => idx !== indexToRemove)
    setInstructions(next)
    writeProjectInstructions(projectId, next)
  }

  const filteredChats = useMemo(() => {
    if (!project) return []
    const q = chatSearchQuery.trim().toLowerCase()
    if (!q) return project.chats
    return project.chats.filter((chat) => chat.title.toLowerCase().includes(q))
  }, [chatSearchQuery, project])

  if (error || !project) {
    return (
      <div className="project-dashboard-layout workspace-shell">
        <aside className="workspace-navigation">
          <WorkspaceNavigation client={apiClient} initialView="projects" hideProjectChatSublist={true} />
        </aside>
        <main className="project-page project-page--missing">
          <Link className="project-page__back" to="/">← Orin</Link>
          <p>Projeto não encontrado.</p>
        </main>
      </div>
    )
  }

  const workspaceDisplayLabel =
    project.workspace?.kind === 'local'
      ? project.workspace.path || project.workspace.folderName || 'Workspace gerenciado'
      : 'Workspace gerenciado'
  const workspaceStatusLabel = project.workspace?.path
    ? 'Pasta local vinculada'
    : 'Sem pasta vinculada'

  return (
    <div className="project-dashboard-layout workspace-shell">
      <aside className="workspace-navigation">
        <WorkspaceNavigation client={apiClient} initialView="projects" hideProjectChatSublist={true} />
      </aside>

      <main className="project-page project-dashboard">
        <header className="project-page__bar canvas-header" data-testid="canvas-header">
          <div className="canvas-header__left">
            <nav className="project-dashboard__breadcrumb" aria-label="Breadcrumb" data-testid="project-breadcrumb">
              <Link to="/projects" className="project-dashboard__breadcrumb-link">
                Projetos
              </Link>
              <span className="project-dashboard__breadcrumb-sep" aria-hidden="true">/</span>
              <strong className="project-dashboard__breadcrumb-current">{project.name}</strong>
            </nav>
          </div>
          <div className="canvas-header__center" />
          <div className="canvas-header__right">
            <button
              type="button"
              className="canvas-header__voice-btn"
              data-testid="topbar-voice-button"
              onClick={() => {
                window.dispatchEvent(new CustomEvent('orin:open-friend-voice'))
                void ensureFriendConversation(apiClient)
                  .then((conv) => navigate(`/chats/${encodeURIComponent(conv.conversation_id)}`))
                  .catch(() => navigate('/?mode=friend'))
              }}
            >
              <svg
                className="canvas-header__voice-icon"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <path d="M3 6.5v3M6.3 4v8M9.7 5.2v5.6M13 6.8v2.4" />
              </svg>
              <span>Conversar por voz</span>
            </button>
            <OrinMascot size={30} variant="flat" statusDot="online" ariaHidden />
          </div>
        </header>

        <div className="project-dashboard__container">
          <section className="project-page__hero project-dashboard__hero">
            <div className="project-dashboard__hero-main">
              <span className="project-dashboard__hero-folder" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M3 6.25A2.25 2.25 0 0 1 5.25 4h4.1c.6 0 1.17.24 1.59.66l1.4 1.4c.28.28.66.44 1.06.44h5.35A2.25 2.25 0 0 1 21 8.75v9A2.25 2.25 0 0 1 18.75 20H5.25A2.25 2.25 0 0 1 3 17.75v-11.5Z" />
                </svg>
              </span>
              <div className="project-dashboard__hero-text">
                <h1>{project.name}</h1>
                <p>{project.description || 'Sem descrição definida.'}</p>
              </div>
            </div>

            <div className="project-dashboard__hero-actions">
              <button
                type="button"
                className="project-dashboard__settings-btn ghost-button"
                data-testid="project-settings-button"
                onClick={() => {
                  setActiveTab('conversations')
                  queueMicrotask(() => {
                    wsInputRef.current?.focus()
                  })
                }}
              >
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                  <circle cx="8" cy="8" r="2.25" />
                  <path
                    d="M8 2.25v1.5M8 12.25v1.5M2.25 8h1.5M12.25 8h1.5M3.93 3.93l1.06 1.06M11.01 11.01l1.06 1.06M12.07 3.93l-1.06 1.06M4.99 11.01l-1.06 1.06"
                    strokeLinecap="round"
                  />
                </svg>
                <span>Configurações do projeto</span>
              </button>

              <div className="project-page__quick-actions">
                <Link
                  className="primary-button"
                  to={`/projects/${encodeURIComponent(projectId)}/new?mode=chat`}
                  data-testid="project-new-chat-button"
                >
                  + Novo Chat
                </Link>
                <Link
                  className="ghost-button"
                  to={`/projects/${encodeURIComponent(projectId)}/new?mode=code`}
                  data-testid="project-new-code-button"
                >
                  &gt;_ Novo Code
                </Link>
              </div>
            </div>
          </section>

          <div
            className="project-dashboard__tabs"
            role="tablist"
            aria-label="Seções do projeto"
            data-testid="project-tabs"
          >
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'conversations'}
              className={`project-dashboard__tab${activeTab === 'conversations' ? ' is-active' : ''}`}
              data-testid="project-tab-conversations"
              onClick={() => setActiveTab('conversations')}
            >
              Conversas
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'files'}
              className={`project-dashboard__tab${activeTab === 'files' ? ' is-active' : ''}`}
              data-testid="project-tab-files"
              onClick={() => setActiveTab('files')}
            >
              Arquivos
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'instructions'}
              className={`project-dashboard__tab${activeTab === 'instructions' ? ' is-active' : ''}`}
              data-testid="project-tab-instructions"
              onClick={() => setActiveTab('instructions')}
            >
              Instruções
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'memory'}
              className={`project-dashboard__tab${activeTab === 'memory' ? ' is-active' : ''}`}
              data-testid="project-tab-memory"
              onClick={() => setActiveTab('memory')}
            >
              Memória
            </button>
          </div>

          <div className="project-dashboard__grid">
            <div className="project-dashboard__main-col">
              {activeTab === 'conversations' && (
                <>
                  <form
                    className="project-dashboard__composer"
                    data-testid="project-inline-composer"
                    onSubmit={handleComposerSubmit}
                  >
                    <textarea
                      className="project-dashboard__composer-input"
                      data-testid="project-composer-input"
                      placeholder="Nova conversa neste projeto..."
                      aria-label="Nova conversa neste projeto"
                      rows={2}
                      value={composerPrompt}
                      onChange={(e) => setComposerPrompt(e.target.value)}
                    />
                    <div className="project-dashboard__composer-bar">
                      <div className="project-dashboard__composer-left">
                        <button
                          type="button"
                          className="project-dashboard__composer-icon-btn"
                          aria-label="Anexar arquivo ao projeto"
                          onClick={() => setActiveTab('files')}
                        >
                          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                            <path
                              d="M10.5 4.5 6.2 8.8a1.5 1.5 0 0 0 2.1 2.1l4.6-4.6a3 3 0 0 0-4.2-4.2L4 6.8a4.5 4.5 0 0 0 6.4 6.4l3.6-3.6"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                        </button>
                        <select
                          className="project-dashboard__composer-mode"
                          data-testid="project-composer-mode"
                          aria-label="Modo da conversa"
                          value={composerMode}
                          onChange={(e) => setComposerMode(e.target.value as ProjectComposerMode)}
                        >
                          <option value="auto">Automático</option>
                          <option value="chat">Chat</option>
                          <option value="code">Code</option>
                        </select>
                      </div>

                      <div className="project-dashboard__composer-right">
                        <button
                          type="button"
                          className="project-dashboard__composer-icon-btn"
                          aria-label="Ditar por voz"
                        >
                          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                            <rect x="5.75" y="2.25" width="4.5" height="7" rx="2.25" />
                            <path d="M3.75 7.5a4.25 4.25 0 0 0 8.5 0M8 11.75v2" strokeLinecap="round" />
                          </svg>
                        </button>
                        <button
                          type="submit"
                          className="project-dashboard__composer-submit"
                          data-testid="project-composer-submit"
                          aria-label="Iniciar conversa no projeto"
                        >
                          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
                            <path d="M2.5 8 13.5 2.5 10 13.5 7.5 9 2.5 8Z" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  </form>

                  <section className="project-page__section project-dashboard__chats-section" aria-labelledby="project-chats-title">
                    <div className="project-dashboard__chats-header">
                      <h2 id="project-chats-title">Conversas recentes</h2>
                      <div className="project-dashboard__chats-search-wrap">
                        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                          <circle cx="7" cy="7" r="4.5" />
                          <path d="m10.5 10.5 3.2 3.2" strokeLinecap="round" />
                        </svg>
                        <input
                          type="search"
                          className="project-dashboard__chats-search"
                          data-testid="project-chats-search"
                          placeholder="Buscar conversas"
                          aria-label="Buscar conversas"
                          value={chatSearchQuery}
                          onChange={(e) => setChatSearchQuery(e.target.value)}
                        />
                      </div>
                    </div>

                    {filteredChats.length > 0 ? (
                      <div className="project-page__chat-list project-dashboard__chat-list" data-testid="project-chat-list">
                        {filteredChats.map((chat) => {
                          const isRunning = ACTIVE_CHAT_STATES.has(chat.state)
                          const isSearch = /pesquis|oportunidade|busca|análise/i.test(chat.title)
                          return (
                            <Link
                              key={chat.conversation_id}
                              to={`/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chat.conversation_id)}`}
                              className="project-dashboard__chat-card"
                            >
                              <span className="project-dashboard__chat-icon" aria-hidden="true">
                                {chat.mode === 'code' ? (
                                  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                                    <rect x="3" y="2.2" width="10" height="11.6" rx="1.6" />
                                    <path d="M5.5 5.5h5M5.5 8h5M5.5 10.5h3" strokeLinecap="round" />
                                  </svg>
                                ) : isSearch ? (
                                  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                                    <circle cx="7" cy="7" r="4.5" />
                                    <path d="m10.5 10.5 3.2 3.2" strokeLinecap="round" />
                                  </svg>
                                ) : (
                                  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                                    <path
                                      d="M2.5 3.5h11a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H6l-3.5 2.5v-2.5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1Z"
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                    />
                                  </svg>
                                )}
                              </span>

                              <span className="project-dashboard__chat-body">
                                <span className="project-dashboard__chat-title-row">
                                  {chat.mode === 'code' ? (
                                    <strong className="workspace-nav__code-pill" data-testid="project-chat-code-pill">
                                      &gt;_ Code{' '}
                                    </strong>
                                  ) : (
                                    <small className="visually-hidden">Chat </small>
                                  )}
                                  <span className="project-dashboard__chat-title">{chat.title}</span>
                                </span>
                                <span className="project-dashboard__chat-summary">
                                  {summarizeProjectChat(chat)}
                                </span>
                              </span>

                              <span className="project-dashboard__chat-meta">
                                {isRunning && (
                                  <span className="project-dashboard__chat-running-pill">
                                    <span aria-hidden="true">◉</span> Em andamento
                                  </span>
                                )}
                                <span className="project-dashboard__chat-time">
                                  {formatChatRelativeTime(chat)}
                                </span>
                                <small className="visually-hidden">{chat.state}</small>
                                <span className="project-dashboard__chat-chevron" aria-hidden="true">›</span>
                              </span>
                            </Link>
                          )
                        })}
                      </div>
                    ) : (
                      <p className="project-page__empty">As conversas deste projeto aparecerão aqui.</p>
                    )}
                  </section>
                </>
              )}

              {activeTab === 'instructions' && (
                <section
                  className="project-page__section project-dashboard__instructions"
                  data-testid="project-instructions-editor"
                  aria-label="Instruções permanentes do projeto"
                >
                  <div className="project-page__section-heading">
                    <div>
                      <p className="eyebrow">DIRETRIZES DO PROJETO</p>
                      <h2>Instruções permanentes</h2>
                    </div>
                    <span>{instructions.length}</span>
                  </div>
                  <form className="project-dashboard__instructions-form" onSubmit={handleAddInstruction}>
                    <input
                      type="text"
                      value={newInstructionText}
                      onChange={(e) => setNewInstructionText(e.target.value)}
                      placeholder="Adicionar instrução permanente ao projeto..."
                      aria-label="Nova instrução do projeto"
                    />
                    <button type="submit" className="primary-button" disabled={!newInstructionText.trim()}>
                      Adicionar instrução
                    </button>
                  </form>
                  <ul className="project-dashboard__instructions-list">
                    {instructions.map((item, idx) => (
                      <li key={`${idx}-${item}`} className="project-dashboard__instruction-item">
                        <span>{item}</span>
                        <button
                          type="button"
                          className="ghost-button"
                          aria-label={`Remover instrução ${idx + 1}`}
                          onClick={() => handleRemoveInstruction(idx)}
                        >
                          Remover
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {activeTab === 'memory' && (
                <section className="project-page__section" aria-labelledby="project-memory-title">
                  <div className="project-page__section-heading">
                    <div>
                      <p className="eyebrow">CONTEXTO COMPARTILHADO</p>
                      <h2 id="project-memory-title">Memória</h2>
                    </div>
                    <Link to={`/projects/${encodeURIComponent(projectId)}/memory`}>Gerenciar</Link>
                  </div>
                  {memories.length > 0 ? (
                    <ul className="project-dashboard__memories-list">
                      {memories.map((mem) => (
                        <li key={mem.memory_id} className="project-dashboard__memory-item">
                          {mem.fact}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="project-page__empty">Nenhuma memória de projeto ainda.</p>
                  )}
                </section>
              )}

              {(activeTab === 'conversations' || activeTab === 'files') && (
                <section
                  className="project-page__section project-dashboard__workspace-section"
                  aria-labelledby="project-workspace-title"
                  data-testid="project-workspace-section"
                >
                  <div className="project-page__section-heading">
                    <div>
                      <p className="eyebrow">PASTA LOCAL</p>
                      <h2 id="project-workspace-title">Workspace do projeto</h2>
                    </div>
                    <span>{workspaceDisplayLabel}</span>
                  </div>
                  <form
                    className="project-dashboard__workspace-form"
                    onSubmit={(e) => {
                      void handleSaveWorkspace(e)
                    }}
                  >
                    <input
                      ref={wsInputRef}
                      type="text"
                      value={wsPath}
                      onChange={(e) => setWsPath(e.target.value)}
                      placeholder="/caminho/para/repositorio"
                      aria-label="Pasta local do projeto"
                      data-testid="project-workspace-input"
                    />
                    <button
                      type="button"
                      className="ghost-button"
                      disabled={wsBusy}
                      onClick={() => {
                        void handlePickWorkspace()
                      }}
                      data-testid="project-workspace-pick-button"
                    >
                      Selecionar pasta…
                    </button>
                    <button
                      type="submit"
                      className="ghost-button"
                      disabled={wsBusy || !wsPath.trim()}
                      data-testid="project-workspace-save-button"
                    >
                      Vincular pasta
                    </button>
                    {project.workspace?.kind === 'local' && (
                      <button
                        type="button"
                        className="ghost-button"
                        disabled={wsBusy}
                        onClick={() => {
                          void handleDetachWorkspace()
                        }}
                        data-testid="project-workspace-detach-button"
                      >
                        Desvincular
                      </button>
                    )}
                  </form>
                  {wsError && <p role="alert">{wsError}</p>}
                </section>
              )}
            </div>

            <aside
              className="project-dashboard__context-col"
              data-testid="project-shared-context"
              aria-label="Contexto compartilhado"
            >
              <div className="project-dashboard__context-panel">
                <h3 className="project-dashboard__context-title">Contexto compartilhado</h3>
                <p className="project-dashboard__context-subtitle">
                  Todas as conversas deste projeto usam este contexto.
                </p>

                <button
                  type="button"
                  className="project-dashboard__context-card"
                  data-testid="project-context-folder-card"
                  onClick={() => setActiveTab('files')}
                >
                  <span className="project-dashboard__context-card-icon" aria-hidden="true">
                    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                      <path
                        d="M2 4.5A1.5 1.5 0 0 1 3.5 3h2.88a1.5 1.5 0 0 1 1.06.44l.87.87a.5.5 0 0 0 .35.15h3.84A1.5 1.5 0 0 1 14 5.96v5.54A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5v-7Z"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </span>
                  <span className="project-dashboard__context-card-body">
                    <strong className="project-dashboard__context-card-primary">
                      {workspaceDisplayLabel}
                    </strong>
                    <small className="project-dashboard__context-card-secondary">
                      {workspaceStatusLabel} · {project.chats.length} {project.chats.length === 1 ? 'conversa' : 'conversas'}
                    </small>
                  </span>
                  <span className="project-dashboard__context-card-chevron" aria-hidden="true">›</span>
                </button>

                <button
                  type="button"
                  className="project-dashboard__context-card"
                  data-testid="project-context-instructions-card"
                  onClick={() => setActiveTab('instructions')}
                >
                  <span className="project-dashboard__context-card-icon" aria-hidden="true">
                    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                      <rect x="3" y="2.2" width="10" height="11.6" rx="1.6" />
                      <path d="M5.5 5.5h5M5.5 8h5M5.5 10.5h3" strokeLinecap="round" />
                    </svg>
                  </span>
                  <span className="project-dashboard__context-card-body">
                    <strong className="project-dashboard__context-card-primary">
                      {instructions.length} {instructions.length === 1 ? 'instrução' : 'instruções'}
                    </strong>
                  </span>
                  <span className="project-dashboard__context-card-chevron" aria-hidden="true">›</span>
                </button>

                <div className="project-dashboard__context-divider" />

                <div className="project-dashboard__context-actions">
                  <button
                    type="button"
                    className="project-dashboard__context-action-btn"
                    data-testid="project-add-files-button"
                    onClick={() => {
                      setActiveTab('files')
                      void handlePickWorkspace()
                    }}
                  >
                    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                      <path d="M8 10.5V3.5M5.2 6.2 8 3.5l2.8 2.7M3 12.5h10" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    <span>Adicionar arquivos</span>
                  </button>

                  <button
                    type="button"
                    className="project-dashboard__context-action-btn"
                    data-testid="project-edit-instructions-button"
                    onClick={() => setActiveTab('instructions')}
                  >
                    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                      <rect x="3" y="2.2" width="10" height="11.6" rx="1.6" />
                      <path d="M5.5 5.5h5M5.5 8h5M5.5 10.5h3" strokeLinecap="round" />
                    </svg>
                    <span>Editar instruções</span>
                  </button>
                </div>
              </div>
            </aside>
          </div>
        </div>
      </main>
    </div>
  )
}
