import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { WorkspaceNavigation } from '../../src/features/projects/WorkspaceNavigation'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>
}

describe('WorkspaceNavigation', () => {
  it('keeps project creation available and opens project chats in the normal composer route', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      if (url.includes('/v1/projects/sidebar')) {
        return json({ items: [{ project_id: 'project-a', name: 'AgentOS', description: null, chats: [{ conversation_id: 'chat-a', title: 'Primeiro chat', state: 'completed', updated_at: null }] }] })
      }
      return json({ items: [] })
    })
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    render(<MemoryRouter initialEntries={['/chats/chat-a']}><Routes><Route path="/chats/:id" element={<><WorkspaceNavigation client={client} /><LocationProbe /></>} /><Route path="*" element={<LocationProbe />} /></Routes></MemoryRouter>)

    fireEvent.click(screen.getByRole('tab', { name: 'Projetos' }))
    await screen.findByText('AgentOS')
    expect(await screen.findByRole('button', { name: 'Novo projeto' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Novo chat em AgentOS' }))

    expect(screen.queryByRole('dialog', { name: 'Novo chat no projeto' })).not.toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/projects/project-a/new')
  })
})
