import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { ChatPage } from '../../src/features/conversations/ChatPage'

type Snapshot = {
  state: string
  messages: Array<Record<string, unknown>>
  activities: Array<Record<string, unknown>>
}

const CONVERSATION_ID = 'chat_e2e'

function activity(sequence: number, type: string, summary: string, payload: Record<string, unknown> = {}) {
  return {
    event_id: `activity:turn-1:${sequence}`,
    event_type: type,
    sequence,
    summary,
    payload,
    occurred_at: '2026-08-10T20:00:00+00:00',
    turn_id: 'turn-1',
    execution_id: 'exe-1',
    agent_id: 'agent:chat_e2e:main',
    parent_agent_id: null,
    cursor: `a.${sequence}`,
  }
}

function snapshotBody(snapshot: Snapshot, workspace: Record<string, unknown> = { kind: 'managed', path: null, folder_name: null, scope: 'chat', project_name: null }) {
  return {
    conversation_id: CONVERSATION_ID,
    title: 'Conversa de teste',
    state: snapshot.state,
    provider: 'openrouter',
    model_id: 'model-a',
    messages: snapshot.messages,
    turns: [{ turn_id: 'turn-1', state: snapshot.state, created_at: '2026-08-10T20:00:00+00:00', started_at: null, finished_at: null }],
    activities: snapshot.activities,
    activity_cursor: 'a.9',
    workspace,
  }
}

/**
 * The stream is stubbed as an immediately-closing body: ChatPage then falls back
 * to its snapshot reconciliation path, which is what a real reconnect looks like.
 */
function stubFetch(current: () => Snapshot, onCancel?: () => void, onSend?: (body: string) => void) {
  return vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    if (url.includes('/events?')) {
      return new Response('event: heartbeat\ndata: {"cursor":"a.9"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
    }
    if (url.includes('/cancel')) {
      onCancel?.()
      return new Response(JSON.stringify({ conversation_id: CONVERSATION_ID, cancelling: ['turn-1'] }), { status: 202, headers: { 'Content-Type': 'application/json' } })
    }
    if (url.includes('/messages') && method === 'POST') {
      onSend?.(String(init?.body ?? ''))
      return new Response(JSON.stringify({ conversation_id: CONVERSATION_ID, title: 't', turn_id: 'turn-2', message_id: 'msg-3', state: 'queued' }), { status: 201, headers: { 'Content-Type': 'application/json' } })
    }
    if (url.endsWith('/v1/projects/sidebar')) {
      return new Response(JSON.stringify({ items: [{ project_id: 'project-e2e', name: 'Projeto de teste', description: null, chats: [] }] }), { headers: { 'Content-Type': 'application/json' } })
    }
    if (url.endsWith('/v1/conversations')) {
      return new Response(JSON.stringify({ items: [{ conversation_id: CONVERSATION_ID, title: 'Conversa de teste', state: current().state }] }), { headers: { 'Content-Type': 'application/json' } })
    }
    if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
      return new Response(JSON.stringify(snapshotBody(current())), { headers: { 'Content-Type': 'application/json' } })
    }
    return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
  })
}

function renderChat() {
  return render(
    <MemoryRouter initialEntries={[`/chats/${CONVERSATION_ID}`]}>
      <Routes><Route path="/chats/:conversationId" element={<ChatPage />} /></Routes>
    </MemoryRouter>,
  )
}

