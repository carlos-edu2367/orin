import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { type ApiClient, createBrowserApiClient } from '../../api/client'
import { ensureFriendConversation } from '../../api/conversations'
import { createProject, listProjectSidebar, type ProjectSidebarItem } from '../../api/projects'
import { OrinMascot } from '../../components/OrinMascot'
import { WorkspaceNavigation } from './WorkspaceNavigation'

export type ProjectsGalleryPageProps = {
  client?: ApiClient
}

const FOLDER_PALETTE = ['purple', 'blue', 'emerald', 'amber'] as const
export type FolderPaletteColor = (typeof FOLDER_PALETTE)[number]

const ACTIVE_CHAT_STATES = new Set(['running', 'working', 'streaming', 'in_progress'])

export function getProjectFolderColor(projectId: string, index: number): FolderPaletteColor {
  if (index >= 0 && index < FOLDER_PALETTE.length) {
    return FOLDER_PALETTE[index]
  }
  let hash = 0
  for (let i = 0; i < projectId.length; i += 1) {
    hash = (hash * 31 + projectId.charCodeAt(i)) >>> 0
  }
  return FOLDER_PALETTE[hash % FOLDER_PALETTE.length]
}

export function formatProjectUpdatedLabel(project: ProjectSidebarItem): string {
  let latestMs = 0
  for (const chat of project.chats) {
    if (chat.updated_at) {
      const parsed = Date.parse(chat.updated_at)
      if (Number.isFinite(parsed) && parsed > latestMs) {
        latestMs = parsed
      }
    }
  }
  if (latestMs > 0) {
    const diffMs = Math.max(0, Date.now() - latestMs)
    const mins = Math.floor(diffMs / 60000)
    if (mins < 1) return 'Atualizado agora mesmo'
    if (mins < 60) return `Atualizado há ${mins} min`
    const hours = Math.floor(mins / 60)
    if (hours < 24) return `Atualizado há ${hours} ${hours === 1 ? 'hora' : 'horas'}`
    const days = Math.floor(hours / 24)
    return `Atualizado há ${days} ${days === 1 ? 'dia' : 'dias'}`
  }
  return 'Sem atividade recente'
}

