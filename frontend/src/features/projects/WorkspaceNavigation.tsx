import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { listConversations, type Conversation, type ConversationMode } from '../../api/conversations'
import { createProject, listProjectSidebar, type ProjectSidebarItem } from '../../api/projects'
import { useTheme } from '../../app/ThemeContext'
import { Brand } from '../../components/Brand'
import { ProfileMenu } from '../auth/ProfileMenu'

export type WorkspaceNavigationConversationSummary = Pick<Conversation, 'conversation_id' | 'title' | 'state'> & {
  mode?: ConversationMode
}

export type WorkspaceNavigationProps = {
  client?: ApiClient
  onChatsChange?: (chats: WorkspaceNavigationConversationSummary[]) => void
  onNewConversation?: () => void
  onNewCodeConversation?: () => void
  railCollapsed?: boolean
  onToggleRail?: () => void
  onOpenPreferences?: () => void
}

const SIDEBAR_COLLAPSED_STORAGE_KEY = 'orin.sidebar.collapsed'

function readStoredCollapsed(): boolean {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return false
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

function writeStoredCollapsed(collapsed: boolean): void {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, collapsed ? 'true' : 'false')
    }
  } catch {
    // Ignore storage failures in restricted contexts
  }
}

function readStoredView(): 'chats' | 'projects' {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return 'chats'
    return window.localStorage.getItem('agentos.navigation-view') === 'projects' ? 'projects' : 'chats'
  } catch {
    return 'chats'
  }
}

function readStoredCollapsedProjects(): Set<string> {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return new Set()
    const raw = window.localStorage.getItem('agentos.collapsed-projects')
    if (!raw) return new Set()
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? new Set(parsed.filter((item): item is string => typeof item === 'string')) : new Set()
  } catch {
    return new Set()
  }
}

function detectApplePlatform(): boolean {
  if (typeof navigator === 'undefined') return false
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } }
  const platform = nav.userAgentData?.platform ?? navigator.platform ?? navigator.userAgent ?? ''
  return /mac|iphone|ipad|ipod/i.test(platform)
}