describe('ChatPage', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('rebuilds a finished conversation from the durable snapshot after a reload', async () => {
    globalThis.fetch = stubFetch(() => ({
      state: 'completed',
      messages: [
        { message_id: 'msg-1', role: 'user', content: 'Crie hello.txt', status: 'completed', retryable: false },
        { message_id: 'msg-2', role: 'assistant', content: 'Pronto, criei o arquivo.', status: 'completed', retryable: false },
      ],
      activities: [
        activity(1, 'tool.finished', 'Escreveu hello.txt', { tool_name: 'write_file', tool_kind: 'filesystem', status: 'succeeded', invocation_id: 'call-1', label: 'hello.txt' }),
        activity(2, 'turn.completed', 'Resposta concluída', { state: 'completed' }),
      ],
    }))

    renderChat()

    expect(await screen.findByText('Crie hello.txt')).toBeInTheDocument()
    expect(await screen.findByText('Pronto, criei o arquivo.')).toBeInTheDocument()
    expect(await screen.findByText('Escreveu hello.txt')).toBeInTheDocument()
    // A finished turn must not look like it is still running.
    expect(screen.queryByRole('button', { name: 'Parar execução' })).not.toBeInTheDocument()
  })

  it('reuses the shared navigation for chat history and projects', async () => {
    globalThis.fetch = stubFetch(() => ({ state: 'completed', messages: [], activities: [] }))

    renderChat()

    expect(await screen.findByRole('link', { name: 'Conversa de teste' })).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('tab', { name: 'Projetos' }))
    expect(await screen.findByText('Projeto de teste')).toBeInTheDocument()
  })

  it('shows the action that happened between two pieces of narration in the order it happened', async () => {
    globalThis.fetch = stubFetch(() => ({
      state: 'completed',
      messages: [
        { message_id: 'msg-1', role: 'user', content: 'Crie o resumo', status: 'completed', retryable: false },
        { message_id: 'msg-2', role: 'assistant', content: 'Vou consultar o site. Pronto, terminei.', status: 'completed', retryable: false },
      ],
      activities: [
        activity(1, 'assistant.delta', 'Vou consultar o site. ', { message_id: 'msg-2', content: 'Vou consultar o site. ' }),
        activity(2, 'tool.finished', 'Consultou example.com', { tool_name: 'fetch_url', tool_kind: 'web', status: 'succeeded', invocation_id: 'call-1', label: 'example.com' }),
        activity(3, 'assistant.delta', 'Pronto, terminei.', { message_id: 'msg-2', content: 'Pronto, terminei.' }),
        activity(4, 'turn.completed', 'Resposta concluída', {}),
      ],
    }))

    renderChat()

    const first = await screen.findByText('Vou consultar o site.', { exact: false })
    const action = await screen.findByText('Consultou example.com')
    const second = await screen.findByText('Pronto, terminei.', { exact: false })

    expect(first.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(action.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('formats Markdown in assistant messages and keeps user messages as plain text', async () => {
    globalThis.fetch = stubFetch(() => ({
      state: 'completed',
      messages: [
        { message_id: 'msg-1', role: 'user', content: '**Mensagem do usuário**', status: 'completed', retryable: false },
        { message_id: 'msg-2', role: 'assistant', content: '**Arquivos**\n\n- hello.txt', status: 'completed', retryable: false },
      ],
      activities: [],
    }))

    renderChat()

    expect(await screen.findByText('**Mensagem do usuário**')).toBeInTheDocument()
    expect(await screen.findByText('Arquivos', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByRole('list')).toBeInTheDocument()
    expect(screen.getByText('hello.txt')).toBeInTheDocument()
  })

  it('expands an activity row into its technical detail on demand', async () => {
    globalThis.fetch = stubFetch(() => ({
      state: 'completed',
      messages: [],
      activities: [activity(1, 'tool.finished', '$ npm test', { tool_name: 'run_command', tool_kind: 'terminal', status: 'failed', invocation_id: 'call-1', error_code: 'TOOL_FAILED' })],
    }))

    renderChat()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /npm test/ }))

    expect(await screen.findByText('TOOL_FAILED')).toBeInTheDocument()
  })

  it('offers Stop while a turn runs and sends the cancel to the backend', async () => {
    const cancelled = vi.fn()
    globalThis.fetch = stubFetch(() => ({
      state: 'running',
      messages: [{ message_id: 'msg-1', role: 'user', content: 'Trabalhe', status: 'completed', retryable: false }],
      activities: [activity(1, 'tool.started', 'Executando run_command', { tool_name: 'run_command', tool_kind: 'terminal', invocation_id: 'call-1' })],
    }), cancelled)

    renderChat()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Parar execução' }))

    await waitFor(() => expect(cancelled).toHaveBeenCalled())
  })

  it('leaves the running UI as soon as a terminal event arrives on an open stream', async () => {
    let completed = false
    let closeStream: () => void = () => {}
    let emitTerminal: () => void = () => {}
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/events?')) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            closeStream = () => controller.close()
            emitTerminal = () => {
              completed = true
              controller.enqueue(new TextEncoder().encode(
                'id: a.10\nevent: turn.completed\ndata: {"event_id":"activity:turn-1:10","event_type":"turn.completed","sequence":10,"summary":"Resposta concluída","payload":{"state":"completed"},"occurred_at":"2026-08-10T20:00:10+00:00","turn_id":"turn-1","execution_id":"exe-1","agent_id":"agent:chat_e2e:main","cursor":"a.10"}\n\n',
              ))
            }
          },
        })
        return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.endsWith('/v1/projects/sidebar')) {
        return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/v1/conversations')) {
        return new Response(JSON.stringify({ items: [{ conversation_id: CONVERSATION_ID, title: 'Conversa de teste', state: completed ? 'completed' : 'running' }] }), { headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
        return new Response(JSON.stringify(snapshotBody({
          state: completed ? 'completed' : 'running',
          messages: [{ message_id: 'msg-1', role: 'assistant', content: 'Resposta concluída.', status: 'completed', retryable: false }],
          activities: [],
        })), { headers: { 'Content-Type': 'application/json' } })
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    renderChat()

    await screen.findByRole('button', { name: 'Parar execução' })
    emitTerminal()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Parar execução' })).not.toBeInTheDocument())
    expect(screen.queryByText('Pensando')).not.toBeInTheDocument()
    closeStream()
  })

  it('sends a follow-up message and shows it immediately', async () => {
    const sent = vi.fn()
    globalThis.fetch = stubFetch(() => ({
      state: 'completed',
      messages: [{ message_id: 'msg-1', role: 'assistant', content: 'Feito.', status: 'completed', retryable: false }],
      activities: [],
    }), undefined, sent)

    renderChat()
    const user = userEvent.setup()
    await screen.findByText('Feito.')
    await user.type(screen.getByRole('textbox', { name: 'Mensagem' }), 'e agora?')
    await user.click(screen.getByRole('button', { name: 'Enviar mensagem' }))

    await waitFor(() => expect(sent).toHaveBeenCalled())
    expect(String(sent.mock.calls[0][0])).toContain('e agora?')
  })

  it('sends a turn with an attachment and no text', async () => {
    const sent = vi.fn()
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.9"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.endsWith('/v1/uploads') && method === 'POST') {
        return new Response(JSON.stringify({ upload_id: 'upl_1', filename: 'foto.png', media_type: 'image/png', kind: 'image', bytes: 3 }), { status: 201, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes('/messages') && method === 'POST') {
        sent(String(init?.body ?? ''))
        return new Response(JSON.stringify({ conversation_id: CONVERSATION_ID, title: 't', turn_id: 'turn-2', message_id: 'msg-3', state: 'queued' }), { status: 201, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/v1/projects/sidebar')) {
        return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/v1/conversations')) {
        return new Response(JSON.stringify({ items: [{ conversation_id: CONVERSATION_ID, title: 'Conversa de teste', state: 'completed' }] }), { headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
        return new Response(JSON.stringify(snapshotBody({
          state: 'completed',
          messages: [{ message_id: 'msg-1', role: 'assistant', content: 'Feito.', status: 'completed', retryable: false }],
          activities: [],
        })), { headers: { 'Content-Type': 'application/json' } })
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    const { container } = renderChat()
    await screen.findByText('Feito.')

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File([new Uint8Array([1, 2, 3])], 'foto.png', { type: 'image/png' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => expect(screen.getByRole('button', { name: 'Enviar mensagem' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Enviar mensagem' }))

    await waitFor(() => expect(sent).toHaveBeenCalled())
    const body = JSON.parse(String(sent.mock.calls[0][0]))
    expect(body).toEqual({ message: '', attachments: ['upl_1'] })
  })

  it('keeps the fixed composer fully revealed when reading the latest message', async () => {
    globalThis.fetch = stubFetch(() => ({
      state: 'completed',
      messages: [{ message_id: 'msg-1', role: 'assistant', content: 'Feito.', status: 'completed', retryable: false }],
      activities: [],
    }))

    const { container } = renderChat()
    await screen.findByText('Feito.')
    const scroll = container.querySelector('.chat__scroll') as HTMLDivElement
    Object.defineProperties(scroll, {
      clientHeight: { configurable: true, value: 400 },
      scrollHeight: { configurable: true, value: 1_000 },
      scrollTop: { configurable: true, writable: true, value: 600 },
    })
    fireEvent.scroll(scroll)

    expect(screen.getByTestId('chat-composer')).toHaveAttribute('data-at-bottom', 'true')

    scroll.scrollTop = 200
    fireEvent.scroll(scroll)

    expect(screen.getByTestId('chat-composer')).toHaveAttribute('data-at-bottom', 'false')
  })

  it('renders an agent-to-agent exchange with its direction and preview', async () => {
    globalThis.fetch = stubFetch(() => ({
      state: 'completed',
      messages: [],
      activities: [
        activity(1, 'agent.created', 'Criou o agente Researcher', { label: 'Researcher', agent_name: 'Researcher' }),
        activity(2, 'agent.message_sent', 'Enviou uma tarefa para Researcher', { label: 'Researcher', content: 'Investigue a documentação' }),
      ],
    }))

    renderChat()

    expect(await screen.findByText(/Criou o agente/)).toBeInTheDocument()
    expect(await screen.findByText(/Investigue a documentação/)).toBeInTheDocument()
  })

  it('shows the attached folder next to the composer', async () => {
    const snapshot: Snapshot = { state: 'idle', messages: [], activities: [] }
    const local = { kind: 'local', path: 'D:/site', folder_name: 'site', scope: 'chat', project_name: null }
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/events?')) return new Response('event: heartbeat\ndata: {"cursor":"a.9"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
      if (url.endsWith('/v1/projects/sidebar')) return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
      if (url.endsWith('/v1/conversations')) return new Response(JSON.stringify({ items: [{ conversation_id: CONVERSATION_ID, title: 'Conversa de teste', state: 'idle' }] }), { headers: { 'Content-Type': 'application/json' } })
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) return new Response(JSON.stringify(snapshotBody(snapshot, local)), { headers: { 'Content-Type': 'application/json' } })
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    renderChat()

    expect(await screen.findByRole('button', { name: /site/ })).toBeInTheDocument()
  })
})
