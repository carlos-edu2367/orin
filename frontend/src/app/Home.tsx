import { motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
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
import { CommandPalette } from '../components/CommandPalette'
import { ModelPicker } from '../components/ModelPicker'
import { Composer } from '../features/conversations/Composer'
import { useComposerAttachments } from '../features/conversations/useComposerAttachments'
import { inspectNewWorkspaceFolder, type WorkspaceState } from '../api/workspace'
import { WorkspaceNavigation } from '../features/projects/WorkspaceNavigation'
import { PreferencesModal } from '../features/settings/PreferencesModal'
import { useTheme } from './ThemeContext'

// Brand identity (<Brand />) is hosted inside WorkspaceNavigation header.

type HomeProps = {
  client?: ApiClient
  bootstrap?: BrowserSessionBootstrap
}

const PROVIDER_STORAGE_KEY = 'agentos.provider'
const MODEL_STORAGE_KEY = 'agentos.model'

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
  const [preferencesOpen, setPreferencesOpen] = useState(false)
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
          <CommandPalette conversations={chats} />
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
            ? (activeProjectName ? `Orin Code · ${activeProjectName}` : 'Qual repositório vamos construir ou evoluir?')
            : (activeProjectName ? 'O que vamos resolver neste projeto?' : 'Por onde começamos?')}
        </motion.h1>

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
                  Selecionar repositório local
                </button>
                <button
                  type="button"
                  className="home__quick-chip"
                  onClick={() => {
                    changeCodePermissionMode('plan_mode')
                    setFocusSignal((n) => n + 1)
                  }}
                >
                  Planejar arquitetura / spec
                </button>
                <button
                  type="button"
                  className="home__quick-chip"
                  onClick={() => {
                    setMessage('Execute a suíte de testes, identifique falhas e proponha a correção.')
                    setFocusSignal((n) => n + 1)
                  }}
                >
                  Revisar testes e corrigir bugs
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="home__quick-chip"
                  onClick={() => {
                    setMessage('Pesquise e sintetize sobre: ')
                    setFocusSignal((n) => n + 1)
                  }}
                >
                  Pesquisar e sintetizar
                </button>
                <button
                  type="button"
                  className="home__quick-chip"
                  onClick={() => {
                    setMessage('Analise os arquivos ou imagens anexados e destaque os pontos principais.')
                    setFocusSignal((n) => n + 1)
                  }}
                >
                  Analisar arquivos ou imagens
                </button>
                <button
                  type="button"
                  className="home__quick-chip"
                  onClick={() => navigate('/library/skills')}
                >
                  Usar uma Skill da Biblioteca
                </button>
              </>
            )}
          </div>
        </motion.div>

        {chats.length > 0 && (
          <div className="visually-hidden" aria-label="Atalhos de conversas recentes">
            {chats.slice(0, 4).map((chat) => (
              <button
                key={chat.conversation_id}
                type="button"
                tabIndex={-1}
                onClick={() => navigate(`/chats/${encodeURIComponent(chat.conversation_id)}`)}
              >
                {chat.title}
              </button>
            ))}
          </div>
        )}
      </section>
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
