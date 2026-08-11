import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { ProjectNavigation } from '../../src/features/projects/ProjectNavigation'


describe('ProjectNavigation', () => {
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
})
