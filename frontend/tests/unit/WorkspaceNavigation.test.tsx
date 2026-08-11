import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { WorkspaceNavigation } from '../../src/features/projects/WorkspaceNavigation'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('WorkspaceNavigation', () => {
  it('keeps project creation available in chat navigation and opens a configurable project chat form', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      if (url.includes('/v1/projects/sidebar')) {
        return json({ items: [{ project_id: 'project-a', name: 'AgentOS', description: null, chats: [{ conversation_id: 'chat-a', title: 'Primeiro chat', state: 'completed', updated_at: null }] }] })
      }
      if (url.includes('/v1/providers/openrouter/models')) {
        return json({ items: [{ provider: 'openrouter', model_id: 'model-a', display_name: 'Modelo A', context_window: null, capabilities: [], input_modalities: ['text'], output_modalities: ['text'], pricing: null, is_favorite: false, refreshed_at: null, route_kind: 'model' }] })
      }
      return json({ items: [] })
    })
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

    render(<MemoryRouter initialEntries={['/chats/chat-a']}><Routes><Route path="/chats/:id" element={<WorkspaceNavigation client={client} />} /></Routes></MemoryRouter>)

    fireEvent.click(screen.getByRole('tab', { name: 'Projetos' }))
    await screen.findByText('AgentOS')
    expect(await screen.findByRole('button', { name: 'Novo projeto' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Novo chat em AgentOS' }))

    expect(await screen.findByRole('dialog', { name: 'Novo chat no projeto' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Mensagem inicial' })).toHaveValue('')
    expect(await screen.findByRole('button', { name: /Modelo A/ })).toBeInTheDocument()
  })
})
