import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../../src/features/conversations/ChatPage'

const ID = 'chat_r14'
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })

function snapshot(state: string, mode: string) {
  return {
    conversation_id: ID, title: 'T', state, mode, provider: 'openrouter', model_id: 'model-a',
    messages: [{ message_id: 'm1', role: 'user', content: 'primeira', status: 'completed', turn_id: 'turn-1', created_at: '2026-08-10T20:00:00+00:00' }],
    turns: [{ turn_id: 'turn-1', state, created_at: '2026-08-10T20:00:00+00:00', started_at: null, finished_at: null }],
    activities: [], activity_cursor: 'a.1',
    workspace: { kind: 'managed', path: null, folder_name: null, scope: 'chat', project_name: null },
  }
}

type Call = { method: string; url: string }

function stub(mode: string) {
  const calls: Call[] = []
  let state = 'running'
  const fn = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    if (method !== 'GET') calls.push({ method, url })
    if (url.includes('/events?')) return new Response('event: heartbeat\ndata: {"cursor":"a.1"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
    if (url.includes('/queue')) return json({ items: [] })
    if (url.includes('/cancel')) { state = 'cancelled'; return json({ conversation_id: ID, cancelling: ['turn-1'] }, 202) }
    if (url.endsWith('/fork') && method === 'POST') return json({ conversation_id: 'fork_1', title: 'Cópia', prompt: null, messages: 2 })
    if (url.endsWith('/shell') && method === 'POST') return json({ turn_id: 'turn-s' })
    if (url.endsWith('/messages') && method === 'POST') return json({ conversation_id: ID, title: 't', turn_id: 'turn-2', message_id: 'm3', state: 'queued' }, 201)
    if (url.endsWith('/v1/conversations')) return json({ items: [] })
    if (url.includes(`/v1/conversations/${ID}`) && !url.includes(`/v1/conversations/${ID}/`)) return json(snapshot(state, mode))
    return json({})
  })
  return { fn, calls }
}

function renderChat() {
  return render(
    <MemoryRouter initialEntries={[`/chats/${ID}`]}>
      <Routes><Route path="/chats/:conversationId" element={<ChatPage />} /></Routes>
    </MemoryRouter>,
  )
}

describe('W7-R14 ctrl+enter (send now) goes through the same rules as Enter', () => {
  const originalFetch = globalThis.fetch
  beforeEach(() => { document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">' })
  afterEach(() => { globalThis.fetch = originalFetch; vi.restoreAllMocks() })

  it('a built-in command typed while a turn runs runs as the command, not as a prompt to the model', async () => {
    const { fn, calls } = stub('chat')
    globalThis.fetch = fn
    renderChat()
    const composer = await screen.findByRole('textbox', { name: 'Mensagem' })
    await screen.findByText('primeira', undefined, { timeout: 4000 })
    fireEvent.change(composer, { target: { value: '/fork x' } })
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/fork'))).toBe(true), { timeout: 8000 })
    expect(calls.some((call) => call.url.endsWith(`/v1/conversations/${ID}/messages`) && call.method === 'POST')).toBe(false) // the fork's own prompt goes to fork_1
  }, 15000)

  it('a !command in a Code conversation runs as a shell turn, not as a prompt', async () => {
    const { fn, calls } = stub('code')
    globalThis.fetch = fn
    renderChat()
    const composer = await screen.findByRole('textbox', { name: 'Mensagem' })
    await screen.findByText('primeira', undefined, { timeout: 4000 })
    fireEvent.change(composer, { target: { value: '!git status' } })
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/shell'))).toBe(true), { timeout: 8000 })
    expect(calls.some((call) => call.url.endsWith('/messages') && call.method === 'POST')).toBe(false)
  }, 15000)
})
