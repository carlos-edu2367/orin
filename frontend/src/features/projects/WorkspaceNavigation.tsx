import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { listConversations, type Conversation } from '../../api/conversations'
import { createProject, listProjectSidebar, type ProjectSidebarItem } from '../../api/projects'
import { ProjectNavigation } from './ProjectNavigation'

type ConversationSummary = Pick<Conversation, 'conversation_id' | 'title' | 'state'>

type Props = { client?: ApiClient; onChatsChange?: (chats: ConversationSummary[]) => void; onNewConversation?: () => void }

export function WorkspaceNavigation({ client, onChatsChange, onNewConversation }: Props) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const navigate = useNavigate()
  const [chats, setChats] = useState<ConversationSummary[]>([])
  const [projects, setProjects] = useState<ProjectSidebarItem[]>([])
  const [creatingProject, setCreatingProject] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [projectDescription, setProjectDescription] = useState('')
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    const [conversationList, sidebar] = await Promise.all([listConversations(apiClient), listProjectSidebar(apiClient)])
    setChats(conversationList.items)
    onChatsChange?.(conversationList.items)
    setProjects(sidebar.items)
  }, [apiClient, onChatsChange])

  useEffect(() => {
    let active = true
    queueMicrotask(() => {
      void reload().catch(() => {
        if (active) setError('Não foi possível atualizar a navegação.')
      })
    })
    return () => { active = false }
  }, [reload])

  async function submitProject() {
    if (!projectName.trim()) return
    try {
      await createProject(apiClient, { name: projectName, description: projectDescription })
      setProjectName(''); setProjectDescription(''); setCreatingProject(false); setError(null)
      await reload()
    } catch { setError('Não foi possível criar o projeto.') }
  }

  return <>
    <ProjectNavigation standalone={chats} projects={projects} onCreateProject={() => setCreatingProject(true)} onNewChat={(projectId) => navigate(`/projects/${encodeURIComponent(projectId)}/new`)} onNewConversation={onNewConversation ?? (() => navigate('/'))} />
    {creatingProject && <div className="project-dialog" role="dialog" aria-modal="true" aria-label="Criar projeto"><form onSubmit={(event) => { event.preventDefault(); void submitProject() }}><h2>Criar projeto</h2><label>Nome<input autoFocus value={projectName} onChange={(event) => setProjectName(event.target.value)} maxLength={120} /></label><label>Descrição<input value={projectDescription} onChange={(event) => setProjectDescription(event.target.value)} maxLength={2000} /></label><div><button type="button" onClick={() => setCreatingProject(false)}>Cancelar</button><button type="submit">Criar</button></div></form></div>}
    {error && <p className="workspace-navigation__error" role="status">{error}</p>}
  </>
}
