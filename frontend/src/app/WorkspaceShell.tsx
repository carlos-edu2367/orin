import { useCallback, useEffect, useMemo, useState, type JSX, type ReactNode } from 'react'
import { createBrowserApiClient, type ApiClient } from '../api/client'
import { CommandPalette } from '../components/CommandPalette'
import {
  WorkspaceNavigation,
  type WorkspaceNavigationConversationSummary,
} from '../features/projects/WorkspaceNavigation'
import { PreferencesModal, type PreferencesTab } from '../features/settings/PreferencesModal'

const SIDEBAR_COLLAPSED_STORAGE_KEY = 'orin.sidebar.collapsed'

export function readSidebarCollapsed(): boolean {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return false
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

export function writeSidebarCollapsed(collapsed: boolean): void {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, collapsed ? 'true' : 'false')
    }
  } catch {
    // Ignore storage errors in restricted environments or when quota is exceeded
  }
}

export type WorkspaceShellProps = {
  headerLeft?: ReactNode
  headerCenter?: ReactNode
  headerRight?: ReactNode
  children: ReactNode
  client?: ApiClient
  chats?: WorkspaceNavigationConversationSummary[]
  onChatsChange?: (chats: WorkspaceNavigationConversationSummary[]) => void
  onNewConversation?: () => void
  onNewCodeConversation?: () => void
  initialPreferencesTab?: PreferencesTab
  defaultPreferencesOpen?: boolean
  backdrop?: ReactNode
  className?: string
}

export function WorkspaceShell({
  headerLeft,
  headerCenter,
  headerRight,
  children,
  client,
  chats,
  onChatsChange,
  onNewConversation,
  onNewCodeConversation,
  initialPreferencesTab = 'appearance',
  defaultPreferencesOpen = false,
  backdrop,
  className,
}: WorkspaceShellProps): JSX.Element {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [railCollapsed, setRailCollapsed] = useState<boolean>(() => readSidebarCollapsed())
  const [preferencesOpen, setPreferencesOpen] = useState<boolean>(defaultPreferencesOpen)
  const [preferencesTab, setPreferencesTab] = useState<PreferencesTab>(initialPreferencesTab)
  const [internalChats, setInternalChats] = useState<WorkspaceNavigationConversationSummary[]>([])

  useEffect(() => {
    setPreferencesTab(initialPreferencesTab)
  }, [initialPreferencesTab])

  const toggleRail = useCallback(() => {
    setRailCollapsed((current) => {
      const next = !current
      writeSidebarCollapsed(next)
      return next
    })
  }, [])

  const handleChatsChange = useCallback(
    (nextChats: WorkspaceNavigationConversationSummary[]) => {
      setInternalChats(nextChats)
      onChatsChange?.(nextChats)
    },
    [onChatsChange],
  )

  const handleOpenPreferences = useCallback(
    (tab?: PreferencesTab) => {
      setPreferencesTab(tab ?? initialPreferencesTab)
      setPreferencesOpen(true)
    },
    [initialPreferencesTab],
  )

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key === ',') {
        event.preventDefault()
        setPreferencesOpen((current) => !current)
      }
    }
    const onOpenPreferencesEvent = (event: Event) => {
      const custom = event as CustomEvent<{ tab?: PreferencesTab }>
      handleOpenPreferences(custom.detail?.tab)
    }
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('orin:open-preferences', onOpenPreferencesEvent)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('orin:open-preferences', onOpenPreferencesEvent)
    }
  }, [handleOpenPreferences])

  const effectiveChats = chats ?? internalChats

  return (
    <div
      className={`workspace-shell${className ? ` ${className}` : ''}`}
      data-testid="workspace-shell"
      data-rail-collapsed={railCollapsed ? 'true' : 'false'}
    >
      {backdrop}

      <aside
        className={`workspace-navigation${railCollapsed ? ' is-rail-collapsed' : ''}`}
        data-rail-collapsed={railCollapsed ? 'true' : 'false'}
      >
        <WorkspaceNavigation
          client={apiClient}
          onChatsChange={handleChatsChange}
          onNewConversation={onNewConversation}
          onNewCodeConversation={onNewCodeConversation}
          railCollapsed={railCollapsed}
          onToggleRail={toggleRail}
          onOpenPreferences={() => handleOpenPreferences()}
        />
      </aside>

      <div className="workspace-shell__canvas">
        <header className="canvas-header" data-testid="canvas-header">
          <div className="canvas-header__left">{headerLeft}</div>
          <div className="canvas-header__center">{headerCenter}</div>
          <div className="canvas-header__right">{headerRight}</div>
        </header>
        <div className="canvas-stage">{children}</div>
      </div>

      <div className="workspace-shell__palette-host">
        <CommandPalette conversations={effectiveChats} />
      </div>

      <PreferencesModal
        open={preferencesOpen}
        initialTab={preferencesTab}
        onClose={() => setPreferencesOpen(false)}
        client={apiClient}
      />
    </div>
  )
}