export function ProjectsGalleryPage({ client }: ProjectsGalleryPageProps) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const navigate = useNavigate()
  const [projects, setProjects] = useState<ProjectSidebarItem[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [newWorkspacePath, setNewWorkspacePath] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadProjects = useCallback(async () => {
    try {
      const sidebar = await listProjectSidebar(apiClient)
      setProjects(Array.isArray(sidebar?.items) ? sidebar.items : [])
    } catch {
      setError('Não foi possível carregar os projetos.')
    }
  }, [apiClient])

  useEffect(() => {
    queueMicrotask(() => {
      void loadProjects()
    })
    const onUpdated = () => {
      void loadProjects()
    }
    window.addEventListener('orin:projects-updated', onUpdated)
    return () => {
      window.removeEventListener('orin:projects-updated', onUpdated)
    }
  }, [loadProjects])

  const filteredProjects = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()
    if (!query) return projects
    return projects.filter((project) => {
      const nameMatch = project.name.toLowerCase().includes(query)
      const descMatch = (project.description ?? 'Sem descrição').toLowerCase().includes(query)
      return nameMatch || descMatch
    })
  }, [projects, searchQuery])

  const handleCreateProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmedName = newName.trim()
    if (!trimmedName || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const trimmedDesc = newDescription.trim()
      const trimmedWorkspace = newWorkspacePath.trim()
      await createProject(apiClient, {
        name: trimmedName,
        description: trimmedDesc || null,
        ...(trimmedWorkspace
          ? { workspace_path: trimmedWorkspace, workspace_acknowledged_risk: true }
          : {}),
      })
      setNewName('')
      setNewDescription('')
      setNewWorkspacePath('')
      setShowCreateForm(false)
      await loadProjects()
      window.dispatchEvent(new CustomEvent('orin:projects-updated'))
    } catch {
      setError('Não foi possível criar o projeto.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="projects-gallery-layout workspace-shell">
      <aside className="workspace-navigation">
        <WorkspaceNavigation client={apiClient} initialView="projects" hideProjectChatSublist={true} />
      </aside>

      <main className="projects-gallery">
        <header className="projects-gallery__topbar canvas-header" data-testid="canvas-header">
          <div className="canvas-header__left" />
          <div className="canvas-header__center" />
          <div className="canvas-header__right">
            <button
              type="button"
              className="canvas-header__voice-btn"
              data-testid="topbar-voice-button"
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
            <OrinMascot size={30} variant="flat" statusDot="online" ariaHidden />
          </div>
        </header>

        <div className="projects-gallery__content">
          <section className="projects-gallery__hero">
            <div className="projects-gallery__hero-titles">
              <h1>Projetos</h1>
              <p className="projects-gallery__subtitle">Um espaço para cada trabalho.</p>
            </div>

            <div className="projects-gallery__hero-actions">
              <div className="projects-gallery__search-box">
                <svg
                  className="projects-gallery__search-icon"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  aria-hidden="true"
                >
                  <circle cx="7" cy="7" r="4.5" />
                  <path d="m10.5 10.5 3.2 3.2" strokeLinecap="round" />
                </svg>
                <input
                  type="search"
                  className="projects-gallery__search-input"
                  placeholder="Buscar projetos..."
                  aria-label="Buscar projetos"
                  data-testid="projects-search-input"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                />
              </div>

              <button
                type="button"
                className="projects-gallery__new-btn primary-button"
                data-testid="projects-new-button"
                onClick={() => setShowCreateForm((current) => !current)}
              >
                <span aria-hidden="true">+</span>
                <span>Novo projeto</span>
              </button>
            </div>
          </section>

          {showCreateForm && (
            <form
              className="projects-gallery__create-form"
              data-testid="projects-create-form"
              onSubmit={(e) => {
                void handleCreateProject(e)
              }}
            >
              <div className="projects-gallery__create-fields">
                <label className="projects-gallery__field">
                  <span>Nome</span>
                  <input
                    type="text"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="Ex.: Meu projeto"
                    maxLength={120}
                    required
                  />
                </label>
                <label className="projects-gallery__field">
                  <span>Descrição</span>
                  <input
                    type="text"
                    value={newDescription}
                    onChange={(e) => setNewDescription(e.target.value)}
                    placeholder="Ex.: Sem descrição"
                    maxLength={2000}
                  />
                </label>
                <label className="projects-gallery__field">
                  <span>Pasta local (opcional)</span>
                  <input
                    type="text"
                    value={newWorkspacePath}
                    onChange={(e) => setNewWorkspacePath(e.target.value)}
                    placeholder="C:/Projetos/meu-projeto"
                    maxLength={1024}
                  />
                </label>
              </div>
              <div className="projects-gallery__create-actions">
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => setShowCreateForm(false)}
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="primary-button"
                  disabled={submitting || !newName.trim()}
                >
                  Criar projeto
                </button>
              </div>
            </form>
          )}

          {error && (
            <p className="projects-gallery__error" role="alert">
              {error}
            </p>
          )}

          <div className="projects-gallery__grid" data-testid="projects-catalog-grid">
            {filteredProjects.map((project, index) => {
              const folderColor = getProjectFolderColor(project.project_id, index)
              const activeCount = project.chats.filter((chat) =>
                ACTIVE_CHAT_STATES.has(chat.state),
              ).length
              const chatsCount = project.chats.length
              const workspaceStatusLabel = project.workspace?.path
                ? 'Pasta local vinculada'
                : 'Sem pasta vinculada'
              const updatedLabel = formatProjectUpdatedLabel(project)

              return (
                <article
                  key={project.project_id}
                  className="projects-gallery__card"
                  data-testid={`project-card-${project.project_id}`}
                >
                  <div className="projects-gallery__card-top">
                    <div className="projects-gallery__card-top-left">
                      <span
                        className={`projects-gallery__folder-icon projects-gallery__folder-icon--${folderColor}`}
                        data-folder-color={folderColor}
                        aria-hidden="true"
                      >
                        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
                          <path
                            d="M2.5 5.5A1.75 1.75 0 0 1 4.25 3.75h3.4c.46 0 .9.18 1.23.51l1.1 1.1c.23.23.55.36.88.36h4.89A1.75 1.75 0 0 1 17.5 7.47v6.78A1.75 1.75 0 0 1 15.75 16h-11.5A1.75 1.75 0 0 1 2.5 14.25v-8.75Z"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </span>
                      {activeCount > 0 && (
                        <span
                          className="projects-gallery__active-badge"
                          data-testid="project-card-active-badge"
                        >
                          <span className="projects-gallery__active-dot" aria-hidden="true">●</span>
                          <span>
                            {activeCount} {activeCount === 1 ? 'tarefa em andamento' : 'tarefas em andamento'}
                          </span>
                        </span>
                      )}
                    </div>
                    <button
                      type="button"
                      className="projects-gallery__card-menu"
                      aria-label={`Mais opções para ${project.name}`}
                      onClick={() => navigate(`/projects/${encodeURIComponent(project.project_id)}`)}
                    >
                      •••
                    </button>
                  </div>

                  <div className="projects-gallery__card-body">
                    <h3 className="projects-gallery__card-title">{project.name}</h3>
                    <p className="projects-gallery__card-desc">
                      {project.description || 'Sem descrição'}
                    </p>

                    <div className="projects-gallery__card-counts" data-testid="project-card-counts">
                      <span className="projects-gallery__count-item">
                        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
                          <rect x="3" y="2.2" width="10" height="11.6" rx="1.6" />
                          <path d="M5.5 5.5h5M5.5 8h5M5.5 10.5h3" strokeLinecap="round" />
                        </svg>
                        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
                          <path d="M10.5 4.5 6.2 8.8a1.5 1.5 0 0 0 2.1 2.1l4.6-4.6a3 3 0 0 0-4.2-4.2L4 6.8a4.5 4.5 0 0 0 6.4 6.4l3.6-3.6" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                        <span>
                          {chatsCount} {chatsCount === 1 ? 'conversa' : 'conversas'} · {workspaceStatusLabel}
                        </span>
                      </span>
                    </div>
                  </div>

                  <footer className="projects-gallery__card-footer">
                    <span className="projects-gallery__updated">
                      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
                        <circle cx="8" cy="8" r="5.5" />
                        <path d="M8 5v3.2l2.2 1.3" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                      <span>{updatedLabel}</span>
                    </span>

                    <Link
                      to={`/projects/${encodeURIComponent(project.project_id)}`}
                      className="projects-gallery__open-link"
                      data-testid="project-card-open-link"
                    >
                      Abrir projeto →
                    </Link>
                  </footer>
                </article>
              )
            })}
          </div>
        </div>
      </main>
    </div>
  )
}