function ModeBadge({ mode, conversationId }: { mode?: ConversationMode; conversationId: string }) {
  if (mode === 'code') {
    return (
      <span
        className="project-navigation__mode-icon project-navigation__mode-icon--code inline-flex shrink-0 items-center justify-center rounded border border-[var(--orin-border-subtle)] bg-[var(--orin-accent-bg)] px-1 py-0.5 font-mono text-[10px] font-bold leading-none text-[var(--orin-accent-soft)]"
        title="Orin Code"
        data-testid={`conversation-mode-icon-${conversationId}`}
        data-mode="code"
        aria-hidden="true"
      >
        &gt;_
      </span>
    )
  }
  return (
    <span
      className="project-navigation__mode-icon project-navigation__mode-icon--chat inline-flex shrink-0 items-center justify-center text-[var(--orin-text-muted)]"
      title="Chat"
      data-testid={`conversation-mode-icon-${conversationId}`}
      data-mode="chat"
      aria-hidden="true"
    >
      <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M2.5 3.5h11a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H6l-3.5 2.5v-2.5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1Z" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  )
}

export function WorkspaceNavigation({
  client,
  onChatsChange,
  onNewConversation,
  onNewCodeConversation,
  railCollapsed,
  onToggleRail,
  onOpenPreferences,
}: WorkspaceNavigationProps) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const navigate = useNavigate()
  const location = useLocation()
  const reduced = useReducedMotion()
  const { resolvedTheme, cycleMode } = useTheme()
  const [internalCollapsed, setInternalCollapsed] = useState<boolean>(() => readStoredCollapsed())
  const isRailCollapsed = railCollapsed !== undefined ? railCollapsed : internalCollapsed

  const [chats, setChats] = useState<WorkspaceNavigationConversationSummary[]>([])
  const [projects, setProjects] = useState<ProjectSidebarItem[]>([])
  const [creatingProject, setCreatingProject] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [projectDescription, setProjectDescription] = useState('')
  const [projectWorkspacePath, setProjectWorkspacePath] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [view, setView] = useState<'chats' | 'projects'>(() => readStoredView())
  const [collapsed, setCollapsed] = useState<Set<string>>(() => readStoredCollapsedProjects())

  const paletteShortcut = useMemo(() => (detectApplePlatform() ? '⌘K' : 'Ctrl+K'), [])

  const locationKey = `${location.pathname}${location.search}`
  const [previousLocationKey, setPreviousLocationKey] = useState(locationKey)
  if (locationKey !== previousLocationKey) {
    setPreviousLocationKey(locationKey)
    if (mobileOpen) {
      setMobileOpen(false)
    }
  }

  useEffect(() => {
    if (!mobileOpen) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setMobileOpen(false)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [mobileOpen])

  const handleToggleRail = useCallback(() => {
    if (onToggleRail) {
      onToggleRail()
      return
    }
    setInternalCollapsed((current) => {
      const next = !current
      writeStoredCollapsed(next)
      return next
    })
  }, [onToggleRail])

  function changeView(next: 'chats' | 'projects') {
    setView(next)
    try {
      localStorage.setItem('agentos.navigation-view', next)
    } catch {
      // Ignore storage errors
    }
  }

  function toggleProject(id: string) {
    setCollapsed((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      try {
        localStorage.setItem('agentos.collapsed-projects', JSON.stringify([...next]))
      } catch {
        // Ignore storage errors
      }
      return next
    })
  }

  const activeProjectId = location.pathname.match(/^\/projects\/([^/]+)/)?.[1]
  const isLibraryActive =
    location.pathname.startsWith('/library') ||
    location.pathname.startsWith('/settings/skills') ||
    location.pathname.startsWith('/settings/plugins') ||
    location.pathname.startsWith('/settings/memory')
  const isIntegrationsActive =
    location.pathname.startsWith('/integrations') ||
    location.pathname.startsWith('/settings/providers') ||
    location.pathname.startsWith('/settings/mcp') ||
    location.pathname.startsWith('/settings/vision')
  const isSchedulesActive = location.pathname.startsWith('/schedules')

  const reload = useCallback(async () => {
    const [conversationList, sidebar] = await Promise.all([listConversations(apiClient), listProjectSidebar(apiClient)])
    setChats(conversationList.items)
    onChatsChange?.(conversationList.items)
    setProjects(sidebar.items)
  }, [apiClient, onChatsChange])

  const activeSessionCount = useMemo(() => {
    const activeStates = new Set(['queued', 'starting', 'running', 'streaming', 'cancelling'])
    const activeIds = new Set<string>()
    for (const chat of chats) {
      if (activeStates.has(chat.state)) {
        activeIds.add(chat.conversation_id)
      }
    }
    for (const project of projects) {
      for (const chat of project.chats) {
        if (activeStates.has(chat.state)) {
          activeIds.add(chat.conversation_id)
        }
      }
    }
    return activeIds.size
  }, [chats, projects])

  useEffect(() => {
    let active = true
    queueMicrotask(() => {
      void reload().catch(() => {
        if (active) setError('Não foi possível atualizar a navegação.')
      })
    })
    const onUpdated = () => {
      void reload().catch(() => undefined)
    }
    window.addEventListener('orin:projects-updated', onUpdated)
    window.addEventListener('orin:conversations-updated', onUpdated)
    return () => {
      active = false
      window.removeEventListener('orin:projects-updated', onUpdated)
      window.removeEventListener('orin:conversations-updated', onUpdated)
    }
  }, [reload])

  useEffect(() => {
    if (activeSessionCount === 0) return
    const intervalId = window.setInterval(() => {
      void reload().catch(() => undefined)
    }, 3000)
    return () => window.clearInterval(intervalId)
  }, [activeSessionCount, reload])

  async function submitProject() {
    if (!projectName.trim()) return
    try {
      const trimmedWorkspace = projectWorkspacePath.trim()
      await createProject(apiClient, {
        name: projectName,
        description: projectDescription,
        ...(trimmedWorkspace
          ? { workspace_path: trimmedWorkspace, workspace_acknowledged_risk: true }
          : {}),
      })
      setProjectName('')
      setProjectDescription('')
      setProjectWorkspacePath('')
      setCreatingProject(false)
      setError(null)
      await reload()
    } catch {
      setError('Não foi possível criar o projeto.')
    }
  }

  const handleNewChat = useCallback(() => {
    setMobileOpen(false)
    if (onNewConversation) {
      onNewConversation()
      return
    }
    navigate('/?mode=chat')
  }, [navigate, onNewConversation])

  const handleNewCode = useCallback(() => {
    setMobileOpen(false)
    if (onNewCodeConversation) {
      onNewCodeConversation()
      return
    }
    navigate('/?mode=code')
  }, [navigate, onNewCodeConversation])

  const openCommandPalette = useCallback(() => {
    setMobileOpen(false)
    document.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, metaKey: true, bubbles: true }),
    )
  }, [])

  const handleOpenPreferences = useCallback(() => {
    setMobileOpen(false)
    if (onOpenPreferences) {
      onOpenPreferences()
      return
    }
    navigate('/settings')
  }, [navigate, onOpenPreferences])

  return (
    <>
      <button
        type="button"
        className="workspace-navigation__mobile-trigger"
        aria-label="Abrir navegação"
        aria-expanded={mobileOpen}
        aria-controls="workspace-navigation-drawer"
        data-testid="mobile-navigation-trigger"
        onClick={() => setMobileOpen((current) => !current)}
      >
        <span aria-hidden="true">☰</span>
        <span>Menu</span>
      </button>

      {mobileOpen && (
        <div
          className="workspace-navigation__backdrop"
          data-testid="mobile-navigation-backdrop"
          aria-hidden="true"
          onClick={() => setMobileOpen(false)}
        />
      )}

      <nav
        id="workspace-navigation-drawer"
        className={`${mobileOpen ? 'project-navigation is-mobile-open' : 'project-navigation'}${isRailCollapsed ? ' is-rail-collapsed' : ''}`}
        data-rail-collapsed={isRailCollapsed ? 'true' : 'false'}
        aria-label="Conversas e projetos"
      >
        <header className="project-navigation__header">
          <div className="project-navigation__brand-row">
            <Brand to="/" />
            <div className="project-navigation__heading-copy project-navigation__rail-hide">
              <p className="project-navigation__eyebrow">Workspace</p>
              <h2>Seu espaço</h2>
            </div>
          </div>
          <div className="project-navigation__header-controls">
            {activeSessionCount > 0 && (
              <span
                className="project-navigation__active-badge inline-flex items-center gap-1 rounded-full border border-[var(--orin-success-border)] bg-[var(--orin-success-bg)] px-2 py-0.5 font-mono text-[10px] font-semibold text-[var(--orin-success)]"
                data-testid="sidebar-active-sessions-badge"
                data-active-count={activeSessionCount}
                aria-label={`${activeSessionCount} ${activeSessionCount === 1 ? 'sessão ativa' : 'sessões ativas'}`}
              >
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-[var(--orin-success)] animate-pulse" aria-hidden="true" />
                <span className="project-navigation__rail-hide">
                  {activeSessionCount} {activeSessionCount === 1 ? 'ativa' : 'ativas'}
                </span>
              </span>
            )}
            <span className="project-navigation__count project-navigation__rail-hide" aria-label={`${chats.length + projects.length} itens`}>
              {chats.length + projects.length}
            </span>
            <button
              type="button"
              className="project-navigation__rail-toggle"
              data-testid="sidebar-rail-toggle"
              aria-expanded={!isRailCollapsed}
              aria-label={isRailCollapsed ? 'Expandir navegação lateral' : 'Recolher navegação lateral'}
              title={isRailCollapsed ? 'Expandir navegação lateral' : 'Recolher navegação lateral'}
              onClick={handleToggleRail}
            >
              <svg className="h-4 w-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                <rect x="2" y="2.5" width="12" height="11" rx="2" />
                <path d="M6 2.5v11" />
              </svg>
            </button>
            {mobileOpen && (
              <button
                type="button"
                className="project-navigation__close-mobile"
                aria-label="Fechar navegação"
                onClick={() => setMobileOpen(false)}
              >
                ✕
              </button>
            )}
          </div>
        </header>

        <div className="project-navigation__cta-group mb-2.5 grid grid-cols-2 gap-1.5" role="group" aria-label="Criar nova sessão">
          <button
            type="button"
            data-testid="sidebar-new-chat"
            onClick={handleNewChat}
            title="Novo chat"
            className="project-navigation__cta-btn flex items-center justify-center gap-1.5 rounded-lg border border-[var(--orin-border)] bg-[var(--orin-surface-raised)] px-2.5 py-2 text-xs font-medium text-[var(--orin-text-primary)] transition-colors hover:border-[var(--orin-border-strong)] hover:bg-[var(--orin-surface-hover)]"
          >
            <span aria-hidden="true">+</span>
            <span className="project-navigation__rail-hide">Novo chat</span>
          </button>
          <button
            type="button"
            data-testid="sidebar-new-code"
            onClick={handleNewCode}
            title="Novo Code"
            className="project-navigation__cta-btn flex items-center justify-center gap-1.5 rounded-lg border border-[var(--orin-border-subtle)] bg-[var(--orin-accent-bg)] px-2.5 py-2 text-xs font-medium text-[var(--orin-text-primary)] transition-colors hover:border-[var(--orin-accent)] hover:bg-[var(--orin-surface-selected)]"
          >
            <span className="font-mono text-[10px] font-bold text-[var(--orin-accent-soft)]" aria-hidden="true">&gt;_</span>
            <span className="project-navigation__rail-hide">Novo Code</span>
          </button>
        </div>

        <button
          type="button"
          className="project-navigation__search-trigger"
          onClick={openCommandPalette}
          title={`Buscar e comandos (${paletteShortcut})`}
          aria-label={`Buscar conversas e comandos (${paletteShortcut})`}
        >
          <span className="project-navigation__rail-hide">Buscar e comandos…</span>
          <kbd>{paletteShortcut}</kbd>
        </button>

        <div className="project-navigation__hubs" role="group" aria-label="Hubs do workspace">
          <Link
            to="/library/skills"
            onClick={() => setMobileOpen(false)}
            aria-current={isLibraryActive ? 'page' : undefined}
            className={`project-navigation__hub-link${isLibraryActive ? ' is-active' : ''}`}
            title="Biblioteca"
          >
            <span className="project-navigation__hub-icon" aria-hidden="true">
              <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M3 2.5h8.5A1.5 1.5 0 0 1 13 4v9.5a.5.5 0 0 1-.5.5H3.5A1.5 1.5 0 0 1 2 12.5v-8.5A1.5 1.5 0 0 1 3.5 2.5Z" />
                <path d="M5.5 5.5h4.5M5.5 8h4.5" strokeLinecap="round" />
              </svg>
            </span>
            <span className="project-navigation__hub-label">Biblioteca</span>
          </Link>

          <Link
            to="/integrations/providers"
            onClick={() => setMobileOpen(false)}
            aria-current={isIntegrationsActive ? 'page' : undefined}
            className={`project-navigation__hub-link${isIntegrationsActive ? ' is-active' : ''}`}
            title="Integrações"
          >
            <span className="project-navigation__hub-icon" aria-hidden="true">
              <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M6 2.5v2.5M10 2.5v2.5M4.5 5h7v2.5a3.5 3.5 0 0 1-7 0V5ZM8 11v2.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <span className="project-navigation__hub-label">Integrações</span>
          </Link>

          <Link
            to="/schedules"
            onClick={() => setMobileOpen(false)}
            aria-current={isSchedulesActive ? 'page' : undefined}
            className={`project-navigation__hub-link${isSchedulesActive ? ' is-active' : ''}`}
            title="Automações"
          >
            <span className="project-navigation__hub-icon" aria-hidden="true">
              <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="8" cy="8" r="5.5" />
                <path d="M8 5v3.2l2.2 1.3" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <span className="project-navigation__hub-label">Automações</span>
          </Link>
        </div>

        <div className="project-navigation__tabs project-navigation__rail-hide" role="tablist" aria-label="Seções da navegação">
          <button
            type="button"
            role="tab"
            aria-label="Chats"
            aria-selected={view === 'chats'}
            aria-controls="navigation-chats"
            onClick={() => changeView('chats')}
          >
            <span>Chats</span>
            <small>{chats.length}</small>
          </button>
          <button
            type="button"
            role="tab"
            aria-label="Projetos"
            aria-selected={view === 'projects'}
            aria-controls="navigation-projects"
            onClick={() => changeView('projects')}
          >
            <span>Projetos</span>
            <small>{projects.length}</small>
          </button>
        </div>

        <div className="project-navigation__scroll project-navigation__rail-hide">
          <AnimatePresence mode="wait">
            {view === 'chats' ? (
              <motion.div
                id="navigation-chats"
                role="tabpanel"
                aria-label="Chats independentes"
                key="chats"
                initial={reduced ? false : { opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduced ? undefined : { opacity: 0, y: -4 }}
              >
                <div className="project-navigation__section-heading">
                  <span>Conversas recentes</span>
                  {chats.length > 0 && <small>{chats.length} {chats.length === 1 ? 'conversa' : 'conversas'}</small>}
                </div>
                {chats.length > 0 ? (
                  chats.map((chat) => (
                    <NavLink
                      key={chat.conversation_id}
                      to={`/chats/${encodeURIComponent(chat.conversation_id)}`}
                      title={chat.title}
                      onClick={() => setMobileOpen(false)}
                      className={({ isActive }) => (isActive ? 'project-navigation__chat is-active' : 'project-navigation__chat')}
                    >
                      <ModeBadge mode={chat.mode} conversationId={chat.conversation_id} />
                      <span className="project-navigation__chat-title" title={chat.title}>{chat.title}</span>
                      <span
                        className="project-navigation__chat-state"
                        data-testid={`conversation-state-${chat.conversation_id}`}
                        data-state={chat.state}
                        aria-hidden="true"
                      />
                    </NavLink>
                  ))
                ) : (
                  <p className="project-navigation__empty">Suas novas conversas aparecerão aqui.</p>
                )}
              </motion.div>
            ) : (
              <motion.div
                id="navigation-projects"
                role="tabpanel"
                aria-label="Projetos"
                key="projects"
                initial={reduced ? false : { opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduced ? undefined : { opacity: 0, y: -4 }}
              >
                <div className="project-navigation__section-heading">
                  <span>Seus projetos</span>
                  {projects.length > 0 && <small>{projects.length} {projects.length === 1 ? 'projeto' : 'projetos'}</small>}
                </div>
                {projects.length > 0 ? (
                  projects.map((project) => (
                    <section key={project.project_id} className="project-navigation__project">
                      <div className={activeProjectId === project.project_id ? 'project-navigation__project-heading is-active' : 'project-navigation__project-heading'}>
                        <button
                          type="button"
                          className="project-navigation__chevron"
                          onClick={() => toggleProject(project.project_id)}
                          aria-label={`Alternar ${project.name}`}
                          aria-expanded={!collapsed.has(project.project_id)}
                        >
                          {collapsed.has(project.project_id) ? '›' : '⌄'}
                        </button>
                        <Link
                          to={`/projects/${encodeURIComponent(project.project_id)}`}
                          className="project-navigation__project-name"
                          title={project.name}
                          onClick={() => setMobileOpen(false)}
                        >
                          <span className="project-navigation__project-dot" aria-hidden="true" />
                          {project.name}
                        </Link>
                        <button
                          type="button"
                          className="project-navigation__add-chat"
                          aria-label={`Novo chat em ${project.name}`}
                          onClick={() => {
                            setMobileOpen(false)
                            navigate(`/projects/${encodeURIComponent(project.project_id)}/new`)
                          }}
                        >
                          +
                        </button>
                      </div>
                      {!collapsed.has(project.project_id) &&
                        project.chats.map((chat) => (
                          <NavLink
                            key={chat.conversation_id}
                            to={`/projects/${encodeURIComponent(project.project_id)}/chats/${encodeURIComponent(chat.conversation_id)}`}
                            title={chat.title}
                            onClick={() => setMobileOpen(false)}
                            className={({ isActive }) => (
                              isActive
                                ? 'project-navigation__chat project-navigation__chat--project is-active'
                                : 'project-navigation__chat project-navigation__chat--project'
                            )}
                          >
                            <ModeBadge mode={chat.mode} conversationId={chat.conversation_id} />
                            <span className="project-navigation__chat-title" title={chat.title}>{chat.title}</span>
                            <span
                              className="project-navigation__chat-state"
                              data-testid={`conversation-state-${chat.conversation_id}`}
                              data-state={chat.state}
                              aria-hidden="true"
                            />
                          </NavLink>
                        ))}
                      {!collapsed.has(project.project_id) && project.chats.length === 0 && (
                        <p className="project-navigation__empty project-navigation__empty--project">Ainda não há chats neste projeto.</p>
                      )}
                    </section>
                  ))
                ) : (
                  <p className="project-navigation__empty">Crie um projeto para organizar conversas e contexto.</p>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <div className="project-navigation__actions">
          {view === 'projects' && (
            <button type="button" className="project-navigation__action project-navigation__action--primary" onClick={() => setCreatingProject(true)}>
              <span className="project-navigation__action-icon" aria-hidden="true">+</span>
              <span className="project-navigation__rail-hide">Novo projeto</span>
            </button>
          )}

          <div className="project-navigation__footer">
            <div className="project-navigation__footer-profile project-navigation__rail-hide">
              <ProfileMenu />
            </div>
            <div className="project-navigation__footer-controls">
              <button
                type="button"
                className="project-navigation__footer-btn"
                data-testid="theme-quick-toggle"
                onClick={cycleMode}
                aria-label={`Alternar tema (atual: ${resolvedTheme})`}
                title={`Alternar tema (${resolvedTheme})`}
              >
                <span aria-hidden="true">
                  {resolvedTheme === 'light' ? '☀' : resolvedTheme === 'carbon' ? '◐' : '☾'}
                </span>
              </button>
              <button
                type="button"
                className="project-navigation__footer-btn project-navigation__footer-btn--preferences"
                data-testid="open-preferences-modal"
                aria-label="Preferências"
                title="Preferências (Ctrl+,)"
                onClick={handleOpenPreferences}
              >
                <span aria-hidden="true">⚙</span>
                <span className="project-navigation__footer-label project-navigation__rail-hide">Preferências</span>
              </button>
            </div>
          </div>
        </div>
      </nav>
      {creatingProject && (
        <div className="project-dialog" role="dialog" aria-modal="true" aria-label="Criar projeto">
          <form onSubmit={(event) => { event.preventDefault(); void submitProject() }}>
            <h2>Criar projeto</h2>
            <label>
              Nome
              <input autoFocus value={projectName} onChange={(event) => setProjectName(event.target.value)} maxLength={120} />
            </label>
            <label>
              Descrição
              <input value={projectDescription} onChange={(event) => setProjectDescription(event.target.value)} maxLength={2000} />
            </label>
            <label>
              Pasta local (opcional)
              <input
                data-testid="new-project-workspace-path"
                value={projectWorkspacePath}
                onChange={(event) => setProjectWorkspacePath(event.target.value)}
                placeholder="/caminho/da/pasta"
                maxLength={1024}
              />
            </label>
            <div>
              <button type="button" onClick={() => setCreatingProject(false)}>Cancelar</button>
              <button type="submit">Criar</button>
            </div>
          </form>
        </div>
      )}
      {error && <p className="workspace-navigation__error" role="status">{error}</p>}
    </>
  )
}
