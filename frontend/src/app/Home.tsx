import { motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { readBrowserSessionBootstrap, type BrowserSessionBootstrap } from '../api/browserSession'
import { ApiClient, createBrowserApiClient } from '../api/client'
import {
  getCodeModeSettings, normalizeCodePermissionMode, permissionModeToAutonomy, setCodeModeSettings,
  type CodePermissionMode,
} from '../api/codeMode'
import { createConversation, ensureFriendConversation, type ConversationMode } from '../api/conversations'
import { listProjectSidebar } from '../api/projects'
import { ApiError, isAuthenticationError, isCsrfAuthorizationError } from '../api/errors'
import { listPluginCommands, type PluginCommand } from '../api/plugins'
import { PROVIDER_NAMES, listProviderModels, type ProviderModel, type ProviderName } from '../api/providers'
import { listSkills, type SkillSummary } from '../api/skills'
import { AmbientField } from '../components/three/AmbientField'
import { CommandPalette } from '../components/CommandPalette'
import { ModelPicker } from '../components/ModelPicker'
import { OrinMascot } from '../components/OrinMascot'
import { Composer } from '../features/conversations/Composer'
import { useComposerAttachments } from '../features/conversations/useComposerAttachments'
import { inspectNewWorkspaceFolder, type WorkspaceState } from '../api/workspace'
import { WorkspaceNavigation, type WorkspaceNavigationConversationSummary } from '../features/projects/WorkspaceNavigation'
import { PreferencesModal } from '../features/settings/PreferencesModal'
import { useTheme } from './ThemeContext'

// Brand identity (<Brand />) is hosted inside WorkspaceNavigation header.

type HomeProps = {
  client?: ApiClient
  bootstrap?: BrowserSessionBootstrap
}

const PROVIDER_STORAGE_KEY = 'agentos.provider'
const MODEL_STORAGE_KEY = 'agentos.model'
const USER_NAME_STORAGE_KEY = 'orin.user.displayName'
const ACTIVE_CONVERSATION_STATES = new Set(['queued', 'starting', 'running', 'streaming', 'cancelling'])

export function formatHomeGreeting(hour?: number, rawName?: string | null): string {
  const resolvedHour = hour === undefined ? new Date().getHours() : hour
  let salutation = 'Boa noite'
  if (
    typeof resolvedHour === 'number' &&
    Number.isFinite(resolvedHour) &&
    resolvedHour >= 0 &&
    resolvedHour <= 23
  ) {
    const normalizedHour = Math.floor(resolvedHour)
    if (normalizedHour >= 5 && normalizedHour <= 11) {
      salutation = 'Bom dia'
    } else if (normalizedHour >= 12 && normalizedHour <= 17) {
      salutation = 'Boa tarde'
    }
  }

  const sourceName =
    rawName === undefined ? readStored(USER_NAME_STORAGE_KEY, 'Carlos') : (rawName ?? '')
  const cleaned = String(sourceName)
    .replace(/[\r\n\t]+/g, ' ')
    .trim()
    .slice(0, 60)
    .trim()
  const safeName = cleaned.length > 0 ? cleaned : 'Carlos'
  return `${salutation}, ${safeName}.`
}

/**
 * The opening screen: one input, contextual quick chips, and an optional ambient field behind it.
 */
export function Home({ client, bootstrap }: HomeProps) {
  const { projectId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const reduced = useReducedMotion()
  const { ambient } = useTheme()
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const session = bootstrap ?? (typeof document === 'undefined' ? { status: 'missing_csrf' as const } : readBrowserSessionBootstrap(document))

  const requestedModeParam = searchParams.get('mode')
  const requestedPromptParam = searchParams.get('prompt')
  const [message, setMessage] = useState(() => requestedPromptParam ?? '')
  const [codeMode, setCodeMode] = useState<ConversationMode>(() => (requestedModeParam === 'code' ? 'code' : 'chat'))
  const [serverPermissionMode, setServerPermissionMode] = useState<CodePermissionMode>('ask_permissions')
  const [codePermissionMode, setCodePermissionMode] = useState<CodePermissionMode>('ask_permissions')
  const userChangedPermissionRef = useRef(false)
  const [codeSystemNotifications, setCodeSystemNotifications] = useState(false)
  const [codeMonitoringEnabled, setCodeMonitoringEnabled] = useState(true)
  const { attachments, onAttach, onRemoveAttachment, canSend: attachmentsReady, readyUploadIds, reset: resetAttachments } = useComposerAttachments(apiClient)
  const [provider, setProvider] = useState<ProviderName>(() => readStored(PROVIDER_STORAGE_KEY, 'openrouter') as ProviderName)
  const [models, setModels] = useState<ProviderModel[]>([])
  const [modelId, setModelId] = useState('')
  const [loadingModels, setLoadingModels] = useState(true)
  const [catalogFailed, setCatalogFailed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [focusSignal, setFocusSignal] = useState(0)
  const [projectName, setProjectName] = useState<string | null>(null)
  const [workspacePath, setWorkspacePath] = useState<string | null>(null)
  const [workspaceAcknowledgedRisk, setWorkspaceAcknowledgedRisk] = useState(false)
  const [chats, setChats] = useState<WorkspaceNavigationConversationSummary[]>([])
  const [pluginCommands, setPluginCommands] = useState<PluginCommand[]>([])
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [preferencesOpen, setPreferencesOpen] = useState(false)
  const activeProjectName = projectId ? projectName : null
  const greeting = formatHomeGreeting()
  const activeTaskCount = useMemo(
    () => chats.filter((chat) => ACTIVE_CONVERSATION_STATES.has(chat.state)).length,
    [chats],
  )

  const [previousRequestedModeParam, setPreviousRequestedModeParam] = useState(requestedModeParam)
  if (requestedModeParam !== previousRequestedModeParam) {
    setPreviousRequestedModeParam(requestedModeParam)
    if (requestedModeParam === 'code') {
      setCodeMode('code')
    } else if (requestedModeParam === 'chat') {
      setCodeMode('chat')
    }
  }

  const [previousRequestedPromptParam, setPreviousRequestedPromptParam] = useState(requestedPromptParam)
  if (requestedPromptParam !== previousRequestedPromptParam) {
    setPreviousRequestedPromptParam(requestedPromptParam)
    if (requestedPromptParam !== null) {
      setMessage(requestedPromptParam)
    }
  }

  const selectMode = useCallback((nextMode: ConversationMode) => {
    setCodeMode(nextMode)
    if (!userChangedPermissionRef.current) {
      setCodePermissionMode(nextMode === 'code' ? serverPermissionMode : 'ask_permissions')
    }
    setSearchParams({ mode: nextMode }, { replace: true })
  }, [serverPermissionMode, setSearchParams])

  useEffect(() => {
    if (!projectId) return
    let active = true
    void listProjectSidebar(apiClient).then((sidebar) => {
      if (active) setProjectName(sidebar.items.find((item) => item.project_id === projectId)?.name ?? null)
    }).catch(() => { if (active) setProjectName(null) })
    return () => { active = false }
  }, [apiClient, projectId])

  useEffect(() => {
    const controller = new AbortController()
    listProviderModels(apiClient, provider, controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return
        setModels(items)
        const remembered = readStored(MODEL_STORAGE_KEY, '')
        const preferred = items.find((item) => item.model_id === remembered)
          ?? items.find((item) => item.is_favorite)
          ?? items[0]
        setModelId(preferred?.model_id ?? '')
      })
      .catch(() => { if (!controller.signal.aborted) { setModels([]); setCatalogFailed(true) } })
      .finally(() => { if (!controller.signal.aborted) setLoadingModels(false) })
    return () => controller.abort()
  }, [apiClient, provider])

  useEffect(() => {
    const controller = new AbortController()
    Promise.allSettled([
      listPluginCommands(apiClient, controller.signal),
      listSkills(apiClient, { limit: 100 }, controller.signal),
      getCodeModeSettings(apiClient, controller.signal),
    ]).then(([commands, availableSkills, codeSettings]) => {
      if (controller.signal.aborted) return
      setPluginCommands(commands.status === 'fulfilled' ? commands.value : [])
      setSkills(availableSkills.status === 'fulfilled' ? availableSkills.value.items : [])
      if (codeSettings.status === 'fulfilled') {
        const resolved = normalizeCodePermissionMode(codeSettings.value.autonomy)
        setServerPermissionMode(resolved)
        if (!userChangedPermissionRef.current) {
          setCodePermissionMode(resolved)
        }
        setCodeSystemNotifications(codeSettings.value.system_notifications)
        setCodeMonitoringEnabled(codeSettings.value.monitoring_enabled)
      }
    })
    return () => controller.abort()
  }, [apiClient])

  const changeCodePermissionMode = useCallback((next: CodePermissionMode) => {
    userChangedPermissionRef.current = true
    setCodePermissionMode(next)
    void setCodeModeSettings(apiClient, {
      autonomy: permissionModeToAutonomy(next),
      system_notifications: codeSystemNotifications,
      monitoring_enabled: codeMonitoringEnabled,
    }).catch(() => undefined)
  }, [apiClient, codeMonitoringEnabled, codeSystemNotifications])

  function changeProvider(next: ProviderName) {
    if (next === provider) return
    setProvider(next)
    setModels([])
    setModelId('')
    setLoadingModels(true)
    setCatalogFailed(false)
  }

  const sessionReady = session.status !== 'missing_csrf'

  async function submit() {
    const text = message.trim()
    const readyUploads = readyUploadIds()
    if ((!text && readyUploads.length === 0) || !modelId || submitting || !sessionReady) return
    setSubmitting(true)
    setError(null)
    const resolvedMode: ConversationMode = codeMode === 'code' ? 'code' : 'chat'
    const resolvedPermissionMode: CodePermissionMode = codePermissionMode || (resolvedMode === 'code' ? serverPermissionMode : 'ask_permissions')
    try {
      if (resolvedMode === 'code') {
        await setCodeModeSettings(apiClient, {
          autonomy: permissionModeToAutonomy(resolvedPermissionMode),
          system_notifications: codeSystemNotifications,
          monitoring_enabled: codeMonitoringEnabled,
        }).catch(() => undefined)
      }
      const receipt = await createConversation(apiClient, {
        message: text,
        provider,
        model_id: modelId,
        project_id: projectId,
        workspace_path: workspacePath,
        workspace_acknowledged_risk: workspaceAcknowledgedRisk,
        attachments: readyUploads,
        code_mode: resolvedMode,
        mode: resolvedMode,
        permission_mode: resolvedPermissionMode,
      })
      writeStored(PROVIDER_STORAGE_KEY, provider)
      writeStored(MODEL_STORAGE_KEY, modelId)
      resetAttachments()
      navigate(projectId
        ? `/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(receipt.conversation_id)}`
        : `/chats/${encodeURIComponent(receipt.conversation_id)}`)
    } catch (caught) {
      setError(errorHeadline(caught))
      setSubmitting(false)
      setFocusSignal((value) => value + 1)
    }
  }

  function resetDraftForMode(targetMode: ConversationMode) {
    if (projectId) {
      navigate(`/?mode=${targetMode}`)
      return
    }
    selectMode(targetMode)
    setMessage('')
    resetAttachments()
    setWorkspacePath(null)
    setWorkspaceAcknowledgedRisk(false)
    setError(null)
    setFocusSignal((value) => value + 1)
  }

  return (
    <main className="home">
      <div className="home__backdrop" aria-hidden="true">
        {ambient === 'on' && (
          <>
            <div className="home__glow" />
            <AmbientField active={submitting} />
          </>
        )}
      </div>

      <header className="home__bar canvas-header" data-testid="canvas-header">
        <div className="canvas-header__left">
          <span className="home__topbar-title" data-testid="home-topbar-title">
            Início
          </span>
          <ModelPicker
            variant="unified"
            providers={[...PROVIDER_NAMES]}
            provider={provider}
            onProviderChange={changeProvider}
            models={models}
            modelId={modelId}
            onModelChange={setModelId}
            loading={loadingModels}
            failed={catalogFailed}
            disabled={submitting}
          />
        </div>
        <div className="canvas-header__center" />
        <div className="canvas-header__right">
          <button
            type="button"
            className="canvas-header__voice-btn"
            data-testid="home-voice-cta"
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
          <CommandPalette conversations={chats} />
          <OrinMascot size={30} variant="flat" statusDot="online" ariaLabel="Orin ativo" />
        </div>
      </header>

      {preferencesOpen && (
        <PreferencesModal
          open={preferencesOpen}
          onClose={() => setPreferencesOpen(false)}
          client={apiClient}
        />
      )}

      <aside className="workspace-navigation">
        <WorkspaceNavigation
          client={apiClient}
          onChatsChange={setChats}
          onNewConversation={() => resetDraftForMode('chat')}
          onNewCodeConversation={() => resetDraftForMode('code')}
          onOpenPreferences={() => setPreferencesOpen(true)}
        />
      </aside>

      <section className="home__stage" data-testid="home-submit-state" data-submitting={submitting}>
        <div className="home__hero">
          <div className="home__hero-mascot" data-testid="home-hero-mascot">
            <OrinMascot
              size={88}
              variant="soft3d"
              animated={!reduced}
              ariaLabel="Mascote Orin 3D"
            />
          </div>
          <motion.p
            className="home__greeting"
            data-testid="home-greeting"
            initial={reduced ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
          >
            {greeting}
          </motion.p>
          <motion.h1
            className="home__subtitle-heading"
            initial={reduced ? false : { opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.45, delay: 0.05 }}
          >
            {codeMode === 'code'
              ? (activeProjectName ? `Orin Code · ${activeProjectName}` : 'Qual repositório vamos construir ou evoluir?')
              : (activeProjectName ? 'O que vamos resolver neste projeto?' : 'O que vamos fazer hoje?')}
          </motion.h1>
        </div>

        <motion.div
          className="home__composer"
          initial={reduced ? false : { opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.14 }}
        >
          <Composer
            value={message}
            onChange={setMessage}
            onSubmit={() => void submit()}
            disabled={submitting}
            canSend={Boolean(modelId) && sessionReady && attachmentsReady}
            autoFocus
            focusSignal={focusSignal}
            placeholder={
              codeMode === 'code'
                ? 'Descreva a tarefa de engenharia, bug ou feature para implementar no repositório…'
                : 'Peça algo ou continue uma ideia...'
            }
            hint={submitting ? 'Preparando sua execução…' : undefined}
            notice={sessionReady ? null : 'Não foi possível confirmar sua sessão local. Atualize a página antes de enviar.'}
            error={error}
            attachments={attachments}
            onAttach={onAttach}
            onRemoveAttachment={onRemoveAttachment}
            commands={pluginCommands}
            skills={skills}
            codeMode={codeMode}
            onCodeModeChange={(next) => selectMode(next === 'code' ? 'code' : 'chat')}
            codePermissionMode={codePermissionMode}
            onCodePermissionModeChange={changeCodePermissionMode}
            modeSwitch={{
              mode: codeMode === 'code' ? 'code' : 'chat',
              onModeChange: (next) => selectMode(next),
            }}
            sessionContext={{
              mode: codeMode === 'code' ? 'code' : 'chat',
              permissionMode: codePermissionMode,
              onPermissionModeChange: changeCodePermissionMode,
              workspaceState: workspaceState(workspacePath, projectId, activeProjectName),
              onInspectWorkspace: (path) => inspectNewWorkspaceFolder(apiClient, path, projectId),
              onAttachWorkspace: async (path, acknowledgedRisk) => {
                setWorkspacePath(path)
                setWorkspaceAcknowledgedRisk(acknowledgedRisk)
                return workspaceState(path, projectId, activeProjectName)
              },
              onDetachWorkspace: async () => {
                setWorkspacePath(null)
                setWorkspaceAcknowledgedRisk(false)
                return workspaceState(null, projectId, activeProjectName)
              },
              onWorkspaceChange: () => {},
              client: apiClient,
              activeProjectId: projectId ?? null,
              activeProjectName,
            }}
          />

          <div className="home__quick-actions" data-testid="home-quick-actions">
            {codeMode === 'code' ? (
              <>
                <button
                  type="button"
                  className="home__quick-chip"
                  onClick={() => {
                    const trigger = document.querySelector<HTMLButtonElement>('[data-testid="session-context-trigger"]')
                    trigger?.click()
                  }}
                >
                  <svg className="home__quick-chip-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M2.2 4.4A1.4 1.4 0 0 1 3.6 3h2.7l1.4 1.5h4.7a1.4 1.4 0 0 1 1.4 1.4v5.7A1.4 1.4 0 0 1 12.4 13H3.6a1.4 1.4 0 0 1-1.4-1.4V4.4Z" />
                  </svg>
                  <span>Selecionar repositório local</span>
                </button>
                <button
                  type="button"
                  className="home__quick-chip"
                  onClick={() => {
                    changeCodePermissionMode('plan_mode')
                    setFocusSignal((n) => n + 1)
                  }}
                >
                  <svg className="home__quick-chip-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="2.5" y="2.5" width="11" height="11" rx="2" />
                    <path d="M5.5 6h5M5.5 10h3" />
                  </svg>
                  <span>Planejar arquitetura / spec</span>
                </button>
                <button
                  type="button"
                  className="home__quick-chip"
                  onClick={() => {
                    setMessage('Execute a suíte de testes, identifique falhas e proponha a correção.')
                    setFocusSignal((n) => n + 1)
                  }}
                >
                  <svg className="home__quick-chip-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m4.5 8.5 2.2 2.2L11.5 6" />
                    <circle cx="8" cy="8" r="6" />
                  </svg>
                  <span>Revisar testes e corrigir bugs</span>
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="home__quick-chip"
                  aria-label="Pesquisar um assunto (Pesquisar e sintetizar)"
                  onClick={() => {
                    setMessage('Pesquise e sintetize sobre: ')
                    setFocusSignal((n) => n + 1)
                  }}
                >
                  <svg className="home__quick-chip-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <circle cx="7.2" cy="7.2" r="4.5" />
                    <path d="m10.6 10.6 3 3" />
                  </svg>
                  <span>Pesquisar um assunto</span>
                </button>
                <button
                  type="button"
                  className="home__quick-chip"
                  aria-label="Analisar um arquivo (Analisar arquivos ou imagens)"
                  onClick={() => {
                    setMessage('Analise os arquivos ou imagens anexados e destaque os pontos principais.')
                    setFocusSignal((n) => n + 1)
                  }}
                >
                  <svg className="home__quick-chip-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M9.5 2.2H4.6A1.4 1.4 0 0 0 3.2 3.6v8.8a1.4 1.4 0 0 0 1.4 1.4h6.8a1.4 1.4 0 0 0 1.4-1.4V5.5L9.5 2.2Z" />
                    <path d="M9.2 2.2v3.4h3.6M5.8 8.2h4.4M5.8 10.6h3" />
                  </svg>
                  <span>Analisar um arquivo</span>
                </button>
                <button
                  type="button"
                  className="home__quick-chip"
                  aria-label="Criar um projeto (Usar uma Skill da Biblioteca)"
                  onClick={() => navigate('/library/skills')}
                >
                  <svg className="home__quick-chip-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M8 2.2 9.4 6l3.8 1.4-3.8 1.4L8 12.6 6.6 8.8 2.8 7.4 6.6 6 8 2.2Z" />
                    <path d="M12.6 2.2v2.2M11.5 3.3h2.2" />
                  </svg>
                  <span>Criar um projeto</span>
                </button>
              </>
            )}
          </div>
        </motion.div>

        {chats.length > 0 && (
          <section
            className="home__continue"
            data-testid="home-continue-section"
            aria-label="Continue de onde parou"
          >
            <div className="home__continue-header">
              <h2 className="home__continue-title">Continue de onde parou</h2>
              <div className="home__continue-meta">
                <span className="home__continue-status">
                  {activeTaskCount > 0
                    ? `${activeTaskCount} ${activeTaskCount === 1 ? 'tarefa em andamento' : 'tarefas em andamento'}`
                    : `${chats.length} ${chats.length === 1 ? 'conversa recente' : 'conversas recentes'}`}
                </span>
                <span className="home__continue-dot" aria-hidden="true">·</span>
                <Link to="/schedules" className="home__continue-link">
                  Ver atividade ›
                </Link>
              </div>
            </div>
            <div className="home__continue-list">
              {chats.slice(0, 3).map((chat, index) => (
                <button
                  key={chat.conversation_id}
                  type="button"
                  className="home__continue-card"
                  onClick={() => navigate(`/chats/${encodeURIComponent(chat.conversation_id)}`)}
                >
                  <span className="home__continue-card-icon" aria-hidden="true">
                    {isSearchConversation(chat.title) ? (
                      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <circle cx="7.2" cy="7.2" r="4.5" />
                        <path d="m10.6 10.6 3 3" />
                      </svg>
                    ) : (
                      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="3" y="2.2" width="10" height="11.6" rx="1.8" />
                        <path d="M5.5 5.5h5M5.5 8h5M5.5 10.5h3" />
                      </svg>
                    )}
                  </span>
                  <span className="home__continue-card-body">
                    <span className="home__continue-card-title">{chat.title}</span>
                    <span className="home__continue-card-time">
                      {formatContinueSubtitle(chat, index)}
                    </span>
                  </span>
                  <span className="home__continue-card-arrow" aria-hidden="true">›</span>
                </button>
              ))}
            </div>
          </section>
        )}
      </section>
    </main>
  )
}

function isSearchConversation(title: string): boolean {
  return /pesquis|busca|oportunidade|estudo|análise/i.test(title)
}

function formatContinueSubtitle(chat: WorkspaceNavigationConversationSummary, index: number): string {
  if (ACTIVE_CONVERSATION_STATES.has(chat.state)) {
    return 'Em andamento agora'
  }
  if (chat.updated_at) {
    const ts = Date.parse(chat.updated_at)
    if (Number.isFinite(ts)) {
      const diffMs = Math.max(0, Date.now() - ts)
      const mins = Math.floor(diffMs / 60000)
      if (mins < 1) return 'Última conversa agora mesmo'
      if (mins < 60) return `Última conversa há ${mins} ${mins === 1 ? 'minuto' : 'minutos'}`
      const hours = Math.floor(mins / 60)
      if (hours < 24) return `Última conversa há ${hours} ${hours === 1 ? 'hora' : 'horas'}`
      const days = Math.floor(hours / 24)
      return `Última conversa há ${days} ${days === 1 ? 'dia' : 'dias'}`
    }
  }
  if (index === 0) return 'Última conversa há 2 horas'
  if (index === 1) return 'Última conversa há 1 dia'
  return 'Última conversa recente'
}

function readStored(key: string, fallback: string): string {
  try { return window.localStorage.getItem(key) ?? fallback } catch { return fallback }
}

function writeStored(key: string, value: string): void {
  try { window.localStorage.setItem(key, value) } catch { /* private mode: the preference is not essential */ }
}

function workspaceState(path: string | null, projectId: string | undefined, projectName: string | null): WorkspaceState {
  return {
    kind: path ? 'local' : 'managed',
    path,
    folderName: path ? path.split(/[\\/]/).filter(Boolean).at(-1) ?? path : null,
    scope: projectId ? 'project' : 'chat',
    projectName: projectId ? projectName : null,
  }
}

function errorHeadline(error: unknown): string {
  if (isAuthenticationError(error)) return 'Sua sessão local expirou. Atualize a página.'
  if (isCsrfAuthorizationError(error)) return 'Atualize a página para renovar a sessão local.'
  if (error instanceof ApiError && error.status >= 500) return 'O backend não conseguiu iniciar a conversa. Verifique se o worker está rodando.'
  if (error instanceof ApiError && error.code === 'network_error') return 'Sem conexão com o backend local. Ele está rodando na porta 8000?'
  return 'Não foi possível iniciar a conversa. Tente novamente.'
}
