import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { createBrowserApiClient } from '../../api/client'
import { listProjectMemories, listProjectSidebar, type ProjectSidebarItem } from '../../api/projects'

export function ProjectPage() {
  const { projectId = '' } = useParams()
  const client = useMemo(() => createBrowserApiClient(), [])
  const [project, setProject] = useState<ProjectSidebarItem | null>(null)
  const [memories, setMemories] = useState<Array<{ memory_id: string; fact: string }>>([])
  const [error, setError] = useState(false)
  const load = useCallback(async () => { try { const [sidebar, memory] = await Promise.all([listProjectSidebar(client), listProjectMemories(client, projectId)]); setProject(sidebar.items.find((item) => item.project_id === projectId) ?? null); setMemories(memory.items); } catch { setError(true) } }, [client, projectId])
  useEffect(() => { queueMicrotask(() => { void load() }) }, [load])
  if (error || !project) return <main className="project-page project-page--missing"><Link className="project-page__back" to="/">← Orin</Link><p>Projeto não encontrado.</p></main>
  return <main className="project-page">
    <header className="project-page__bar"><Link className="project-page__back" to="/">← Orin</Link><Link className="project-page__memory-link" to={`/projects/${encodeURIComponent(projectId)}/memory`}>Memória do projeto</Link></header>
    <section className="project-page__hero"><p className="eyebrow">PROJETO</p><h1>{project.name}</h1><p>{project.description || 'Um espaço compartilhado para conversas, arquivos e memória.'}</p></section>
    <div className="project-page__sections">
      <section className="project-page__section" aria-labelledby="project-chats-title"><div className="project-page__section-heading"><div><p className="eyebrow">CONVERSAS</p><h2 id="project-chats-title">Chats</h2></div><span>{project.chats.length}</span></div>{project.chats.length ? <div className="project-page__chat-list">{project.chats.map((chat) => <Link key={chat.conversation_id} to={`/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chat.conversation_id)}`}><span>{chat.title}</span><small>{chat.state}</small></Link>)}</div> : <p className="project-page__empty">As conversas deste projeto aparecerão aqui.</p>}</section>
      <section className="project-page__section" aria-labelledby="project-memory-title"><div className="project-page__section-heading"><div><p className="eyebrow">CONTEXTO COMPARTILHADO</p><h2 id="project-memory-title">Memória</h2></div><Link to={`/projects/${encodeURIComponent(projectId)}/memory`}>Gerenciar</Link></div><p className="project-page__empty">{memories.length ? `${memories.length} memórias disponíveis para este projeto.` : 'Nenhuma memória de projeto ainda.'}</p></section>
    </div>
  </main>
}
