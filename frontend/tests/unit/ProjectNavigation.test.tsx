import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { ProjectNavigation } from '../../src/features/projects/ProjectNavigation'


describe('ProjectNavigation', () => {
  beforeEach(() => localStorage.clear())

  it('keeps standalone chats separate from project chats and collapses groups', async () => {
    const onCreate = vi.fn()
    const onNewChat = vi.fn()
    render(<MemoryRouter><ProjectNavigation standalone={[{ conversation_id: 'chat-standalone', title: 'Standalone', state: 'completed' }]} projects={[{ project_id: 'project-a', name: 'AgentOS', description: null, chats: [{ conversation_id: 'chat-a', title: 'Implementar Skills', state: 'completed', updated_at: null }] }]} onCreateProject={onCreate} onNewChat={onNewChat} /></MemoryRouter>)

    expect(screen.getByText('Standalone')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Projetos' }))
    await waitFor(() => expect(screen.queryByText('Standalone')).not.toBeInTheDocument())
    expect(screen.getByText('Implementar Skills')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Novo chat em AgentOS' }))
    expect(onNewChat).toHaveBeenCalledWith('project-a')
    fireEvent.click(screen.getByRole('button', { name: 'Alternar AgentOS' }))
    expect(screen.queryByText('Implementar Skills')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Novo projeto' }))
    expect(onCreate).toHaveBeenCalledOnce()
  })

  it('marks the open standalone and project conversations as active, including overview routes', async () => {
    const props = {
      standalone: [{ conversation_id: 'chat-standalone', title: 'Standalone', state: 'completed' }],
      projects: [{ project_id: 'project-a', name: 'AgentOS', description: null, chats: [{ conversation_id: 'chat-a', title: 'Implementar Skills', state: 'completed', updated_at: null }] }],
    }
    const standalone = render(<MemoryRouter initialEntries={['/chats/chat-standalone/overview']}><ProjectNavigation {...props} /></MemoryRouter>)
    expect(screen.getByRole('link', { name: 'Standalone' })).toHaveClass('is-active')
    expect(screen.getByRole('link', { name: 'Standalone' })).toHaveAttribute('aria-current', 'page')
    standalone.unmount()

    render(<MemoryRouter initialEntries={['/projects/project-a/chats/chat-a/overview']}><ProjectNavigation {...props} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('tab', { name: 'Projetos' }))
    const projectChat = await screen.findByRole('link', { name: 'Implementar Skills' })
    expect(projectChat).toHaveClass('is-active')
    expect(projectChat).toHaveAttribute('aria-current', 'page')
  })

  it('keeps primary actions and scheduled actions available outside the history scroll', async () => {
    const onNewConversation = vi.fn()
    const onCreateProject = vi.fn()
    render(<MemoryRouter><ProjectNavigation
      standalone={[]}
      projects={[{ project_id: 'project-a', name: 'AgentOS', description: null, chats: [] }]}
      onNewConversation={onNewConversation}
      onCreateProject={onCreateProject}
    /></MemoryRouter>)

    fireEvent.click(screen.getByRole('button', { name: 'Nova conversa' }))
    expect(onNewConversation).toHaveBeenCalledOnce()
    expect(screen.getByRole('link', { name: 'Ações agendadas' })).toHaveAttribute('href', '/schedules')

    fireEvent.click(screen.getByRole('tab', { name: 'Projetos' }))
    expect(await screen.findByRole('button', { name: 'Novo projeto' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Novo projeto' }))
    expect(onCreateProject).toHaveBeenCalledOnce()
    expect(screen.getByRole('link', { name: 'Ações agendadas' })).toHaveAttribute('href', '/schedules')
  })
})
