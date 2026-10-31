import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import { Link } from 'react-router-dom'
import type { ApiClient } from '../../api/client'
import type { CodePermissionMode } from '../../api/codeMode'
import type { ConversationMode } from '../../api/conversations'
import type { FolderInspection, InspectionOutcome, WorkspaceRisk, WorkspaceState } from '../../api/workspace'
import { useSession } from '../../app/useSession'
import { ProfileFolderBrowser } from '../files/ProfileFolderBrowser'

export const SAFE_PERMISSION_CYCLE: readonly CodePermissionMode[] = [
  'accept_edits',
  'plan_mode',
  'ask_permissions',
  'full_autonomy',
]

export function formatPermissionModePill(mode: CodePermissionMode): string {
  switch (mode) {
    case 'accept_edits':
      return 'Aceitar edições'
    case 'plan_mode':
      return 'Modo Plano'
    case 'ask_permissions':
      return 'Pedir permissão'
    case 'full_autonomy':
      return 'Sem restrições'
    case 'auto':
      return 'Automático'
    case 'dont_ask':
      return 'Não perguntar'
  }
}

export function formatPermissionModeTitle(mode: CodePermissionMode): string {
  switch (mode) {
    case 'accept_edits':
      return 'Modo de permissão: Aceitar edições (Shift+Tab para alternar)'
    case 'plan_mode':
      return 'Modo de permissão: Modo Plano (Shift+Tab para alternar)'
    case 'ask_permissions':
      return 'Modo de permissão: Pedir permissão (Shift+Tab para alternar)'
    case 'full_autonomy':
      return 'Modo de permissão: Sem restrições (Shift+Tab para alternar)'
    case 'auto':
      return 'Modo de permissão: Automático (Shift+Tab para alternar)'
    case 'dont_ask':
      return 'Modo de permissão: Não perguntar (Shift+Tab para alternar)'
  }
}

export function WorkspaceFolderIcon() {
  return (
    <svg
      aria-hidden="true"
      className="session-context-popover__icon"
      viewBox="0 0 16 16"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2.2 4.4A1.4 1.4 0 0 1 3.6 3h2.7l1.4 1.5h4.7a1.4 1.4 0 0 1 1.4 1.4v5.7A1.4 1.4 0 0 1 12.4 13H3.6a1.4 1.4 0 0 1-1.4-1.4V4.4Z" />
    </svg>
  )
}

export function GitBranchIcon() {
  return (
    <svg
      aria-hidden="true"
      className="session-context-popover__icon"
      viewBox="0 0 16 16"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="5" cy="4" r="1.6" />
      <circle cx="5" cy="12" r="1.6" />
      <circle cx="11.5" cy="6" r="1.6" />
      <path d="M5 5.6v4.8" />
      <path d="M11.5 7.6c0 2-1.5 2.4-6.5 2.4" />
    </svg>
  )
}

export function PermissionModeIcon({ mode }: { mode: CodePermissionMode }) {
  switch (mode) {
    case 'ask_permissions':
      return (
        <svg
          aria-hidden="true"
          className="session-context-popover__icon"
          viewBox="0 0 16 16"
          width="13"
          height="13"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <rect x="3.5" y="7" width="9" height="6.5" rx="1.5" />
          <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" />
        </svg>
      )
    case 'accept_edits':
      return (
        <svg
          aria-hidden="true"
          className="session-context-popover__icon"
          viewBox="0 0 16 16"
          width="13"
          height="13"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="m2.5 8.5 2.8 2.8 5.2-5.6" />
          <path d="m7.5 8.5 2.2 2.3 3.8-4.1" />
        </svg>
      )
    case 'plan_mode':
      return (
        <svg
          aria-hidden="true"
          className="session-context-popover__icon"
          viewBox="0 0 16 16"
          width="13"
          height="13"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <rect x="3" y="2.5" width="10" height="11" rx="1.5" />
          <path d="M5.5 6h5" />
          <path d="M5.5 8.5h5" />
          <path d="M5.5 11h3" />
        </svg>
      )
    case 'full_autonomy':
      return (
        <svg
          aria-hidden="true"
          className="session-context-popover__icon"
          viewBox="0 0 16 16"
          width="13"
          height="13"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M4.5 5.5 7.5 8l-3 2.5" />
          <path d="M8.5 10.5h3" />
          <rect x="2" y="2.5" width="12" height="11" rx="2" />
        </svg>
      )
    case 'auto':
    case 'dont_ask':
      return (
        <svg
          aria-hidden="true"
          className="session-context-popover__icon"
          viewBox="0 0 16 16"
          width="13"
          height="13"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="8" cy="8" r="5.5" />
          <circle cx="8" cy="8" r="2" />
        </svg>
      )
  }
}

