import { motion, useReducedMotion } from 'motion/react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { readBrowserSessionBootstrap, type BrowserSessionBootstrap } from '../api/browserSession'
import { ApiClient, createBrowserApiClient } from '../api/client'
import { createConversation } from '../api/conversations'
import { listProjectSidebar } from '../api/projects'
import { ApiError, isAuthenticationError, isCsrfAuthorizationError } from '../api/errors'
import { PROVIDER_NAMES, listProviderModels, type ProviderModel, type ProviderName } from '../api/providers'
import { AmbientField } from '../components/three/AmbientField'
import { Brand } from '../components/Brand'
import { CommandPalette } from '../components/CommandPalette'
import { ModelPicker } from '../components/ModelPicker'
import { Composer } from '../features/conversations/Composer'
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
  const navigate = useNavigate()
  const reduced = useReducedMotion()
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const session = bootstrap ?? (typeof document === 'undefined' ? { status: 'missing_csrf' as const } : readBrowserSessionBootstrap(document))

  const [message, setMessage] = useState('')
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
  const [chats, setChats] = useState<Array<{ conversation_id: string; title: string; state: string }>>([])
  const activeProjectName = projectId ? projectName : null

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
    try {
      const receipt = await createConversation(apiClient, {
        message: text,
        provider,
        model_id: modelId,
        project_id: projectId,
        workspace_path: workspacePath,
        workspace_acknowledged_risk: workspaceAcknowledgedRisk,
        attachments: readyUploads,
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
          onNewConversation={() => {
            if (projectId) {
              navigate('/')
              return
            }
            setMessage('')
            resetAttachments()
            setWorkspacePath(null)
            setWorkspaceAcknowledgedRisk(false)
            setError(null)
            setFocusSignal((value) => value + 1)
          }}
        />
      </aside>

      <section className="home__stage" data-testid="home-submit-state" data-submitting={submitting}>
        <motion.p
          className="eyebrow"
          initial={reduced ? false : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          {activeProjectName ? `projeto · ${activeProjectName}` : `ambiente local · ${chats.length} ${chats.length === 1 ? 'conversa' : 'conversas'}`}
        </motion.p>
        <motion.h1
          initial={reduced ? false : { opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.06 }}
        >
          {activeProjectName ? 'O que vamos resolver neste projeto?' : 'O que vamos resolver?'}
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
            hint={submitting ? 'Preparando sua execução…' : undefined}
            notice={sessionReady ? null : 'Não foi possível confirmar sua sessão local. Atualize a página antes de enviar.'}
            error={error}
            attachments={attachments}
            onAttach={onAttach}
            onRemoveAttachment={onRemoveAttachment}
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
