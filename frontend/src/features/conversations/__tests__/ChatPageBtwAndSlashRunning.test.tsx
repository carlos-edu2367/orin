import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../../../app/ThemeContext'
import { KeybindingsProvider } from '../../keybindings/engine'
import { ChatPage } from '../ChatPage'

const CONVERSATION_ID = 'conv_btw_slash_running'

type Posted = { url: string; method: string; body: string }

function runningSnapshot() {
  return {
    conversation_id: CONVERSATION_ID,
    title: 'Refatoração',
    state: 'running',
    mode: 'code',
    permission_mode: 'ask_permissions',
    provider: 'anthropic',
    model_id: 'claude-sonnet-5-5',
    messages: [
      { message_id: 'msg-1', role: 'user', content: 'analise a tabela billing_invoices', status: 'completed', retryable: false, turn_id: 'turn-1' },
      { message_id: 'msg-2', role: 'assistant', content: 'Lendo o schema…', status: 'streaming', retryable: false, turn_id: 'turn-1' },
    ],
    turns: [
      {
        turn_id: 'turn-1',
        state: 'running',
        code_mode: 'code',
        created_at: '2026-11-23T18:00:00.000Z',
        started_at: '2026-11-23T18:00:01.000Z',
        finished_at: null,
      },
    ],
    activities: [],
    activity_cursor: 'a.0',
    workspace: { kind: 'local', path: 'C:/Projetos/demo', folder_name: 'demo', scope: 'chat', project_name: null },
  }
}

function installFetch() {
  const requests: Posted[] = []
  globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = typeof init?.body === 'string' ? init.body : ''
    if (method !== 'GET') requests.push({ url, method, body })

    if (url.includes('/events?')) {
      return new Response('event: heartbeat\ndata: {"cursor":"a.0"}\n\n', {
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    if (url.endsWith(`/v1/conversations/${CONVERSATION_ID}/btw`) && method === 'POST') {
      const encoder = new TextEncoder()
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            encoder.encode(
              'event: start\ndata: {"btw_id":"btw_live","provider":"anthropic","model_id":"claude-sonnet-5-5","effort":"low","active_turn_included":true,"context_messages":2}\n\n',
            ),
          )
          controller.enqueue(
            encoder.encode(
              'event: thinking\ndata: {"delta":"conferindo schema"}\n\n',
            ),
          )
          controller.enqueue(
            encoder.encode(
              'event: delta\ndata: {"delta":"A tabela é `billing_invoices`."}\n\n',
            ),
          )
          controller.enqueue(
            encoder.encode(
              'event: done\ndata: {"btw_id":"btw_live","text":"A tabela é `billing_invoices`.","duration_ms":420,"provider":"anthropic","model_id":"claude-sonnet-5-5","effort":"low","usage":{"input_tokens":50,"output_tokens":8,"cached_input_tokens":0}}\n\n',
            ),
          )
          controller.close()
        },
      })
      return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    }
    if (url.includes(`/v1/conversations/${CONVERSATION_ID}`) && !url.includes('/queue') && !url.includes('/events')) {
      return new Response(JSON.stringify(runningSnapshot()), { headers: { 'Content-Type': 'application/json' } })
    }
    return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
  })
  return requests
}

function renderChat() {
  render(
    <ThemeProvider>
      <KeybindingsProvider>
        <MemoryRouter initialEntries={[`/chats/${CONVERSATION_ID}`]}>
          <Routes>
            <Route path="/chats/:conversationId" element={<ChatPage />} />
          </Routes>
        </MemoryRouter>
      </KeybindingsProvider>
    </ThemeProvider>,
  )
}

const composer = () => screen.getByRole('textbox', { name: 'Mensagem' }) as HTMLTextAreaElement

describe('ChatPage /btw and slash command safety during an active turn', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    localStorage.clear()
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('preserves typed text and avoids mutating turn state for /compact, /review, /plan <args> and custom skills while running', async () => {
    const requests = installFetch()
    renderChat()

    await waitFor(() => expect(document.querySelector('form.composer')).toHaveClass('is-running'))

    // /compact while running must not call POST /compact and must keep the typed input
    fireEvent.change(composer(), { target: { value: '/compact foco em api' } })
    fireEvent.keyDown(composer(), { key: 'Enter', code: 'Enter' })
    await screen.findByText(/Espere o turno atual terminar para compactar/i)
    expect(composer().value).toBe('/compact foco em api')
    expect(requests.some((r) => r.url.endsWith('/compact'))).toBe(false)

    // /review <args> while running must keep the typed prompt instead of wiping it
    fireEvent.change(composer(), { target: { value: '/review verifique pagamentos' } })
    fireEvent.keyDown(composer(), { key: 'Enter', code: 'Enter' })
    await screen.findByText(/Espere o turno atual terminar para enviar/i)
    expect(composer().value).toBe('/review verifique pagamentos')

    // /plan <args> while running must not change permission mode or wipe args
    fireEvent.change(composer(), { target: { value: '/plan criar servico de cobranca' } })
    fireEvent.keyDown(composer(), { key: 'Enter', code: 'Enter' })
    await screen.findByText(/Espere o turno atual terminar para enviar o pedido com \/plan/i)
    expect(composer().value).toBe('/plan criar servico de cobranca')
    expect(requests.some((r) => r.url.includes('permission-mode'))).toBe(false)

    // Custom skill (/minha-skill) while running must warn and keep text instead of silent no-op
    fireEvent.change(composer(), { target: { value: '/minha-skill gerar relatorio' } })
    fireEvent.keyDown(composer(), { key: 'Enter', code: 'Enter' })
    await screen.findByText(/Espere o turno atual terminar para executar este comando\/skill/i)
    expect(composer().value).toBe('/minha-skill gerar relatorio')
  })

  it('streams /btw parallel answer during running turn and dismisses BtwCard on first Escape without stopping the turn', async () => {
    const requests = installFetch()
    renderChat()

    await waitFor(() => expect(document.querySelector('form.composer')).toHaveClass('is-running'))

    fireEvent.change(composer(), { target: { value: '/btw qual o nome da tabela?' } })
    fireEvent.keyDown(composer(), { key: 'Enter', code: 'Enter' })

    await screen.findByTestId('btw-card')
    await screen.findByText('billing_invoices')
    expect(requests.some((r) => r.url.endsWith(`/v1/conversations/${CONVERSATION_ID}/btw`))).toBe(true)
    expect(requests.some((r) => r.url.endsWith('/queue'))).toBe(false)

    // First Escape closes BtwCard without calling /stop
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByTestId('btw-card')).not.toBeInTheDocument())
    expect(requests.some((r) => r.url.endsWith('/stop'))).toBe(false)
  })
})
