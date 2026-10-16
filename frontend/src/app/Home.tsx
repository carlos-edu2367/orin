import { motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { readBrowserSessionBootstrap, type BrowserSessionBootstrap } from '../api/browserSession'
import { ApiClient, createBrowserApiClient } from '../api/client'
import {
  getCodeModeSettings, normalizeCodePermissionMode, permissionModeToAutonomy, setCodeModeSettings,
  type CodePermissionMode,
} from '../api/codeMode'
import { createConversation, type ConversationMode } from '../api/conversations'
import { listProjectSidebar } from '../api/projects'
import { ApiError, isAuthenticationError, isCsrfAuthorizationError } from '../api/errors'
import { listPluginCommands, type PluginCommand } from '../api/plugins'
import { PROVIDER_NAMES, listProviderModels, type ProviderModel, type ProviderName } from '../api/providers'
import { listSkills, type SkillSummary } from '../api/skills'
import { AmbientField } from '../components/three/AmbientField'
import { Brand } from '../components/Brand'
import { CommandPalette } from '../components/CommandPalette'
import { ModelPicker } from '../components/ModelPicker'
import { Composer, formatPermissionModePill } from '../features/conversations/Composer'
import { useComposerAttachments } from '../features/conversations/useComposerAttachments'
import { WorkspaceFolderButton } from '../features/conversations/WorkspaceFolderButton'
import { inspectNewWorkspaceFolder, type WorkspaceState } from '../api/workspace'
import { WorkspaceNavigation } from '../features/projects/WorkspaceNavigation'

type HomeProps = {
  client?: ApiClient
  bootstrap?: BrowserSessionBootstrap
}

const PROVIDER_STORAGE_KEY = 'agentos.provider'
const MODEL_STORAGE_KEY = 'agentos.model'

/**
 * The opening screen: one input, two quiet chips, and a slow field behind it.
 *
 * Everything else — history, providers, overview — is one keystroke away in the
 * palette rather than permanently on screen, so nothing competes with the act of
 * describing what you need.
 */
export function Home({ client, bootstrap }: HomeProps) {
  const { projectId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const reduced = useReducedMotion()
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const session = bootstrap ?? (typeof document === 'undefined' ? { status: 'missing_csrf' as const } : readBrowserSessionBootstrap(document))

  const requestedModeParam = searchParams.get('mode')
  const [message, setMessage] = useState('')
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
  const [chats, setChats] = useState<Array<{ conversation_id: string; title: string; state: string; mode?: ConversationMode }>>([])
  const [pluginCommands, setPluginCommands] = useState<PluginCommand[]>([])
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const activeProjectName = projectId ? projectName : null

  const [previousRequestedModeParam, setPreviousRequestedModeParam] = useState(requestedModeParam)
  if (requestedModeParam !== previousRequestedModeParam) {
    setPreviousRequestedModeParam(requestedModeParam)
    if (requestedModeParam === 'code') {
      setCodeMode('code')
    } else if (requestedModeParam === 'chat') {
      setCodeMode('chat')
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
    // Reset the catalog view here rather than inside the fetch effect: the
    // change is caused by this interaction, so this is where it belongs.
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
    const resolvedPermissionMode: CodePermissionMode = resolvedMode === 'code' ? (codePermissionMode || serverPermissionMode) : 'ask_permissions'
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
        <div className="home__glow" />
        <AmbientField active={submitting} />
      </div>

      <header className="home__bar">
        <Brand />
        <Link className="ghost-button" to="/schedules">Agendar</Link>
        <CommandPalette conversations={chats} />
      </header>

      <aside className="workspace-navigation">
        <WorkspaceNavigation
          client={apiClient}
          onChatsChange={setChats}
          onNewConversation={() => resetDraftForMode('chat')}
          onNewCodeConversation={() => resetDraftForMode('code')}
        />
      </aside>

      <section className="home__stage" data-testid="home-submit-state" data-submitting={submitting}>
        <div
          className="home__mode-switch mb-5 inline-grid w-full max-w-xl grid-cols-2 gap-1.5 rounded-2xl border border-white/10 bg-black/35 p-1.5 backdrop-blur-md"
          role="group"
          aria-label="Modo da conversa"
          data-testid="home-mode-toggle"
        >
          <button
            type="button"
            data-testid="home-mode-chat"
            aria-pressed={codeMode === 'chat'}
            onClick={() => selectMode('chat')}
            className={`flex flex-col items-start gap-0.5 rounded-xl px-3.5 py-2.5 text-left transition-all ${
              codeMode === 'chat'
                ? 'border border-white/15 bg-white/[0.09] text-white shadow-sm'
                : 'border border-transparent text-white/55 hover:bg-white/[0.04] hover:text-white/80'
            }`}
          >
            <span className="flex items-center gap-2 text-xs font-semibold tracking-tight">
              <svg className="h-3.5 w-3.5 shrink-0" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
                <path d="M2.5 3.5h11a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H6l-3.5 2.5v-2.5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1Z" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span>Chat</span>
            </span>
            <span className="text-[11px] leading-snug text-white/50">
              Conversa geral, pesquisa e criação
            </span>
          </button>

          <button
            type="button"
            data-testid="home-mode-code"
            aria-pressed={codeMode === 'code'}
            onClick={() => selectMode('code')}
            className={`flex flex-col items-start gap-0.5 rounded-xl px-3.5 py-2.5 text-left transition-all ${
              codeMode === 'code'
                ? 'border border-amber-400/35 bg-amber-400/[0.11] text-amber-200 shadow-sm'
                : 'border border-transparent text-white/55 hover:bg-white/[0.04] hover:text-white/80'
            }`}
          >
            <span className="flex items-center gap-2 text-xs font-semibold tracking-tight">
              <span className="inline-flex items-center justify-center rounded bg-amber-400/20 px-1 py-0.5 font-mono text-[10px] font-bold leading-none text-amber-300" aria-hidden="true">
                &gt;_
              </span>
              <span>Orin Code</span>
            </span>
            <span className="text-[11px] leading-snug text-white/50">
              Agente de engenharia no seu repositório local
            </span>
          </button>
        </div>

        <motion.p
          className="eyebrow"
          initial={reduced ? false : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          {codeMode === 'code'
            ? (activeProjectName ? `orin code · ${activeProjectName}` : `orin code · ${workspacePath ?? 'workspace gerenciado'}`)
            : (activeProjectName ? `projeto · ${activeProjectName}` : `ambiente local · ${chats.length} ${chats.length === 1 ? 'conversa' : 'conversas'}`)}
        </motion.p>
        <motion.h1
          initial={reduced ? false : { opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.06 }}
        >
          {codeMode === 'code'
            ? (activeProjectName ? `Orin Code · ${activeProjectName}` : 'Orin Code — Engenharia de software no seu repositório')
            : (activeProjectName ? 'O que vamos resolver neste projeto?' : 'O que vamos resolver?')}
        </motion.h1>

        <motion.div
          className="home__composer"
          initial={reduced ? false : { opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.14 }}
        >
          {codeMode === 'code' && (
            <div
              className="home__code-workspace-bar mb-2.5 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-400/25 bg-amber-400/[0.06] px-3.5 py-2.5 text-left text-xs"
              data-testid="home-code-workspace-bar"
            >
              <div className="flex min-w-0 items-center gap-2.5">
                <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-amber-400/30 bg-amber-400/15 font-mono text-[11px] font-bold text-amber-300" aria-hidden="true">
                  &gt;_
                </span>
                {workspacePath ? (
                  <div className="min-w-0">
                    <span className="block truncate font-mono text-xs font-medium text-amber-200" title={workspacePath}>
                      {workspacePath}
                    </span>
                    <span className="block text-[11px] text-white/55">
                      Repositório local vinculado · {formatPermissionModePill(codePermissionMode)}
                    </span>
                  </div>
                ) : (
                  <div className="min-w-0">
                    <span className="block font-medium text-white/85">
                      {activeProjectName ? `Workspace do projeto ${activeProjectName}` : 'Workspace padrão gerenciado pelo Orin'}
                    </span>
                    <span className="block text-[11px] text-white/55">
                      Vincule uma pasta local ou envie diretamente para usar o workspace gerenciado
                    </span>
                  </div>
                )}
              </div>
              <button
                type="button"
                data-testid="home-select-workspace-folder"
                onClick={() => {
                  const trigger = document.querySelector<HTMLButtonElement>('.workspace-folder__button')
                  trigger?.click()
                }}
                className="shrink-0 rounded-lg border border-amber-400/35 bg-amber-400/15 px-3 py-1.5 font-mono text-[11px] font-medium text-amber-200 transition-colors hover:bg-amber-400/25"
              >
                {workspacePath ? 'Alterar pasta do projeto / repositório' : 'Selecionar pasta do projeto / repositório'}
              </button>
            </div>
          )}

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
                : 'Descreva o que você precisa…'
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
            settings={(
              <>
              <ModelPicker
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
              <WorkspaceFolderButton
                client={apiClient}
                state={workspaceState(workspacePath, projectId, activeProjectName)}
                onInspect={(path) => inspectNewWorkspaceFolder(apiClient, path, projectId)}
                onAttach={async (path, acknowledgedRisk) => {
                  setWorkspacePath(path)
                  setWorkspaceAcknowledgedRisk(acknowledgedRisk)
                  return workspaceState(path, projectId, activeProjectName)
                }}
                onDetach={async () => {
                  setWorkspacePath(null)
                  setWorkspaceAcknowledgedRisk(false)
                  return workspaceState(null, projectId, activeProjectName)
                }}
                onChange={() => {}}
              />
              </>
            )}
          />
        </motion.div>

        {chats.length > 0 && (
          <motion.nav
            className="home__recent"
            aria-label="Conversas recentes"
            initial={reduced ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.5, delay: 0.24 }}
          >
            {chats.slice(0, 4).map((chat) => (
              <button key={chat.conversation_id} type="button" className="home__recent-item" onClick={() => navigate(`/chats/${encodeURIComponent(chat.conversation_id)}`)}>
                <span>{chat.title}</span>
                <small data-state={chat.state}>{chat.state}</small>
              </button>
            ))}
          </motion.nav>
        )}
      </section>

      <footer className="home__foot">
        <span>local-first · nada sai desta máquina sem seu pedido</span>
        <kbd>Ctrl K</kbd>
      </footer>
    </main>
  )
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
