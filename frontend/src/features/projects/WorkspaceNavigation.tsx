import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { listConversations, type Conversation } from '../../api/conversations'
import { createProject, createProjectConversation, listProjectSidebar, type ProjectSidebarItem } from '../../api/projects'
import { PROVIDER_NAMES, listProviderModels, type ProviderModel, type ProviderName } from '../../api/providers'
import { ModelPicker } from '../../components/ModelPicker'
import { ProjectNavigation } from './ProjectNavigation'

type ConversationSummary = Pick<Conversation, 'conversation_id' | 'title' | 'state'>

type Props = { client?: ApiClient; onChatsChange?: (chats: ConversationSummary[]) => void }

export function WorkspaceNavigation({ client, onChatsChange }: Props) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const navigate = useNavigate()
  const [chats, setChats] = useState<ConversationSummary[]>([])
  const [projects, setProjects] = useState<ProjectSidebarItem[]>([])
  const [creatingProject, setCreatingProject] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [projectDescription, setProjectDescription] = useState('')
  const [projectId, setProjectId] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [provider, setProvider] = useState<ProviderName>('openrouter')
  const [models, setModels] = useState<ProviderModel[]>([])
  const [modelId, setModelId] = useState('')
  const [loadingModels, setLoadingModels] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    const [conversationList, sidebar] = await Promise.all([listConversations(apiClient), listProjectSidebar(apiClient)])
    setChats(conversationList.items)
    onChatsChange?.(conversationList.items)
    setProjects(sidebar.items)
  }, [apiClient, onChatsChange])

  useEffect(() => { void reload().catch(() => setError('Não foi possível atualizar a navegação.')) }, [reload])

  useEffect(() => {
    if (!projectId) return
    const controller = new AbortController()
    setLoadingModels(true)
    setModels([])
    setModelId('')
    void listProviderModels(apiClient, provider, controller.signal).then((items) => {
      if (controller.signal.aborted) return
      setModels(items)
      setModelId(items.find((item) => item.is_favorite)?.model_id ?? items[0]?.model_id ?? '')
    }).catch(() => setError('Não foi possível carregar os modelos deste provider.')).finally(() => { if (!controller.signal.aborted) setLoadingModels(false) })
    return () => controller.abort()
  }, [apiClient, projectId, provider])

  async function submitProject() {
    if (!projectName.trim()) return
    try {
      await createProject(apiClient, { name: projectName, description: projectDescription })
      setProjectName(''); setProjectDescription(''); setCreatingProject(false); setError(null)
      await reload()
    } catch { setError('Não foi possível criar o projeto.') }
  }

  async function submitProjectChat() {
    if (!projectId || !message.trim() || !modelId) return
    try {
      const receipt = await createProjectConversation(apiClient, projectId, { message: message.trim(), provider, model_id: modelId })
      setProjectId(null); setMessage(''); setError(null)
      await reload()
      navigate(`/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(receipt.conversation_id)}`)
    } catch { setError('Não foi possível criar o chat deste projeto.') }
  }

  return <>
    <ProjectNavigation standalone={chats} projects={projects} onCreateProject={() => setCreatingProject(true)} onNewChat={setProjectId} />
    {creatingProject && <div className="project-dialog" role="dialog" aria-modal="true" aria-label="Criar projeto"><form onSubmit={(event) => { event.preventDefault(); void submitProject() }}><h2>Criar projeto</h2><label>Nome<input autoFocus value={projectName} onChange={(event) => setProjectName(event.target.value)} maxLength={120} /></label><label>Descrição<input value={projectDescription} onChange={(event) => setProjectDescription(event.target.value)} maxLength={2000} /></label><div><button type="button" onClick={() => setCreatingProject(false)}>Cancelar</button><button type="submit">Criar</button></div></form></div>}
    {projectId && <div className="project-dialog" role="dialog" aria-modal="true" aria-label="Novo chat no projeto"><form onSubmit={(event) => { event.preventDefault(); void submitProjectChat() }}><h2>Novo chat no projeto</h2><label>Mensagem inicial<textarea autoFocus value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Descreva o que este chat precisa resolver" /></label><ModelPicker providers={[...PROVIDER_NAMES]} provider={provider} onProviderChange={setProvider} models={models} modelId={modelId} onModelChange={setModelId} loading={loadingModels} disabled={loadingModels} /><div><button type="button" onClick={() => { setProjectId(null); setMessage('') }}>Cancelar</button><button type="submit" disabled={!message.trim() || !modelId || loadingModels}>Criar chat</button></div></form></div>}
    {error && <p className="workspace-navigation__error" role="status">{error}</p>}
  </>
}
