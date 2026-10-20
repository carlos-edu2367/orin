import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../../src/features/conversations/ChatPage'

const ID = 'chat_r13'
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })

function snapshot() {
  return {
    conversation_id: ID, title: 'T', state: 'completed', provider: 'openrouter', model_id: 'model-a',
    messages: [{ message_id: 'm1', role: 'user', content: 'primeira', status: 'completed', turn_id: 'turn-1', created_at: '2026-08-10T20:00:00+00:00' }],
    turns: [{ turn_id: 'turn-1', state: 'completed', created_at: '2026-08-10T20:00:00+00:00', started_at: null, finished_at: null }],
    activities: [], activity_cursor: 'a.1',
    workspace: { kind: 'managed', path: null, folder_name: null, scope: 'chat', project_name: null },
  }
}

function stub() {
  const forks: string[] = []
  const fn = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    if (url.includes('/events?')) return new Response('event: heartbeat\ndata: {"cursor":"a.1"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
    if (url.includes('/queue')) return json({ items: [] })
    if (url.endsWith('/fork') && method === 'POST') {
      forks.push(url)
      await new Promise((resolve) => setTimeout(resolve, 150))
      return json({ conversation_id: `fork_${forks.length}`, title: 'Cópia', prompt: null, messages: 2 })
    }
    if (url.endsWith('/v1/conversations')) return json({ items: [] })
    if (url.includes(`/v1/conversations/${ID}`) && !url.includes(`/v1/conversations/${ID}/`)) return json(snapshot())
    return json({})
  })
  return { fn, forks }
}

describe('W7-R13 Enter twice on a slash command runs it once', () => {
  const originalFetch = globalThis.fetch
  beforeEach(() => { document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">' })
  afterEach(() => { globalThis.fetch = originalFetch; vi.restoreAllMocks() })

  it('a second Enter while /fork runs does not create a second fork', async () => {
    const { fn, forks } = stub()
    globalThis.fetch = fn
    render(
      <MemoryRouter initialEntries={[`/chats/${ID}`]}>
        <Routes><Route path="/chats/:conversationId" element={<ChatPage />} /></Routes>
      </MemoryRouter>,
    )
    const composer = await screen.findByRole('textbox', { name: 'Mensagem' })
    await screen.findByText('primeira', undefined, { timeout: 4000 })
    fireEvent.change(composer, { target: { value: '/fork x' } })
    fireEvent.keyDown(composer, { key: 'Enter' })
    fireEvent.keyDown(composer, { key: 'Enter' })
    await new Promise((resolve) => setTimeout(resolve, 600))
    expect(forks).toHaveLength(1)
  }, 15000)
})
