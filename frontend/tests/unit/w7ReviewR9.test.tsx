import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../../src/features/conversations/ChatPage'

const ID = 'chat_r9'
type Call = { url: string; method: string; body: string }
type Reply = { status?: number; body: unknown }

function snapshot(state: string) {
  return {
    conversation_id: ID, title: 'T', state, mode: 'chat', provider: 'openrouter', model_id: 'model-a',
    messages: [{ message_id: 'm1', role: 'user', content: 'primeira', status: 'completed', retryable: false, turn_id: 'turn-1' }],
    turns: [{ turn_id: 'turn-1', state, created_at: '2026-10-20T10:00:00+00:00', started_at: '2026-10-20T10:00:00+00:00', finished_at: null }],
    activities: [], activity_cursor: 'a.1', workspace: { kind: 'managed', path: null, folder_name: null, scope: 'chat', project_name: null },
  }
}

function stub(route: (call: Call) => Reply | undefined): Call[] {
  const calls: Call[] = []
  globalThis.fetch = vi.fn(async (input: Parameters<typeof fetch>[0], init?: NonNullable<Parameters<typeof fetch>[1]>) => {
    const call = { url: String(input), method: (init?.method ?? 'GET').toUpperCase(), body: String(init?.body ?? '') }
    calls.push(call)
    if (call.url.includes('/events?')) return new Response('event: heartbeat\ndata: {"cursor":"a.1"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
    const reply = route(call) ?? { body: {} }
    return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200, headers: { 'Content-Type': 'application/json' } })
  }) as unknown as typeof fetch
  return calls
}

const renderChat = () => render(<MemoryRouter initialEntries={[`/chats/${ID}`]}><Routes><Route path="/chats/:conversationId" element={<ChatPage />} /></Routes></MemoryRouter>)
const conversationGet = (call: Call) => call.method === 'GET' && new URL(call.url, 'http://x').pathname === `/v1/conversations/${ID}`
const TEXT = 'texto que estava na fila'

describe('W7-R9 "Enviar agora" on a queued message', () => {
  const original = globalThis.fetch
  beforeEach(() => { document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">' })
  afterEach(() => { globalThis.fetch = original; vi.restoreAllMocks() })

  it('keeps the text when the send fails: it is back in the composer, not lost', async () => {
    let state = 'running'
    let queued = [{ queue_id: 'q1', content: TEXT, created_at: '2026-10-20T10:00:01+00:00' }]
    const calls = stub((call) => {
      if (call.url.includes('/queue/q1') && call.method === 'DELETE') { queued = []; return { body: { queue_id: 'q1', cancelled: true } } }
      if (call.url.endsWith('/queue') && call.method === 'GET') return { body: { items: queued } }
      if (call.url.includes('/cancel')) { state = 'cancelled'; return { status: 202, body: { conversation_id: ID, cancelling: ['turn-1'] } } }
      if (call.url.endsWith('/messages') && call.method === 'POST') return { status: 503, body: { error: { code: 'unavailable', message: 'x' } } }
      if (conversationGet(call)) return { body: snapshot(state) }
      return undefined
    })
    renderChat()
    fireEvent.click(await screen.findByRole('button', { name: 'Enviar agora' }))
    await screen.findByText('Não foi possível enviar agora. A mensagem continua com você.')
    const composer = screen.getByLabelText('Mensagem') as HTMLTextAreaElement
    await waitFor(() => expect(composer.value).toBe(TEXT))
    expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/messages'))).toBe(true)
  })

  it('aborts when the turn already read the message (DELETE answers 404): nothing is sent twice', async () => {
    let state = 'running'
    const calls = stub((call) => {
      if (call.url.includes('/queue/q1') && call.method === 'DELETE') return { status: 404, body: { error: { code: 'not_found', message: 'gone' } } }
      if (call.url.endsWith('/queue') && call.method === 'GET') return { body: { items: [{ queue_id: 'q1', content: 'rode o lint', created_at: null }] } }
      if (call.url.includes('/cancel')) { state = 'cancelled'; return { status: 202, body: { conversation_id: ID, cancelling: ['turn-1'] } } }
      if (call.url.endsWith('/messages') && call.method === 'POST') return { status: 201, body: { conversation_id: ID, title: 'T', turn_id: 'turn-2', message_id: 'm9', state: 'queued' } }
      if (conversationGet(call)) return { body: snapshot(state) }
      return undefined
    })
    renderChat()
    fireEvent.click(await screen.findByRole('button', { name: 'Enviar agora' }))
    await screen.findByText('O agente já leu esta mensagem.')
    expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/messages'))).toBe(false)
  }, 20_000)

  it('sends nothing and says so when the turn is still running after the wait', async () => {
    const calls = stub((call) => {
      if (call.url.endsWith('/queue') && call.method === 'GET') return { body: { items: [{ queue_id: 'q1', content: TEXT, created_at: null }] } }
      if (call.url.includes('/cancel')) return { status: 202, body: { conversation_id: ID, cancelling: ['turn-1'] } }
      if (conversationGet(call)) return { body: snapshot('running') }
      return undefined
    })
    renderChat()
    fireEvent.click(await screen.findByRole('button', { name: 'Enviar agora' }))
    await screen.findByText(/ainda está rodando/, undefined, { timeout: 15_000 })
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/messages'))).toBe(false)
  }, 20_000)
})
