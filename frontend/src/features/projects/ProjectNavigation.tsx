import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { ProjectSidebarItem } from '../../api/projects'

type Standalone = { conversation_id: string; title: string; state: string }
type Props = { standalone: Standalone[]; projects: ProjectSidebarItem[]; onCreateProject?: () => void; onNewChat?: (projectId: string) => void }

export function ProjectNavigation({ standalone, projects, onCreateProject, onNewChat }: Props) {
  const [view, setView] = useState<'chats' | 'projects'>(() => (localStorage.getItem('agentos.navigation-view') === 'projects' ? 'projects' : 'chats'))
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(JSON.parse(localStorage.getItem('agentos.collapsed-projects') ?? '[]')))
  const reduced = useReducedMotion()
  function change(next: 'chats' | 'projects') { setView(next); localStorage.setItem('agentos.navigation-view', next) }
  function toggle(id: string) { setCollapsed((current) => { const next = new Set(current); next.has(id) ? next.delete(id) : next.add(id); localStorage.setItem('agentos.collapsed-projects', JSON.stringify([...next])); return next }) }
  return <nav className="project-navigation" aria-label="Conversas e projetos">
    <div className="project-navigation__tabs" role="tablist">
      <button role="tab" aria-selected={view === 'chats'} onClick={() => change('chats')}>Chats</button>
      <button role="tab" aria-selected={view === 'projects'} onClick={() => change('projects')}>Projetos</button>
    </div>
    <AnimatePresence mode="wait">
      {view === 'chats' ? <motion.div key="chats" initial={reduced ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={reduced ? undefined : { opacity: 0 }}>
        {standalone.map((chat) => <Link key={chat.conversation_id} to={`/chats/${encodeURIComponent(chat.conversation_id)}`} className="project-navigation__chat">{chat.title}</Link>)}
      </motion.div> : <motion.div key="projects" initial={reduced ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={reduced ? undefined : { opacity: 0 }}>
        {projects.map((project) => <section key={project.project_id} className="project-navigation__project">
          <div className="project-navigation__project-heading"><button type="button" className="project-navigation__chevron" onClick={() => toggle(project.project_id)} aria-label={`Alternar ${project.name}`} aria-expanded={!collapsed.has(project.project_id)}>{collapsed.has(project.project_id) ? '›' : '⌄'}</button><Link to={`/projects/${encodeURIComponent(project.project_id)}`} className="project-navigation__project-name">{project.name}</Link>{onNewChat && <button type="button" className="project-navigation__add-chat" aria-label={`Novo chat em ${project.name}`} onClick={() => onNewChat(project.project_id)}>+</button>}</div>
          {!collapsed.has(project.project_id) && project.chats.map((chat) => <Link key={chat.conversation_id} to={`/projects/${encodeURIComponent(project.project_id)}/chats/${encodeURIComponent(chat.conversation_id)}`} className="project-navigation__chat">{chat.title}</Link>)}
        </section>)}
        {onCreateProject && <button type="button" className="project-navigation__new" onClick={onCreateProject}>Novo projeto</button>}
      </motion.div>}
    </AnimatePresence>
  </nav>
}
