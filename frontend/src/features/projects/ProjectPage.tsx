import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { createBrowserApiClient } from '../../api/client'
import {
  attachProjectWorkspace,
  detachProjectWorkspace,
  listProjectMemories,
  listProjectSidebar,
  type ProjectSidebarItem,
} from '../../api/projects'

export function ProjectPage() {
  const { projectId = '' } = useParams()
  const client = useMemo(() => createBrowserApiClient(), [])
  const [project, setProject] = useState<ProjectSidebarItem | null>(null)
  const [memories, setMemories] = useState<Array<{ memory_id: string; fact: string }>>([])
  const [error, setError] = useState(false)
  const [wsPath, setWsPath] = useState('')
  const [wsBusy, setWsBusy] = useState(false)
  const [wsError, setWsError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [sidebar, memory] = await Promise.all([listProjectSidebar(client), listProjectMemories(client, projectId)])
      const found = sidebar.items.find((item) => item.project_id === projectId) ?? null
      setProject(found)
      setWsPath(found?.workspace?.path ?? '')
      setMemories(memory.items)
    } catch {
      setError(true)
    }
  }, [client, projectId])

  useEffect(() => {
    queueMicrotask(() => {
      void load()
    })
  }, [load])

  const handleSaveWorkspace = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!wsPath.trim() || wsBusy) return
    setWsBusy(true)
    setWsError(null)
    try {
      const nextWs = await attachProjectWorkspace(client, projectId, wsPath.trim(), true)
      setProject((cur) => (cur ? { ...cur, workspace: nextWs } : cur))
      setWsPath(nextWs.path ?? '')
    } catch {
      setWsError('Não foi possível vincular a pasta ao projeto.')
    } finally {
      setWsBusy(false)
    }
  }

  const handleDetachWorkspace = async () => {
    if (wsBusy) return
    setWsBusy(true)
    setWsError(null)
    try {
      const nextWs = await detachProjectWorkspace(client, projectId)
      setProject((cur) => (cur ? { ...cur, workspace: nextWs } : cur))
      setWsPath('')
    } catch {
      setWsError('Não foi possível desvincular a pasta.')
    } finally {
      setWsBusy(false)
    }
  }

  if (error || !project) {
    return (
      <main className="project-page project-page--missing">
        <Link className="project-page__back" to="/">← Orin</Link>
        <p>Projeto não encontrado.</p>
      </main>
    )
  }

  return (
    <main className="project-page">
      <header className="project-page__bar">
        <Link className="project-page__back" to="/">← Orin</Link>
        <Link className="project-page__memory-link" to={`/projects/${encodeURIComponent(projectId)}/memory`}>Memória do projeto</Link>
      </header>
      <section className="project-page__hero">
        <p className="eyebrow">PROJETO</p>
        <h1>{project.name}</h1>
        <p>{project.description || 'Um espaço compartilhado para conversas, arquivos e memória.'}</p>
        <div className="project-page__quick-actions" style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
          <Link
            className="primary-button"
            to={`/projects/${encodeURIComponent(projectId)}/new?mode=chat`}
            data-testid="project-new-chat-button"
          >
            + Novo Chat
          </Link>
          <Link
            className="ghost-button"
            to={`/projects/${encodeURIComponent(projectId)}/new?mode=code`}
            data-testid="project-new-code-button"
          >
            &gt;_ Novo Code
          </Link>
        </div>
      </section>
      <div className="project-page__sections">
        <section className="project-page__section" aria-labelledby="project-workspace-title" data-testid="project-workspace-section">
          <div className="project-page__section-heading">
            <div>
              <p className="eyebrow">PASTA LOCAL</p>
              <h2 id="project-workspace-title">Workspace do projeto</h2>
            </div>
            <span>{project.workspace?.kind === 'local' ? (project.workspace.path || project.workspace.folderName) : 'Workspace gerenciado'}</span>
          </div>
          <form onSubmit={(e) => { void handleSaveWorkspace(e) }} style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '8px' }}>
            <input
              type="text"
              value={wsPath}
              onChange={(e) => setWsPath(e.target.value)}
              placeholder="/caminho/para/repositorio"
              aria-label="Pasta local do projeto"
              data-testid="project-workspace-input"
              style={{ flex: 1 }}
            />
            <button type="submit" className="ghost-button" disabled={wsBusy || !wsPath.trim()} data-testid="project-workspace-save-button">
              Vincular pasta
            </button>
            {project.workspace?.kind === 'local' && (
              <button
                type="button"
                className="ghost-button"
                disabled={wsBusy}
                onClick={() => { void handleDetachWorkspace() }}
                data-testid="project-workspace-detach-button"
              >
                Desvincular
              </button>
            )}
          </form>
          {wsError && <p role="alert">{wsError}</p>}
        </section>

        <section className="project-page__section" aria-labelledby="project-chats-title">
          <div className="project-page__section-heading">
            <div>
              <p className="eyebrow">CONVERSAS</p>
              <h2 id="project-chats-title">Chats &amp; Sessões Code</h2>
            </div>
            <span>{project.chats.length}</span>
          </div>
          {project.chats.length ? (
            <div className="project-page__chat-list">
              {project.chats.map((chat) => (
                <Link key={chat.conversation_id} to={`/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chat.conversation_id)}`}>
                  <span>
                    {chat.mode === 'code' ? (
                      <strong className="workspace-nav__code-pill" data-testid="project-chat-code-pill">&gt;_ Code </strong>
                    ) : (
                      <small style={{ marginRight: '6px', opacity: 0.7 }}>Chat </small>
                    )}
                    {chat.title}
                  </span>
                  <small>{chat.state}</small>
                </Link>
              ))}
            </div>
          ) : (
            <p className="project-page__empty">As conversas deste projeto aparecerão aqui.</p>
          )}
        </section>

        <section className="project-page__section" aria-labelledby="project-memory-title">
          <div className="project-page__section-heading">
            <div>
              <p className="eyebrow">CONTEXTO COMPARTILHADO</p>
              <h2 id="project-memory-title">Memória</h2>
            </div>
            <Link to={`/projects/${encodeURIComponent(projectId)}/memory`}>Gerenciar</Link>
          </div>
          <p className="project-page__empty">
            {memories.length ? `${memories.length} memórias disponíveis para este projeto.` : 'Nenhuma memória de projeto ainda.'}
          </p>
        </section>
      </div>
    </main>
  )
}

