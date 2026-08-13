import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import type { ProjectSidebarItem } from '../../api/projects'

type Standalone = { conversation_id: string; title: string; state: string }
type Props = {
  standalone: Standalone[]
  projects: ProjectSidebarItem[]
  onCreateProject?: () => void
  onNewChat?: (projectId: string) => void
  onNewConversation?: () => void
}

export function ProjectNavigation({ standalone, projects, onCreateProject, onNewChat, onNewConversation }: Props) {
  const location = useLocation()
  const [view, setView] = useState<'chats' | 'projects'>(() => (localStorage.getItem('agentos.navigation-view') === 'projects' ? 'projects' : 'chats'))
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(JSON.parse(localStorage.getItem('agentos.collapsed-projects') ?? '[]')))
  const reduced = useReducedMotion()
  function change(next: 'chats' | 'projects') { setView(next); localStorage.setItem('agentos.navigation-view', next) }
  function toggle(id: string) { setCollapsed((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); localStorage.setItem('agentos.collapsed-projects', JSON.stringify([...next])); return next }) }
  const activeProjectId = location.pathname.match(/^\/projects\/([^/]+)/)?.[1]

  return <nav className="project-navigation" aria-label="Conversas e projetos">
    <header className="project-navigation__header">
      <div>
        <p className="project-navigation__eyebrow">Workspace</p>
        <h2>Seu espaço</h2>
      </div>
      <span className="project-navigation__count" aria-label={`${standalone.length + projects.length} itens`}>{standalone.length + projects.length}</span>
    </header>

    <div className="project-navigation__tabs" role="tablist" aria-label="Seções da navegação">
      <button type="button" role="tab" aria-label="Chats" aria-selected={view === 'chats'} aria-controls="navigation-chats" onClick={() => change('chats')}>
        <span>Chats</span><small>{standalone.length}</small>
      </button>
      <button type="button" role="tab" aria-label="Projetos" aria-selected={view === 'projects'} aria-controls="navigation-projects" onClick={() => change('projects')}>
        <span>Projetos</span><small>{projects.length}</small>
      </button>
    </div>

    <div className="project-navigation__scroll">
      <AnimatePresence mode="wait">
        {view === 'chats' ? <motion.div id="navigation-chats" role="tabpanel" aria-label="Chats independentes" key="chats" initial={reduced ? false : { opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={reduced ? undefined : { opacity: 0, y: -4 }}>
          <div className="project-navigation__section-heading">
            <span>Conversas recentes</span>
            {standalone.length > 0 && <small>{standalone.length} {standalone.length === 1 ? 'conversa' : 'conversas'}</small>}
          </div>
          {standalone.length > 0 ? standalone.map((chat) => <NavLink key={chat.conversation_id} to={`/chats/${encodeURIComponent(chat.conversation_id)}`} className={({ isActive }) => isActive ? 'project-navigation__chat is-active' : 'project-navigation__chat'}>
            <span className="project-navigation__chat-mark" aria-hidden="true" />
            <span className="project-navigation__chat-title">{chat.title}</span>
            <span className="project-navigation__chat-state" data-state={chat.state} aria-hidden="true" />
          </NavLink>) : <p className="project-navigation__empty">Suas novas conversas aparecerão aqui.</p>}
        </motion.div> : <motion.div id="navigation-projects" role="tabpanel" aria-label="Projetos" key="projects" initial={reduced ? false : { opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={reduced ? undefined : { opacity: 0, y: -4 }}>
          <div className="project-navigation__section-heading">
            <span>Seus projetos</span>
            {projects.length > 0 && <small>{projects.length} {projects.length === 1 ? 'projeto' : 'projetos'}</small>}
          </div>
          {projects.length > 0 ? projects.map((project) => <section key={project.project_id} className="project-navigation__project">
            <div className={activeProjectId === project.project_id ? 'project-navigation__project-heading is-active' : 'project-navigation__project-heading'}>
              <button type="button" className="project-navigation__chevron" onClick={() => toggle(project.project_id)} aria-label={`Alternar ${project.name}`} aria-expanded={!collapsed.has(project.project_id)}>{collapsed.has(project.project_id) ? '›' : '⌄'}</button>
              <Link to={`/projects/${encodeURIComponent(project.project_id)}`} className="project-navigation__project-name"><span className="project-navigation__project-dot" aria-hidden="true" />{project.name}</Link>
              {onNewChat && <button type="button" className="project-navigation__add-chat" aria-label={`Novo chat em ${project.name}`} onClick={() => onNewChat(project.project_id)}>+</button>}
            </div>
            {!collapsed.has(project.project_id) && project.chats.map((chat) => <NavLink key={chat.conversation_id} to={`/projects/${encodeURIComponent(project.project_id)}/chats/${encodeURIComponent(chat.conversation_id)}`} className={({ isActive }) => isActive ? 'project-navigation__chat project-navigation__chat--project is-active' : 'project-navigation__chat project-navigation__chat--project'}>
              <span className="project-navigation__chat-mark" aria-hidden="true" />
              <span className="project-navigation__chat-title">{chat.title}</span>
              <span className="project-navigation__chat-state" data-state={chat.state} aria-hidden="true" />
            </NavLink>)}
            {!collapsed.has(project.project_id) && project.chats.length === 0 && <p className="project-navigation__empty project-navigation__empty--project">Ainda não há chats neste projeto.</p>}
          </section>) : <p className="project-navigation__empty">Crie um projeto para organizar conversas e contexto.</p>}
        </motion.div>}
      </AnimatePresence>
    </div>

    <div className="project-navigation__actions">
      {view === 'chats' && <button type="button" className="project-navigation__action project-navigation__action--primary" onClick={onNewConversation}>
        <span className="project-navigation__action-icon" aria-hidden="true">+</span>
        <span>Nova conversa</span>
      </button>}
      {view === 'projects' && onCreateProject && <button type="button" className="project-navigation__action project-navigation__action--primary" onClick={onCreateProject}>
        <span className="project-navigation__action-icon" aria-hidden="true">+</span>
        <span>Novo projeto</span>
      </button>}
      <Link className="project-navigation__action project-navigation__action--secondary" to="/schedules">
        <span className="project-navigation__action-icon project-navigation__action-icon--schedule" aria-hidden="true">◷</span>
        <span>Ações agendadas</span>
      </Link>
    </div>
  </nav>
}
