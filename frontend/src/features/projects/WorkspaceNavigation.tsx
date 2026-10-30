import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { ensureFriendConversation, listConversations, type Conversation, type ConversationMode } from '../../api/conversations'
import { createProject, listProjectSidebar, type ProjectSidebarItem } from '../../api/projects'
import { inspectNewWorkspaceFolder } from '../../api/workspace'
import { useTheme } from '../../app/ThemeContext'
import { Brand } from '../../components/Brand'
import { OrinMascot } from '../../components/OrinMascot'
import { ProfileMenu } from '../auth/ProfileMenu'

export type WorkspaceNavigationConversationSummary = Pick<Conversation, 'conversation_id' | 'title' | 'state'> & {
  mode?: ConversationMode
  updated_at?: string | null
}

export type WorkspaceNavigationProps = {
  client?: ApiClient
  onChatsChange?: (chats: WorkspaceNavigationConversationSummary[]) => void
  onNewConversation?: () => void
  onNewCodeConversation?: () => void
  onOpenOrinFriend?: () => void
  isOrinFriendActive?: boolean
  friendBondStage?: string
  railCollapsed?: boolean
  onToggleRail?: () => void
  onOpenPreferences?: () => void
  initialView?: 'chats' | 'projects'
  hideProjectChatSublist?: boolean
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

export function formatRelativeShort(isoDate?: string | null): string {
  if (!isoDate) return 'agora'
  const ts = Date.parse(isoDate)
  if (!Number.isFinite(ts)) return 'agora'
  const diffMs = Math.max(0, Date.now() - ts)
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return 'agora'
  if (mins < 60) return `há ${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `há ${hours}h`
  const days = Math.floor(hours / 24)
  return `há ${days}d`
}

function ModeBadge({ mode, conversationId }: { mode?: ConversationMode; conversationId: string }) {
  if (mode === 'friend') {
    return (
      <span
        className="project-navigation__mode-icon project-navigation__mode-icon--friend inline-flex shrink-0 items-center justify-center rounded border border-[var(--orin-border-subtle)] bg-[var(--orin-accent-bg)] px-1 py-0.5 font-mono text-[10px] font-bold leading-none text-[#A78BFA]"
        title="Orin Friend"
        data-testid={`conversation-mode-icon-${conversationId}`}
        data-mode="friend"
        aria-hidden="true"
      >
        ◈
      </span>
    )
  }
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
  onOpenOrinFriend,
  isOrinFriendActive,
  railCollapsed,
  onToggleRail,
  onOpenPreferences,
  initialView,
  hideProjectChatSublist = false,
}: WorkspaceNavigationProps) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const navigate = useNavigate()
  const location = useLocation()
  const reduced = useReducedMotion()
  const { resolvedTheme, cycleMode } = useTheme()
  const [internalCollapsed, setInternalCollapsed] = useState<boolean>(() => readStoredCollapsed())
  const isRailCollapsed = railCollapsed !== undefined ? railCollapsed : internalCollapsed

  const [chats, setChats] = useState<WorkspaceNavigationConversationSummary[]>([])
  // The pinned companion carries the name the person gave it.
  const friendName = chats.find((chat) => chat.mode === 'friend')?.title?.trim() || 'Orin Friend'
  const [projects, setProjects] = useState<ProjectSidebarItem[]>([])
  const [creatingProject, setCreatingProject] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [projectDescription, setProjectDescription] = useState('')
  const [projectWorkspacePath, setProjectWorkspacePath] = useState('')
  const [pickingProjectFolder, setPickingProjectFolder] = useState(false)
  const [projectPickerNotice, setProjectPickerNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [view, setView] = useState<'chats' | 'projects'>(() => initialView ?? readStoredView())
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
  const isHomeActive = location.pathname === '/'
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
  const isSettingsActive =
    location.pathname === '/settings' ||
    location.pathname.startsWith('/settings/general') ||
    location.pathname.startsWith('/settings/permissions') ||
    location.pathname.startsWith('/settings/trusted-folders')

  const reload = useCallback(async () => {
    const [conversationList, sidebar] = await Promise.all([listConversations(apiClient), listProjectSidebar(apiClient)])
    const nextChats = Array.isArray(conversationList?.items) ? conversationList.items : []
    const nextProjects = Array.isArray(sidebar?.items) ? sidebar.items : []
    setChats(nextChats)
    onChatsChange?.(nextChats)
    setProjects(nextProjects)
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

  useEffect(() => {
    if (!creatingProject) return
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.stopPropagation()
        setCreatingProject(false)
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [creatingProject])

  async function handlePickProjectFolder() {
    if (pickingProjectFolder) return
    setPickingProjectFolder(true)
    setProjectPickerNotice(null)
    try {
      const outcome = await inspectNewWorkspaceFolder(apiClient, null)
      if (outcome.kind === 'cancelled') return
      if (outcome.kind === 'unavailable') {
        setProjectPickerNotice('Não foi possível abrir o seletor do sistema. Digite o caminho abaixo.')
        return
      }
      setProjectWorkspacePath(outcome.path)
    } catch {
      setProjectPickerNotice('Não foi possível inspecionar a pasta selecionada.')
    } finally {
      setPickingProjectFolder(false)
    }
  }

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
      setProjectPickerNotice(null)
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

  const handleOpenOrinFriend = useCallback(async () => {
    setMobileOpen(false)
    if (onOpenOrinFriend) {
      onOpenOrinFriend()
      return
    }
    try {
      const friendConv = await ensureFriendConversation(apiClient)
      navigate(`/chats/${encodeURIComponent(friendConv.conversation_id)}`)
    } catch {
      navigate('/?mode=friend')
    }
  }, [apiClient, navigate, onOpenOrinFriend])

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
        <span className="workspace-navigation__mobile-trigger-label">Menu</span>
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
            <span className="project-navigation__count visually-hidden" aria-label={`${chats.length + projects.length} itens`}>
              {chats.length + projects.length}
            </span>
            <button
              type="button"
              className="project-navigation__rail-toggle"
              onClick={openCommandPalette}
              title={`Buscar e comandos (${paletteShortcut})`}
              aria-label={`Buscar conversas e comandos (${paletteShortcut})`}
            >
              <svg
                className="project-navigation__search-icon h-4 w-4"
                data-testid="sidebar-search-icon"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                aria-hidden="true"
              >
                <circle cx="7" cy="7" r="4.25" />
                <path d="M10.25 10.25 13.5 13.5" strokeLinecap="round" />
              </svg>
            </button>
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

        <div className="project-navigation__cta-primary mb-2.5" role="group" aria-label="Criar nova sessão">
          <button
            type="button"
            data-testid="sidebar-new-chat"
            onClick={handleNewChat}
            title="Nova conversa"
            className="project-navigation__new-conversation-btn"
          >
            <span className="project-navigation__new-conversation-plus" aria-hidden="true">+</span>
            <span className="project-navigation__rail-hide">Nova conversa</span>
          </button>
          <button
            type="button"
            data-testid="sidebar-new-code"
            onClick={handleNewCode}
            title="Novo Code"
            className="visually-hidden"
          >
            Novo Code
          </button>
        </div>

        <div className="project-navigation__primary-nav" role="group" aria-label="Navegação principal">
          <Link
            to="/"
            onClick={() => setMobileOpen(false)}
            aria-current={isHomeActive ? 'page' : undefined}
            className={`project-navigation__hub-link${isHomeActive ? ' is-active' : ''}`}
            title="Início"
          >
            <span className="project-navigation__hub-icon" aria-hidden="true">
              <svg className="h-4 w-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M2.5 6.8 8 2.25l5.5 4.55v5.7a1 1 0 0 1-1 1h-3v-4h-3v4h-3a1 1 0 0 1-1-1v-5.7Z" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <span className="project-navigation__hub-label">Início</span>
          </Link>

          <button
            type="button"
            data-testid="sidebar-orin-friend-button"
            onClick={() => void handleOpenOrinFriend()}
            title={`${friendName} — seu companheiro com memória e conversa por voz`}
            aria-current={isOrinFriendActive ? 'page' : undefined}
            className={`project-navigation__hub-link project-navigation__hub-link--friend${isOrinFriendActive ? ' is-active' : ''}`}
          >
            <span className="project-navigation__hub-icon" aria-hidden="true">
              <OrinMascot size={18} variant="flat" ariaHidden />
            </span>
            <span className="project-navigation__hub-label">
              {friendName === 'Orin Friend' ? (
                <>
                  Friend
                  <span className="visually-hidden"> (Orin Friend)</span>
                </>
              ) : (
                friendName
              )}
            </span>
            <span className="project-navigation__hub-chevron project-navigation__rail-hide" aria-hidden="true">›</span>
          </button>

          <Link
            to="/schedules"
            onClick={() => setMobileOpen(false)}
            aria-current={isSchedulesActive ? 'page' : undefined}
            aria-label="Ações agendadas (Automações)"
            className={`project-navigation__hub-link${isSchedulesActive ? ' is-active' : ''}`}
            title="Ações agendadas"
          >
            <span className="project-navigation__hub-icon" aria-hidden="true">
              <svg className="h-4 w-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="2.25" y="3.25" width="11.5" height="10.5" rx="1.75" />
                <path d="M5.25 2v2.25M10.75 2v2.25M2.25 6.25h11.5M6.25 9.75l1.35 1.35 2.4-2.4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <span className="project-navigation__hub-label">Ações agendadas</span>
          </Link>
        </div>

        <div className="project-navigation__tabs project-navigation__rail-hide" role="tablist" aria-label="Seções da navegação">
          <button
            type="button"
            role="tab"
            aria-label="Conversas (Chats)"
            aria-selected={view === 'chats'}
            aria-controls="navigation-chats"
            onClick={() => changeView('chats')}
          >
            <svg
              className="project-navigation__tab-icon"
              data-testid="sidebar-tab-icon-chats"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              aria-hidden="true"
            >
              <path
                d="M2.5 3.5h11a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H6l-3.5 2.5v-2.5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1Z"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <span>Conversas</span>
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
            <svg
              className="project-navigation__tab-icon"
              data-testid="sidebar-tab-icon-projects"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              aria-hidden="true"
            >
              <path
                d="M2 4.5A1.5 1.5 0 0 1 3.5 3h2.88a1.5 1.5 0 0 1 1.06.44l.87.87a.5.5 0 0 0 .35.15h3.84A1.5 1.5 0 0 1 14 5.96v5.54A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5v-7Z"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <span>Projetos</span>
            <small>{projects.length}</small>
          </button>
        </div>

        <div className="project-navigation__scroll project-navigation__rail-hide">
          <AnimatePresence mode="wait" initial={false}>
            {view === 'chats' ? (
              <motion.div
                id="navigation-chats"
                role="tabpanel"
                aria-label="Chats independentes"
                key="chats"
                initial={reduced ? false : { y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduced ? undefined : { y: -4 }}
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
                      {chat.state !== 'running' && (
                        <span
                          className="project-navigation__chat-time"
                          data-testid={`conversation-time-${chat.conversation_id}`}
                        >
                          {formatRelativeShort(chat.updated_at)}
                        </span>
                      )}
                      <span
                        className="project-navigation__chat-state"
                        data-testid={`conversation-state-${chat.conversation_id}`}
                        data-state={chat.state}
                        aria-hidden="true"
                      />
                    </NavLink>
                  ))
                ) : (
                  <p className="project-navigation__empty">
                    <svg
                      className="project-navigation__empty-icon"
                      data-testid="sidebar-empty-chats-icon"
                      viewBox="0 0 16 16"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      aria-hidden="true"
                    >
                      <path
                        d="M8 2.25a5.75 5.75 0 0 0-4.96 8.66L2.25 13.75l2.84-.79A5.75 5.75 0 1 0 8 2.25Z"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    <span>Suas novas conversas aparecerão aqui</span>
                  </p>
                )}
              </motion.div>
            ) : (
              <motion.div
                id="navigation-projects"
                role="tabpanel"
                aria-label="Projetos"
                key="projects"
                initial={reduced ? false : { y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduced ? undefined : { y: -4 }}
              >
                <div className="project-navigation__section-heading">
                  <span>Seus projetos</span>
                  {projects.length > 0 && <small>{projects.length} {projects.length === 1 ? 'projeto' : 'projetos'}</small>}
                  <Link
                    to="/projects"
                    className="project-navigation__view-all-link"
                    data-testid="sidebar-view-all-projects"
                    onClick={() => setMobileOpen(false)}
                  >
                    Ver todos os projetos
                  </Link>
                </div>
                {projects.length > 0 ? (
                  projects.map((project, projectIdx) => (
                    <section key={project.project_id} className="project-navigation__project">
                      <div className={activeProjectId === project.project_id ? 'project-navigation__project-heading is-active' : 'project-navigation__project-heading'}>
                        {!hideProjectChatSublist && (
                          <button
                            type="button"
                            className="project-navigation__chevron"
                            onClick={() => toggleProject(project.project_id)}
                            aria-label={`Alternar ${project.name}`}
                            aria-expanded={!collapsed.has(project.project_id)}
                          >
                            {collapsed.has(project.project_id) ? '›' : '⌄'}
                          </button>
                        )}
                        <Link
                          to={`/projects/${encodeURIComponent(project.project_id)}`}
                          className="project-navigation__project-name"
                          title={project.name}
                          onClick={() => setMobileOpen(false)}
                        >
                          <span
                            className={`project-navigation__project-folder is-color-${projectIdx % 3}`}
                            data-testid={`project-folder-icon-${project.project_id}`}
                            aria-hidden="true"
                          >
                            <svg viewBox="0 0 16 16" fill="currentColor" className="h-3.5 w-3.5">
                              <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h2.88a1.5 1.5 0 0 1 1.06.44l.87.87a.5.5 0 0 0 .35.15h3.84A1.5 1.5 0 0 1 14 5.96v5.54A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5v-7Z" />
                            </svg>
                          </span>
                          <span className="project-navigation__project-name-text">{project.name}</span>
                        </Link>
                        <span
                          className="project-navigation__project-count"
                          data-testid={`project-count-${project.project_id}`}
                        >
                          {project.chats.length} {project.chats.length === 1 ? 'conversa' : 'conversas'}
                        </span>
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
                      {!hideProjectChatSublist && !collapsed.has(project.project_id) &&
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
                      {!hideProjectChatSublist && !collapsed.has(project.project_id) && project.chats.length === 0 && (
                        <p className="project-navigation__empty project-navigation__empty--project">Ainda não há chats neste projeto.</p>
                      )}
                    </section>
                  ))
                ) : (
                  <p className="project-navigation__empty">
                    <svg
                      className="project-navigation__empty-icon"
                      data-testid="sidebar-empty-projects-icon"
                      viewBox="0 0 16 16"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      aria-hidden="true"
                    >
                      <path
                        d="M2 4.5A1.5 1.5 0 0 1 3.5 3h2.88a1.5 1.5 0 0 1 1.06.44l.87.87a.5.5 0 0 0 .35.15h3.84A1.5 1.5 0 0 1 14 5.96v5.54A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5v-7Z"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    <span>Crie um projeto para organizar conversas e contexto</span>
                  </p>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <div className="project-navigation__actions">
          {view === 'projects' && (
            <button
              type="button"
              className="project-navigation__action project-navigation__action--primary"
              title="Novo projeto"
              aria-label="Novo projeto"
              onClick={() => setCreatingProject(true)}
            >
              <span className="project-navigation__action-icon" aria-hidden="true">+</span>
              <span className="project-navigation__rail-hide">Novo projeto</span>
            </button>
          )}

          <div className="project-navigation__hubs project-navigation__footer-hubs" role="group" aria-label="Links do sistema">
            <Link
              to="/library/skills"
              onClick={() => setMobileOpen(false)}
              aria-current={isLibraryActive ? 'page' : undefined}
              aria-label="Habilidades (Biblioteca)"
              className={`project-navigation__hub-link${isLibraryActive ? ' is-active' : ''}`}
              title="Habilidades"
            >
              <span className="project-navigation__hub-icon" aria-hidden="true">
                <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="2.25" y="2.25" width="4.75" height="4.75" rx="1" />
                  <rect x="9" y="2.25" width="4.75" height="4.75" rx="1" />
                  <rect x="2.25" y="9" width="4.75" height="4.75" rx="1" />
                  <rect x="9" y="9" width="4.75" height="4.75" rx="1" />
                </svg>
              </span>
              <span className="project-navigation__hub-label">Habilidades</span>
            </Link>

            <Link
              to="/integrations/providers"
              onClick={() => setMobileOpen(false)}
              aria-current={isIntegrationsActive ? 'page' : undefined}
              aria-label="Conexões (Integrações)"
              className={`project-navigation__hub-link${isIntegrationsActive ? ' is-active' : ''}`}
              title="Conexões"
            >
              <span className="project-navigation__hub-icon" aria-hidden="true">
                <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M6.5 9.5 9.5 6.5M5.25 11.25l-1 1a2.12 2.12 0 0 1-3-3l1.5-1.5a2.12 2.12 0 0 1 3 0M10.75 4.75l1-1a2.12 2.12 0 0 1 3 3l-1.5 1.5a2.12 2.12 0 0 1-3 0" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
              <span className="project-navigation__hub-label">Conexões</span>
            </Link>

            <Link
              to="/settings/general"
              onClick={() => setMobileOpen(false)}
              aria-current={isSettingsActive ? 'page' : undefined}
              aria-label="Configurações"
              className={`project-navigation__hub-link${isSettingsActive ? ' is-active' : ''}`}
              title="Configurações"
            >
              <span className="project-navigation__hub-icon" aria-hidden="true">
                <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <circle cx="8" cy="8" r="2.25" />
                  <path d="M8 2.25v1.5M8 12.25v1.5M2.25 8h1.5M12.25 8h1.5M3.93 3.93l1.06 1.06M11.01 11.01l1.06 1.06M12.07 3.93l-1.06 1.06M4.99 11.01l-1.06 1.06" strokeLinecap="round" />
                </svg>
              </span>
              <span className="project-navigation__hub-label">Configurações</span>
            </Link>
          </div>

          <div className="project-navigation__footer">
            <div className="project-navigation__footer-profile project-navigation__rail-hide">
              <ProfileMenu showLocalFallback onOpenPreferences={handleOpenPreferences} />
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
                <span className="visually-hidden">Preferências</span>
              </button>
            </div>
          </div>
          {error && <p className="workspace-navigation__error project-navigation__rail-hide" role="status">{error}</p>}
        </div>
      </nav>
      {creatingProject && (
        <div className="project-dialog" role="dialog" aria-modal="true" aria-label="Criar projeto">
          <form onSubmit={(event) => { event.preventDefault(); void submitProject() }}>
            <div className="project-dialog__header">
              <h2>Criar projeto</h2>
              <p className="project-dialog__subtitle">Agrupe conversas, arquivos e contexto em um único workspace.</p>
            </div>
            <label>
              Nome
              <input autoFocus value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="Ex.: Orin Core" maxLength={120} />
            </label>
            <label>
              Descrição
              <input value={projectDescription} onChange={(event) => setProjectDescription(event.target.value)} placeholder="Objetivo ou escopo do projeto (opcional)" maxLength={2000} />
            </label>
            <label>
              Pasta local (opcional)
              <div className="project-dialog__path-row">
                <input
                  data-testid="new-project-workspace-path"
                  value={projectWorkspacePath}
                  onChange={(event) => setProjectWorkspacePath(event.target.value)}
                  placeholder="/caminho/da/pasta"
                  maxLength={1024}
                />
                <button
                  type="button"
                  className="button button--quiet project-dialog__pick-btn"
                  data-testid="new-project-pick-folder"
                  disabled={pickingProjectFolder}
                  onClick={() => void handlePickProjectFolder()}
                >
                  {pickingProjectFolder ? 'Abrindo…' : 'Selecionar pasta…'}
                </button>
              </div>
            </label>
            {pickingProjectFolder && (
              <p className="project-dialog__notice" role="status">
                O seletor do sistema está aberto. Se não aparecer, cole o caminho acima.
              </p>
            )}
            {projectPickerNotice && (
              <p className="project-dialog__notice" role="status">
                {projectPickerNotice}
              </p>
            )}
            <div className="project-dialog__actions">
              <button type="button" className="button button--quiet" onClick={() => setCreatingProject(false)}>
                Cancelar
              </button>
              <button type="submit" className="button button--primary">
                Criar
              </button>
            </div>
          </form>
        </div>
      )}
    </>
  )
}