const PERMISSION_OPTIONS: ReadonlyArray<{ mode: CodePermissionMode; label: string; detail: string }> = [
  { mode: 'ask_permissions', label: 'Pedir permissão', detail: 'Confirma comandos e edições sensíveis' },
  { mode: 'accept_edits', label: 'Aceitar edições', detail: 'Aplica edições em arquivos automaticamente' },
  { mode: 'plan_mode', label: 'Modo Plano', detail: 'Apenas leitura e planejamento antes de agir' },
  { mode: 'full_autonomy', label: 'Sem restrições', detail: 'Executa ferramentas sem confirmação e sem restrição ao workspace' },
]

/**
 * W7 (W6): what "Sem restrições" means here. On a local Orin it also reaches outside the session's folder; on a shared
 * server it only skips the confirmations and never leaves the workspace.
 */
export function fullAutonomyDetail(hostFolders: boolean): string {
  return hostFolders
    ? 'Executa ferramentas sem confirmação, inclusive fora da pasta da sessão'
    : 'Executa ferramentas sem confirmação, sempre dentro do workspace'
}

const RISK_SENTENCE: Record<Exclude<WorkspaceRisk, 'none'>, string> = {
  drive_root: 'é um drive inteiro',
  system: 'é uma pasta de sistema',
  home_root: 'é a sua pasta pessoal inteira',
  orin_data: 'é a pasta de dados do próprio Orin',
}

const DEFAULT_MANAGED_WORKSPACE: WorkspaceState = {
  kind: 'managed',
  path: null,
  folderName: null,
  scope: 'chat',
  projectName: null,
}

export type SessionProjectOption = {
  project_id: string
  name: string
  description?: string | null
}

export type SessionContextPopoverProps = {
  mode: ConversationMode
  permissionMode?: CodePermissionMode
  onPermissionModeChange?: (mode: CodePermissionMode) => void
  worktreeIsolated?: boolean
  onWorktreeIsolatedChange?: (isolated: boolean) => void
  worktreeBranch?: string | null
  workspaceState?: WorkspaceState
  onInspectWorkspace?: (path: string | null) => Promise<InspectionOutcome>
  onAttachWorkspace?: (path: string, acknowledgedRisk: boolean) => Promise<WorkspaceState>
  onDetachWorkspace?: () => Promise<WorkspaceState>
  onWorkspaceChange?: (state: WorkspaceState) => void
  client?: ApiClient
  projects?: SessionProjectOption[]
  activeProjectId?: string | null
  activeProjectName?: string | null
  onMoveToProject?: (projectId: string | null) => void | Promise<void>
  onCreateProjectFromSession?: (input: { name: string; description: string }) => void | Promise<void>
  defaultNewProjectName?: string
  disabled?: boolean
}

export function SessionContextPopover({
  mode,
  permissionMode,
  onPermissionModeChange,
  worktreeIsolated,
  onWorktreeIsolatedChange,
  worktreeBranch = null,
  workspaceState = DEFAULT_MANAGED_WORKSPACE,
  onInspectWorkspace,
  onAttachWorkspace,
  onDetachWorkspace,
  onWorkspaceChange,
  client,
  projects = [],
  activeProjectId = null,
  activeProjectName = null,
  onMoveToProject,
  onCreateProjectFromSession,
  defaultNewProjectName = '',
  disabled = false,
}: SessionContextPopoverProps) {
  const { capabilities } = useSession()
  const profileArea = capabilities.profile_files && client !== undefined
  const containerRef = useRef<HTMLDivElement>(null)
  const inspectionRequest = useRef(0)

  const [open, setOpen] = useState(false)
  const [panelPlacement, setPanelPlacement] = useState<'top' | 'bottom'>('top')
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({})
  const [internalPermissionMode, setInternalPermissionMode] = useState<CodePermissionMode>(
    permissionMode ?? (mode === 'code' ? 'accept_edits' : 'ask_permissions'),
  )
  const [internalWorktreeIsolated, setInternalWorktreeIsolated] = useState(false)
  const activePermissionMode = permissionMode ?? internalPermissionMode
  const activeWorktreeIsolated = worktreeIsolated ?? internalWorktreeIsolated

  const [candidate, setCandidate] = useState<FolderInspection | null>(null)
  const [typedPath, setTypedPath] = useState('')
  const [actionBusy, setActionBusy] = useState(false)
  const [manualInspecting, setManualInspecting] = useState(false)
  const [dialogBusy, setDialogBusy] = useState(false)
  const [workspaceNotice, setWorkspaceNotice] = useState<string | null>(null)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)

  const [newProjectName, setNewProjectName] = useState(defaultNewProjectName)
  const [newProjectDescription, setNewProjectDescription] = useState('')
  const [projectBusy, setProjectBusy] = useState(false)

  useLayoutEffect(() => {
    if (!open) return
    const updatePosition = () => {
      const el = containerRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      if (rect.width === 0 && rect.height === 0) {
        setPanelPlacement('top')
        setPanelStyle({})
        return
      }
      const clipAncestor = el.closest('.home__stage, .chat__body, .workspace-shell__canvas')
      const clipRect = clipAncestor?.getBoundingClientRect()
      const viewportHeight = typeof window !== 'undefined' && window.innerHeight > 0 ? window.innerHeight : 800
      const viewportWidth = typeof window !== 'undefined' && window.innerWidth > 0 ? window.innerWidth : 1280
      const topBoundary = Math.max(56, (clipRect && clipRect.height > 0 ? clipRect.top : 48) + 12)
      const bottomBoundary = Math.min(
        viewportHeight - 12,
        (clipRect && clipRect.height > 0 ? clipRect.bottom : viewportHeight) - 12,
      )
      const spaceAbove = Math.floor(rect.top - topBoundary - 10)
      const spaceBelow = Math.floor(bottomBoundary - rect.bottom - 10)
      const panelWidth = Math.min(380, Math.max(260, viewportWidth - 32))
      const minLeft = clipRect && clipRect.width > 0 ? Math.max(16, clipRect.left + 12) : 16
      const maxRight = clipRect && clipRect.width > 0 ? Math.min(viewportWidth - 16, clipRect.right - 12) : viewportWidth - 16
      const clampedLeftViewport = Math.max(minLeft, Math.min(rect.left, maxRight - panelWidth))
      const leftOffset = Math.round(clampedLeftViewport - rect.left)

      if (spaceAbove < 240 && spaceBelow > spaceAbove) {
        setPanelPlacement('bottom')
        setPanelStyle({
          top: 'calc(100% + 8px)',
          bottom: 'auto',
          left: `${leftOffset}px`,
          right: 'auto',
          maxHeight: `${Math.max(200, Math.min(500, spaceBelow))}px`,
        })
      } else {
        setPanelPlacement('top')
        setPanelStyle({
          bottom: 'calc(100% + 8px)',
          top: 'auto',
          left: `${leftOffset}px`,
          right: 'auto',
          maxHeight: `${Math.max(200, Math.min(500, spaceAbove))}px`,
        })
      }
    }

    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  function handleContainerKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
    }
  }

  function toggleOpen() {
    setOpen((current) => {
      const next = !current
      if (!next) {
        inspectionRequest.current += 1
        setDialogBusy(false)
        setManualInspecting(false)
        setCandidate(null)
      } else if (!newProjectName && defaultNewProjectName) {
        setNewProjectName(defaultNewProjectName)
      }
      return next
    })
  }

  function changePermissionMode(next: CodePermissionMode) {
    setInternalPermissionMode(next)
    onPermissionModeChange?.(next)
  }

  function stepPermissionMode() {
    if (activePermissionMode === 'auto' || activePermissionMode === 'dont_ask') {
      changePermissionMode('ask_permissions')
      return
    }
    const idx = SAFE_PERMISSION_CYCLE.indexOf(activePermissionMode)
    const next = SAFE_PERMISSION_CYCLE[(idx === -1 ? 0 : idx + 1) % SAFE_PERMISSION_CYCLE.length]
    changePermissionMode(next)
  }

  function changeWorktreeIsolated(next: boolean) {
    setInternalWorktreeIsolated(next)
    onWorktreeIsolatedChange?.(next)
  }

  async function inspect(path: string | null) {
    if (!onInspectWorkspace) return
    const requestId = ++inspectionRequest.current
    const nativeDialog = path === null
    if (nativeDialog) setDialogBusy(true)
    else setManualInspecting(true)
    setWorkspaceError(null)
    setWorkspaceNotice(null)
    try {
      const outcome = await onInspectWorkspace(path)
      if (requestId !== inspectionRequest.current) return
      if (outcome.kind === 'cancelled') return
      if (outcome.kind === 'unavailable') {
        setWorkspaceNotice('Não foi possível abrir o seletor do sistema. Cole o caminho da pasta abaixo.')
        return
      }
      if (!outcome.isDirectory) {
        setWorkspaceError(outcome.exists ? 'Esse caminho não é uma pasta.' : 'Essa pasta não existe.')
        return
      }
      if (!outcome.writable) {
        setWorkspaceError('Sem permissão de escrita nessa pasta.')
        return
      }
      setCandidate(outcome)
    } catch {
      if (requestId === inspectionRequest.current) {
        setWorkspaceError('Não foi possível inspecionar a pasta.')
      }
    } finally {
      if (nativeDialog) setDialogBusy(false)
      else setManualInspecting(false)
    }
  }

  async function attach(inspection: FolderInspection) {
    if (!onAttachWorkspace) return
    setActionBusy(true)
    setWorkspaceError(null)
    try {
      const nextState = await onAttachWorkspace(inspection.path, inspection.risk !== 'none')
      onWorkspaceChange?.(nextState)
      setCandidate(null)
      setOpen(false)
    } catch {
      setWorkspaceError('Não foi possível usar essa pasta.')
    } finally {
      setActionBusy(false)
    }
  }

  async function detach() {
    if (!onDetachWorkspace) return
    setActionBusy(true)
    setWorkspaceError(null)
    try {
      const nextState = await onDetachWorkspace()
      onWorkspaceChange?.(nextState)
      setOpen(false)
    } catch {
      setWorkspaceError('Não foi possível remover a pasta.')
    } finally {
      setActionBusy(false)
    }
  }

  async function handleMoveToProject(targetProjectId: string | null) {
    if (!onMoveToProject || projectBusy) return
    setProjectBusy(true)
    try {
      await onMoveToProject(targetProjectId)
    } finally {
      setProjectBusy(false)
    }
  }

  async function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedName = newProjectName.trim()
    if (!trimmedName || !onCreateProjectFromSession || projectBusy) return
    setProjectBusy(true)
    try {
      await onCreateProjectFromSession({
        name: trimmedName,
        description: newProjectDescription.trim(),
      })
      setNewProjectDescription('')
    } finally {
      setProjectBusy(false)
    }
  }

  const folderLabel =
    workspaceState.kind === 'local'
      ? workspaceState.folderName || workspaceState.path || 'Pasta'
      : workspaceState.kind === 'unavailable'
        ? 'Pasta indisponível'
        : 'Workspace padrão'

  const triggerAriaLabel =
    workspaceState.kind === 'local'
      ? `Diretório do workspace: ${folderLabel}`
      : workspaceState.kind === 'unavailable'
        ? 'Pasta indisponível no servidor'
        : 'Adicionar pasta ao workspace'

  const showProjectSection = Boolean(
    onMoveToProject || onCreateProjectFromSession || projects.length > 0 || activeProjectId,
  )

  return (
    <div
      className="session-context-popover workspace-folder"
      ref={containerRef}
      onKeyDown={handleContainerKeyDown}
    >
      <div className="session-context-popover__bar">
        <button
          type="button"
          className={`session-context-popover__trigger workspace-folder__button${workspaceState.kind === 'local' ? ' is-attached' : ''}`}
          data-testid="session-context-trigger"
          disabled={disabled}
          aria-expanded={open}
          aria-haspopup="dialog"
          aria-label={triggerAriaLabel}
          title={workspaceState.path ?? undefined}
          onClick={toggleOpen}
        >
          <WorkspaceFolderIcon />
          <span className="session-context-popover__folder">{folderLabel}</span>
          {activeProjectName && (
            <span className="session-context-popover__project-tag">· {activeProjectName}</span>
          )}
          {mode === 'code' && activeWorktreeIsolated && (
            <span className="session-context-popover__worktree-tag">
              · <GitBranchIcon /> {worktreeBranch || 'Worktree'}
            </span>
          )}
          <span className="chip__caret" aria-hidden="true">⌄</span>
        </button>

        <button
          type="button"
          className="composer__permission-pill composer__permission-mode session-context-popover__permission-pill"
          data-testid="code-permission-mode-pill"
          data-permission-mode={activePermissionMode}
          aria-label="Modo de permissão do Code"
          title={formatPermissionModeTitle(activePermissionMode)}
          disabled={disabled}
          onClick={() => stepPermissionMode()}
        >
          <PermissionModeIcon mode={activePermissionMode} />
          <span>{formatPermissionModePill(activePermissionMode)}</span>
        </button>
        <select
          className="visually-hidden"
          data-testid="code-permission-mode-select"
          aria-label="Selecionar modo de permissão do Code"
          tabIndex={-1}
          value={activePermissionMode}
          onChange={(event) => changePermissionMode(event.target.value as CodePermissionMode)}
        >
          <option value="ask_permissions">Pedir permissão</option>
          <option value="accept_edits">Aceitar edições</option>
          <option value="plan_mode">Modo Plano</option>
          <option value="full_autonomy">Sem restrições</option>
        </select>
      </div>

      {open && (
        <div
          className="session-context-popover__panel workspace-folder__panel"
          role="dialog"
          aria-label="Contexto da sessão"
          data-testid="session-context-dialog"
          data-placement={panelPlacement}
          style={panelStyle}
        >
          <section className="session-context-popover__section">
            {candidate ? (
              <>
                <h3>Confirmar diretório</h3>
                <p className="workspace-folder__path">{candidate.path}</p>
                <p className="workspace-folder__meta">
                  {candidate.entryCount}{candidate.entriesTruncated ? '+' : ''} itens no primeiro nível
                </p>
                {workspaceState.scope === 'project' && workspaceState.projectName && (
                  <p className="workspace-folder__meta">
                    Vale para todos os chats do projeto {workspaceState.projectName}.
                  </p>
                )}
                {candidate.risk === 'none' ? (
                  <button
                    type="button"
                    className="session-context-popover__btn session-context-popover__btn--primary"
                    disabled={actionBusy}
                    onClick={() => void attach(candidate)}
                  >
                    Usar esta pasta
                  </button>
                ) : (
                  <>
                    <p className="workspace-folder__risk">
                      Essa pasta {RISK_SENTENCE[candidate.risk]}. O agente vai poder criar, editar e apagar arquivos em {candidate.path}, com shell real, sem pedir permissão a cada passo.
                    </p>
                    <button
                      type="button"
                      className="workspace-folder__risk-action session-context-popover__btn"
                      disabled={actionBusy}
                      onClick={() => void attach(candidate)}
                    >
                      Trabalhar em {candidate.path} mesmo assim
                    </button>
                  </>
                )}
                <button
                  type="button"
                  className="session-context-popover__btn"
                  disabled={actionBusy}
                  onClick={() => setCandidate(null)}
                >
                  Cancelar
                </button>
              </>
            ) : (
              <>
                <div className="session-context-popover__section-head">
                  <h3>{capabilities.profile_files ? 'Pasta do projeto' : 'Pasta de trabalho'}</h3>
                  <p className="workspace-folder__meta">
                    Escolha um diretório para o agente trabalhar neste {workspaceState.scope === 'project' ? 'projeto' : 'chat'}.
                  </p>
                </div>
                {workspaceState.kind === 'local' ? (
                  <p className="workspace-folder__path">{workspaceState.path}</p>
                ) : workspaceState.kind === 'unavailable' ? (
                  <p className="workspace-folder__risk">
                    A pasta {workspaceState.folderName} era do computador onde o Orin rodava antes. Neste servidor, escolha uma pasta da sua área de arquivos.
                  </p>
                ) : (
                  <p className="workspace-folder__meta">
                    Sem pasta local. O agente trabalha na pasta gerenciada pelo Orin.
                  </p>
                )}
                {profileArea ? (
                  <ProfileFolderBrowser client={client} onChoose={(path) => void inspect(path)} />
                ) : onInspectWorkspace ? (
                  <div className="session-context-popover__workspace-controls">
                    <button
                      type="button"
                      className="session-context-popover__btn session-context-popover__btn--primary"
                      disabled={actionBusy || dialogBusy}
                      onClick={() => void inspect(null)}
                    >
                      Selecionar diretório…
                    </button>
                    <label className="workspace-folder__field">
                      Caminho da pasta
                      <input
                        value={typedPath}
                        disabled={actionBusy || manualInspecting}
                        onChange={(event) => setTypedPath(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') {
                            event.preventDefault()
                            if (typedPath.trim()) void inspect(typedPath.trim())
                          }
                        }}
                        placeholder="C:\projetos\meu-app"
                      />
                    </label>
                    <button
                      type="button"
                      className="session-context-popover__btn"
                      disabled={actionBusy || manualInspecting || !typedPath.trim()}
                      onClick={() => void inspect(typedPath.trim())}
                    >
                      Adicionar diretório
                    </button>
                  </div>
                ) : null}
                {workspaceState.kind !== 'managed' && onDetachWorkspace && (
                  <button
                    type="button"
                    className="session-context-popover__btn"
                    disabled={actionBusy}
                    onClick={() => void detach()}
                  >
                    Remover
                  </button>
                )}
              </>
            )}
            {dialogBusy && (
              <p className="workspace-folder__notice" role="status">
                O seletor do Windows está aberto. Se ele não aparecer, cole o caminho abaixo.
              </p>
            )}
            {workspaceNotice && (
              <p className="workspace-folder__notice" role="status">
                {workspaceNotice}
              </p>
            )}
            {workspaceError && (
              <p className="workspace-folder__error" role="alert">
                {workspaceError}
              </p>
            )}
          </section>

          <section className="session-context-popover__section">
            <div className="session-context-popover__section-head session-context-popover__section-head--row">
              <h3>Modo de permissão</h3>
              <kbd>Shift+Tab</kbd>
            </div>
            <div className="session-context-popover__permission-grid">
              {PERMISSION_OPTIONS.map((item) => {
                const isSelected = activePermissionMode === item.mode
                return (
                  <button
                    key={item.mode}
                    type="button"
                    aria-pressed={isSelected}
                    className={`session-context-popover__permission-option${isSelected ? ' is-selected' : ''}`}
                    onClick={() => changePermissionMode(item.mode)}
                  >
                    <span className="session-context-popover__permission-label">
                      <PermissionModeIcon mode={item.mode} />
                      <span>{item.label}</span>
                    </span>
                    <small className="session-context-popover__permission-detail">{item.mode === 'full_autonomy' ? fullAutonomyDetail(capabilities.host_folders) : item.detail}</small>
                  </button>
                )
              })}
            </div>
          </section>

          {mode === 'code' && (
            <section className="session-context-popover__section session-context-popover__section--worktree">
              <div className="session-context-popover__section-head session-context-popover__section-head--row">
                <h3>Isolamento Git Worktree</h3>
              </div>
              <button
                type="button"
                className={`composer__worktree session-context-popover__worktree-btn${activeWorktreeIsolated ? ' is-active' : ''}`}
                data-testid="code-worktree-toggle"
                aria-pressed={activeWorktreeIsolated}
                aria-label={activeWorktreeIsolated ? 'Desativar Worktree isolado' : 'Ativar Worktree isolado'}
                title={
                  activeWorktreeIsolated
                    ? `Worktree isolado ativo${worktreeBranch ? ` (${worktreeBranch})` : ''}`
                    : 'Isolar sessão em um Git Worktree (.orin/worktrees/)'
                }
                onClick={() => changeWorktreeIsolated(!activeWorktreeIsolated)}
              >
                <GitBranchIcon />
                <span>{activeWorktreeIsolated && worktreeBranch ? worktreeBranch : 'Worktree'}</span>
              </button>
            </section>
          )}

          {showProjectSection && (
            <section className="session-context-popover__section" data-testid="conversation-project-menu">
              <div className="session-context-popover__section-head session-context-popover__section-head--row">
                <h3>Projeto</h3>
                {activeProjectId && activeProjectName && (
                  <Link
                    to={`/projects/${encodeURIComponent(activeProjectId)}`}
                    className="chat__workspace-chip"
                    data-testid="conversation-project-badge"
                  >
                    {activeProjectName}
                  </Link>
                )}
              </div>

              {projects.length > 0 && (
                <div className="session-context-popover__project-list">
                  {projects.map((proj) => (
                    <button
                      key={proj.project_id}
                      type="button"
                      className="ghost-button session-context-popover__project-item"
                      disabled={projectBusy || proj.project_id === activeProjectId}
                      onClick={() => void handleMoveToProject(proj.project_id)}
                    >
                      {proj.name}
                    </button>
                  ))}
                </div>
              )}

              {activeProjectId && onMoveToProject && (
                <button
                  type="button"
                  className="ghost-button session-context-popover__project-item"
                  data-testid="remove-from-project-button"
                  disabled={projectBusy}
                  onClick={() => void handleMoveToProject(null)}
                >
                  Remover do projeto (sessão avulsa)
                </button>
              )}

              {onCreateProjectFromSession && (
                <form
                  className="session-context-popover__project-form"
                  data-testid="create-project-from-session-form"
                  onSubmit={(event) => void handleCreateProject(event)}
                >
                  <input
                    type="text"
                    name="project-name"
                    value={newProjectName}
                    onChange={(event) => setNewProjectName(event.target.value)}
                    placeholder="Nome do novo projeto"
                    aria-label="Nome do novo projeto"
                  />
                  <input
                    type="text"
                    name="project-description"
                    value={newProjectDescription}
                    onChange={(event) => setNewProjectDescription(event.target.value)}
                    placeholder="Descrição (opcional)"
                    aria-label="Descrição do novo projeto"
                  />
                  <button
                    type="submit"
                    className="primary-button session-context-popover__btn session-context-popover__btn--primary"
                    disabled={projectBusy || !newProjectName.trim()}
                  >
                    Criar projeto e vincular
                  </button>
                </form>
              )}
            </section>
          )}
        </div>
      )}
    </div>
  )
}
