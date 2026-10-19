import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
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

function CurrentPath() {
  return <output data-testid="current-path">{useLocation().pathname}</output>
}

function renderChat(initialEntry = `/chats/${CONVERSATION_ID}`) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/chats/:conversationId" element={<ChatPage />} />
        <Route path="/chats/:conversationId/overview" element={<ChatPage />} />
        <Route path="/projects/:projectId/chats/:conversationId" element={<ChatPage />} />
        <Route path="/projects/:projectId/chats/:conversationId/overview" element={<ChatPage />} />
      </Routes>
      <CurrentPath />
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
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Parar execução' })).not.toBeInTheDocument())
    expect(screen.queryByText('Pensando')).not.toBeInTheDocument()
  })

  it('keeps question answers enabled while the turn waits for the user', async () => {
    const sent = vi.fn()
    globalThis.fetch = stubFetch(() => ({
      state: 'waiting_user',
      messages: [],
      activities: [
        activity(1, 'tool.finished', 'Aguardando sua resposta', {
          tool_name: 'ask_user', tool_kind: 'user_input', status: 'succeeded', invocation_id: 'call-questions',
          questions: [{ id: 'name', question: 'Como devo te chamar?', mode: 'text', options: [] }],
        }),
        activity(2, 'turn.waiting_user', 'Aguardando sua resposta', { state: 'waiting_user' }),
      ],
    }), undefined, sent)

    renderChat()

    expect(await screen.findByRole('button', { name: 'Enviar respostas' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Parar execuÃ§Ã£o' })).not.toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Enviar respostas' }))

    await waitFor(() => expect(sent).toHaveBeenCalled())
    expect(String(sent.mock.calls[0][0])).toContain('Respostas às perguntas do agente:')
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
    await waitFor(() => expect(screen.queryByText('Pensando')).not.toBeInTheDocument())
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

  it('restores a failed request to the composer for an explicit retry', async () => {
    globalThis.fetch = stubFetch(() => ({
      state: 'failed',
      messages: [
        { message_id: 'msg-1', role: 'user', content: 'Corrija o erro', status: 'completed', retryable: false },
        { message_id: 'msg-2', role: 'assistant', content: '', status: 'failed', retryable: true },
      ],
      activities: [],
    }))

    renderChat()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Usar solicitação novamente' }))

    expect(screen.getByRole('textbox', { name: 'Mensagem' })).toHaveValue('Corrija o erro')
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
    expect(body).toEqual({
      message: '', attachments: ['upl_1'],
      selection: { provider: 'openrouter', model_id: 'model-a' },
    })
  })

  it('changes the provider and model used by the next turn', async () => {
    const sent = vi.fn()
    let currentProvider = 'openrouter'
    let currentModel = 'model-a'
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.includes('/events?')) return new Response('event: heartbeat\ndata: {"cursor":"a.9"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
      if (url.includes('/v1/providers/') && url.endsWith('/models')) {
        const provider = url.split('/v1/providers/')[1].split('/')[0]
        const items = provider === 'ollama'
          ? [{ provider: 'ollama', model_id: 'model-b', display_name: 'Modelo B', context_window: 128000, capabilities: ['tools'], input_modalities: ['text'], output_modalities: ['text'], pricing: null, is_favorite: true, refreshed_at: null, route_kind: 'model' }]
          : [{ provider: 'openrouter', model_id: 'model-a', display_name: 'Modelo A', context_window: 128000, capabilities: ['tools'], input_modalities: ['text'], output_modalities: ['text'], pricing: null, is_favorite: true, refreshed_at: null, route_kind: 'model' }]
        return new Response(JSON.stringify({ items }), { headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes('/messages') && method === 'POST') {
        const body = JSON.parse(String(init?.body ?? '{}')) as { selection?: { provider: string; model_id: string } }
        sent(body)
        currentProvider = body.selection?.provider ?? currentProvider
        currentModel = body.selection?.model_id ?? currentModel
        return new Response(JSON.stringify({ conversation_id: CONVERSATION_ID, title: 't', turn_id: 'turn-2', message_id: 'msg-3', state: 'queued' }), { status: 201, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
        return new Response(JSON.stringify({ ...snapshotBody({ state: 'completed', messages: [{ message_id: 'msg-1', role: 'assistant', content: 'Feito.', status: 'completed', retryable: false }], activities: [] }), provider: currentProvider, model_id: currentModel }), { headers: { 'Content-Type': 'application/json' } })
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    renderChat()
    const user = userEvent.setup()
    await screen.findByText('Feito.')
    await user.click(screen.getByRole('button', { name: /openrouter/i }))
    await user.click(screen.getByRole('option', { name: 'ollama' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Modelo B' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Modelo B' }))
    await user.click(await screen.findByRole('option', { name: /Modelo B/ }))
    await user.type(screen.getByRole('textbox', { name: 'Mensagem' }), 'troquei de modelo')
    await user.click(screen.getByRole('button', { name: 'Enviar mensagem' }))

    await waitFor(() => expect(sent).toHaveBeenCalledWith({
      message: 'troquei de modelo', attachments: [], selection: { provider: 'ollama', model_id: 'model-b' },
    }))
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

  it('keeps the scroll position and offers a counted return action for new stream activity', async () => {
    let emitActivity: () => void = () => {}
    let closeStream: () => void = () => {}
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/events?')) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            closeStream = () => controller.close()
            emitActivity = () => controller.enqueue(new TextEncoder().encode(
              'id: a.10\nevent: tool.started\ndata: {"event_id":"activity:turn-1:10","event_type":"tool.started","sequence":10,"summary":"Executando busca","payload":{"tool_name":"search","tool_kind":"web","invocation_id":"call-10"},"occurred_at":"2026-08-10T20:00:10+00:00","turn_id":"turn-1","execution_id":"exe-1","agent_id":"agent:chat_e2e:main","cursor":"a.10"}\n\n',
            ))
          },
        })
        return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.endsWith('/v1/projects/sidebar')) return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
      if (url.endsWith('/v1/conversations')) return new Response(JSON.stringify({ items: [{ conversation_id: CONVERSATION_ID, title: 'Conversa de teste', state: 'running' }] }), { headers: { 'Content-Type': 'application/json' } })
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) return new Response(JSON.stringify(snapshotBody({
        state: 'running', messages: [{ message_id: 'msg-1', role: 'assistant', content: 'Vou pesquisar.', status: 'streaming', retryable: false }], activities: [],
      })), { headers: { 'Content-Type': 'application/json' } })
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    const { container } = renderChat()
    await screen.findByText('Vou pesquisar.')
    const scroll = container.querySelector('.chat__scroll') as HTMLDivElement
    const scrollTo = vi.fn()
    Object.defineProperties(scroll, {
      clientHeight: { configurable: true, value: 400 },
      scrollHeight: { configurable: true, value: 1_000 },
      scrollTop: { configurable: true, writable: true, value: 200 },
      scrollTo: { configurable: true, value: scrollTo },
    })
    fireEvent.scroll(scroll)
    scrollTo.mockClear()

    emitActivity()

    const latest = await screen.findByRole('button', { name: 'Ir para o fim · 1 novas atividades' })
    expect(scrollTo).not.toHaveBeenCalled()
    await userEvent.setup().click(latest)
    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ top: 1_000 }))
    expect(screen.queryByRole('button', { name: /Ir para o fim/ })).not.toBeInTheDocument()
    closeStream()
  })

  it('updates a pinned viewport without queuing smooth scroll animations per event', async () => {
    let emitActivity: () => void = () => {}
    let closeStream: () => void = () => {}
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/events?')) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            closeStream = () => controller.close()
            emitActivity = () => controller.enqueue(new TextEncoder().encode(
              `id: a.10\nevent: tool.started\ndata: ${JSON.stringify(activity(10, 'tool.started', 'Executando busca', { tool_name: 'search', tool_kind: 'web', invocation_id: 'call-10' }))}\n\n`,
            ))
          },
        })
        return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.endsWith('/v1/projects/sidebar')) return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
      if (url.endsWith('/v1/conversations')) return new Response(JSON.stringify({ items: [{ conversation_id: CONVERSATION_ID, title: 'Conversa de teste', state: 'running' }] }), { headers: { 'Content-Type': 'application/json' } })
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) return new Response(JSON.stringify(snapshotBody({
        state: 'running',
        messages: [{ message_id: 'msg-1', role: 'assistant', content: 'Resposta.', status: 'streaming', retryable: false }],
        activities: [],
      })), { headers: { 'Content-Type': 'application/json' } })
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    const { container } = renderChat()
    await screen.findByText('Resposta.')
    const scroll = container.querySelector('.chat__scroll') as HTMLDivElement
    const scrollTo = vi.fn()
    Object.defineProperties(scroll, {
      clientHeight: { configurable: true, value: 400 },
      scrollHeight: { configurable: true, value: 1_000 },
      scrollTop: { configurable: true, writable: true, value: 600 },
      scrollTo: { configurable: true, value: scrollTo },
    })
    fireEvent.scroll(scroll)
    scrollTo.mockClear()

    emitActivity()

    await waitFor(() => expect(scroll.scrollTop).toBe(1_000))
    expect(scrollTo).not.toHaveBeenCalled()
    closeStream()
  })

  it('keeps streamed text after the bounded activity window rolls over', async () => {
    const prefix = Array.from({ length: 499 }, (_, index) => `parte-${index + 1}|`).join('')
    let emitDelta: (sequence: number) => void = () => {}
    let closeStream: () => void = () => {}
    const initialActivities = Array.from({ length: 499 }, (_, index) => activity(index + 1, 'assistant.delta', 'Resposta em andamento', {
      message_id: 'msg-1', content: `parte-${index + 1}|`,
    }))
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/events?')) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            closeStream = () => controller.close()
            emitDelta = (sequence) => controller.enqueue(new TextEncoder().encode(
              `id: a.${sequence}\nevent: assistant.delta\ndata: ${JSON.stringify(activity(sequence, 'assistant.delta', 'Resposta em andamento', { message_id: 'msg-1', content: `parte-${sequence}|` }))}\n\n`,
            ))
          },
        })
        return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.endsWith('/v1/projects/sidebar')) return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
      if (url.endsWith('/v1/conversations')) return new Response(JSON.stringify({ items: [{ conversation_id: CONVERSATION_ID, title: 'Conversa de teste', state: 'running' }] }), { headers: { 'Content-Type': 'application/json' } })
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) return new Response(JSON.stringify(snapshotBody({
        state: 'running',
        messages: [{ message_id: 'msg-1', role: 'assistant', content: '', status: 'streaming', retryable: false }],
        activities: initialActivities,
      })), { headers: { 'Content-Type': 'application/json' } })
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    renderChat()
    await waitFor(() => expect(document.querySelector('.bubble--assistant .markdown-message')?.textContent).toContain('parte-1|'))

    emitDelta(500)
    emitDelta(501)

    await waitFor(() => expect(document.querySelector('.bubble--assistant .markdown-message')?.textContent).toBe(`${prefix}parte-500|parte-501|`))
    closeStream()
  })

  it('keeps the project chat route and keeps composer mounted when toggling the overview', async () => {
    globalThis.fetch = stubFetch(() => ({ state: 'completed', messages: [], activities: [] }))

    renderChat(`/projects/project-e2e/chats/${CONVERSATION_ID}`)
    await screen.findByText('Conversa de teste')

    await userEvent.setup().click(screen.getByRole('button', { name: 'Visão geral' }))
    expect(screen.getByTestId('current-path')).toHaveTextContent(`/projects/project-e2e/chats/${CONVERSATION_ID}/overview`)
    expect(screen.getByTestId('chat-composer')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Visão geral' }))
    expect(screen.getByTestId('current-path')).toHaveTextContent(`/projects/project-e2e/chats/${CONVERSATION_ID}`)
    expect(screen.getByTestId('chat-composer')).toBeInTheDocument()
  })

  it('keeps a missing conversation out of the composer and gives the person a safe recovery path', async () => {
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
        return new Response(JSON.stringify({ error: { code: 'resource_not_found', category: 'NOT_FOUND', message_key: 'resource_not_found', retryable: false, retry_after: null } }), { status: 404, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
    })

    renderChat()

    expect(await screen.findByRole('heading', { name: 'Não foi possível abrir esta conversa' })).toBeInTheDocument()
    expect(screen.getByText('Esta conversa não está disponível neste workspace.')).toBeInTheDocument()
    expect(screen.queryByTestId('chat-composer')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Nova conversa' })).toHaveAttribute('href', '/')
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

  it('renders Orin Code badge, workspace chip, right-side files drawer with search, TodoBar, and scopes Shift+Tab permission mode changes to textarea and conversation', async () => {
    const permissionUpdates: Array<Record<string, unknown>> = []
    let currentPermissionMode = 'accept_edits'
    let globalSettingsCalled = false
    const local = { kind: 'local', path: 'D:/orin', folder_name: 'orin', scope: 'chat', project_name: null }
    const snapshot: Snapshot = {
      state: 'completed',
      messages: [
        { message_id: 'msg-1', role: 'user', content: 'Refatore o módulo', status: 'completed', retryable: false },
        { message_id: 'msg-2', role: 'assistant', content: 'Refatoração concluída.', status: 'completed', retryable: false },
      ],
      activities: [
        activity(1, 'tool.finished', 'Atualizou tarefas', {
          tool_name: 'todo_write',
          tool_kind: 'filesystem',
          status: 'succeeded',
          todos: [
            { id: 'todo-1', content: 'Inspecionar ChatPage', status: 'completed', activeForm: 'Inspecionando ChatPage' },
            { id: 'todo-2', content: 'Testar integração', status: 'in_progress', activeForm: 'Testando integração' },
          ],
        }),
      ],
    }

    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.9"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.includes('/v1/settings/code-mode') && method === 'PUT') {
        globalSettingsCalled = true
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}/permission-mode`) && method === 'PATCH') {
        const parsed = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
        if (typeof parsed.permission_mode === 'string') {
          currentPermissionMode = parsed.permission_mode
        }
        permissionUpdates.push(parsed)
        return new Response(
          JSON.stringify({ conversation_id: CONVERSATION_ID, permission_mode: parsed.permission_mode }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}/workspace-files`) || url.includes(`/v1/conversations/${CONVERSATION_ID}/workspace/files`)) {
        const parsedUrl = new URL(url, 'http://localhost')
        const q = parsedUrl.searchParams.get('query') ?? ''
        const allFiles = ['src/App.tsx', 'README.md']
        const filtered = q ? allFiles.filter((item) => item.toLowerCase().includes(q.toLowerCase())) : allFiles
        return new Response(JSON.stringify({ files: filtered, items: filtered }), { headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) {
        return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
        return new Response(
          JSON.stringify({
            ...snapshotBody(snapshot, local),
            mode: 'code',
            permission_mode: currentPermissionMode,
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    renderChat()
    const user = userEvent.setup()

    expect(await screen.findByTestId('orin-code-badge')).toHaveTextContent('>_ Orin Code')
    expect(screen.getByTestId('orin-code-workspace-chip')).toHaveTextContent('orin')
    expect(screen.queryByRole('button', { name: /Ativar Modo Code|Desativar Modo Code/i })).not.toBeInTheDocument()
    expect(screen.queryByTestId('workspace-files-drawer')).not.toBeInTheDocument()
    await user.click(screen.getByTestId('workspace-files-drawer-toggle'))
    const drawer = await screen.findByTestId('workspace-files-drawer')
    expect(drawer).toBeInTheDocument()
    expect(await screen.findByText('src/App.tsx')).toBeInTheDocument()
    expect(screen.getByText('README.md')).toBeInTheDocument()

    const searchInput = screen.getByTestId('workspace-files-search')
    await user.type(searchInput, 'App')
    await waitFor(() => {
      expect(screen.getByText('src/App.tsx')).toBeInTheDocument()
      expect(screen.queryByText('README.md')).not.toBeInTheDocument()
    })

    expect(screen.getByTestId('todo-bar')).toBeInTheDocument()

    const pill = screen.getByTestId('code-permission-mode-pill')
    expect(pill).toHaveTextContent('⏵⏵ Aceitar edições')

    // Global Shift+Tab outside composer textarea must not change permission mode
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true })
    expect(pill).toHaveTextContent('⏵⏵ Aceitar edições')

    const textarea = screen.getByRole('textbox', { name: 'Mensagem' })
    textarea.focus()
    fireEvent.keyDown(textarea, { key: 'Tab', shiftKey: true })
    expect(pill).toHaveTextContent('⏸ Modo Plano')
    await waitFor(() => {
      expect(permissionUpdates).toContainEqual({ permission_mode: 'plan_mode' })
    })
    expect(globalSettingsCalled).toBe(false)
  })

  it('immediately hides AgentPulse when activity status is terminal even if snapshot turn state is still running', async () => {
    globalThis.fetch = stubFetch(() => ({
      state: 'running',
      messages: [{ message_id: 'msg-1', role: 'user', content: 'Execute', status: 'completed', retryable: false }],
      activities: [
        activity(1, 'code_mode.completed', 'Execução concluída', { stage: 'completed' }),
      ],
    }))

    renderChat()

    expect(await screen.findByText('Execute')).toBeInTheDocument()
    expect(screen.queryByText('Pensando')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Parar execução' })).not.toBeInTheDocument()
  })

  it('keeps Parar execução visible when activeTurn.state is starting or streaming and stops on Escape', async () => {
    const cancelled = vi.fn()
    globalThis.fetch = stubFetch(() => ({
      state: 'streaming',
      messages: [{ message_id: 'msg-1', role: 'user', content: 'Analise os arquivos', status: 'completed', retryable: false }],
      activities: [activity(1, 'tool.started', 'Lendo src/index.ts', { tool_name: 'read_file', tool_kind: 'filesystem', invocation_id: 'call-1' })],
    }), cancelled)

    renderChat()

    const stopButton = await screen.findByRole('button', { name: 'Parar execução' })
    expect(stopButton).toBeInTheDocument()

    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(cancelled).toHaveBeenCalledTimes(1))
  })

  it('does not hide Parar execução when a subagent emits turn.completed while the root turn is still running', async () => {
    globalThis.fetch = stubFetch(() => ({
      state: 'running',
      messages: [{ message_id: 'msg-1', role: 'user', content: 'Delegue a pesquisa', status: 'completed', retryable: false }],
      activities: [
        {
          ...activity(1, 'turn.completed', 'Subagente concluiu', { state: 'completed' }),
          agent_id: 'agent:chat_e2e:sub-1',
          parent_agent_id: 'agent:chat_e2e:main',
        },
      ],
    }))

    renderChat()

    expect(await screen.findByRole('button', { name: 'Parar execução' })).toBeInTheDocument()
  })

  it('disables PlanApprovalCard actions on completed or historical turns so old plans cannot be re-submitted', async () => {
    const sent = vi.fn()
    globalThis.fetch = stubFetch(() => ({
      state: 'completed',
      messages: [
        { message_id: 'msg-1', role: 'user', content: 'Planeje a mudança', status: 'completed', retryable: false, turn_id: 'turn-1' },
        { message_id: 'msg-2', role: 'assistant', content: 'Plano finalizado.', status: 'completed', retryable: false, turn_id: 'turn-1' },
      ],
      activities: [
        activity(1, 'tool.finished', 'Plano proposto', {
          tool_name: 'exit_plan_mode',
          tool_kind: 'plan',
          status: 'succeeded',
          invocation_id: 'call-plan-1',
          plan: '# Plano\n1. Passo um',
        }),
        activity(2, 'turn.completed', 'Resposta concluída', { state: 'completed' }),
      ],
    }), undefined, sent)

    renderChat()

    expect(await screen.findByTestId('plan-approval-card')).toBeInTheDocument()
    expect(screen.getByTestId('plan-approve-accept-edits')).toBeDisabled()
    expect(screen.getByTestId('plan-approve-ask-permissions')).toBeDisabled()
    expect(screen.queryByTestId('plan-request-changes-toggle')).not.toBeInTheDocument()
    await userEvent.setup().click(screen.getByTestId('plan-approve-accept-edits'))
    expect(sent).not.toHaveBeenCalled()
  })

  it('reloads the conversation snapshot after handleRewindTurn succeeds', async () => {
    let rewound = false
    let snapshotLoads = 0
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.9"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.includes('/turns/turn-1/rewind') && method === 'POST') {
        rewound = true
        return new Response(JSON.stringify({ status: 'ok', restored: ['hello.txt'] }), { headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) {
        return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
        snapshotLoads += 1
        return new Response(
          JSON.stringify({
            ...snapshotBody({
              state: 'completed',
              messages: [
                { message_id: 'msg-1', role: 'user', content: 'Edite hello.txt', status: 'completed', retryable: false },
                { message_id: 'msg-2', role: 'assistant', content: rewound ? 'Alterações revertidas.' : 'Editei hello.txt.', status: 'completed', retryable: false },
              ],
              activities: [
                activity(1, 'tool.finished', 'Editou hello.txt', {
                  tool_name: 'write_file',
                  tool_kind: 'filesystem',
                  status: 'succeeded',
                  invocation_id: 'call-1',
                  path: 'hello.txt',
                }),
                activity(2, 'turn.completed', 'Concluído', { state: 'completed' }),
              ],
            }),
            mode: 'code',
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    renderChat()
    const user = userEvent.setup()
    expect(await screen.findByText('Editei hello.txt.')).toBeInTheDocument()
    const loadsBeforeRewind = snapshotLoads

    await user.click(screen.getByTestId('rewind-turn-button'))
    const confirmBtn = screen.queryByTestId('rewind-confirm-button')
    if (confirmBtn) {
      await user.click(confirmBtn)
    }

    await waitFor(() => expect(rewound).toBe(true))
    await waitFor(() => expect(snapshotLoads).toBeGreaterThan(loadsBeforeRewind))
    expect(await screen.findByText('Alterações revertidas.')).toBeInTheDocument()
  })

  it('exposes role="log" on chat thread, offers Copiar resposta on completed assistant messages, and deduplicates cancelled lifecycle events', async () => {
    globalThis.fetch = stubFetch(() => ({
      state: 'cancelled',
      messages: [
        { message_id: 'msg-1', role: 'user', content: 'Faça algo', status: 'completed', retryable: false },
        { message_id: 'msg-2', role: 'assistant', content: 'Resposta parcial útil.', status: 'completed', retryable: false },
      ],
      activities: [
        activity(1, 'assistant.delta', 'Resposta parcial útil.', { message_id: 'msg-2', content: 'Resposta parcial útil.' }),
        activity(2, 'execution.canceled', 'Execução cancelada', { state: 'cancelled' }),
        activity(3, 'turn.cancelled', 'Execução cancelada', { state: 'cancelled' }),
      ],
    }))

    renderChat()

    expect(await screen.findByRole('log')).toHaveAttribute('aria-live', 'polite')
    expect(await screen.findByRole('button', { name: 'Copiar resposta' })).toBeInTheDocument()
    expect(screen.getAllByText('Execução cancelada')).toHaveLength(1)
  })

  it('does not erase streamed assistant text when code_mode.validation_started arrives (P0-2) and renders shimmer on empty streaming bubble', async () => {
    let emitEvents: () => void = () => {}
    let closeStream: () => void = () => {}
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/events?')) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            closeStream = () => controller.close()
            emitEvents = () => {
              controller.enqueue(
                new TextEncoder().encode(
                  `id: a.10\nevent: assistant.delta\ndata: ${JSON.stringify(activity(10, 'assistant.delta', 'Explicação importante', { message_id: 'msg-1', content: 'Explicação importante antes da validação.' }))}\n\n` +
                    `id: a.11\nevent: code_mode.validation_started\ndata: ${JSON.stringify(activity(11, 'code_mode.validation_started', 'Executando validação', { stage: 'validating' }))}\n\n`,
                ),
              )
            }
          },
        })
        return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) {
        return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
        return new Response(
          JSON.stringify(
            snapshotBody({
              state: 'running',
              messages: [{ message_id: 'msg-1', role: 'assistant', content: '', status: 'streaming', retryable: false }],
              activities: [],
            }),
          ),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    renderChat()

    const shimmer = await screen.findByText('Pensando…')
    expect(shimmer).toHaveClass('agent-pulse__label--shimmer')

    emitEvents()

    await waitFor(() => {
      expect(screen.getByText('Explicação importante antes da validação.')).toBeInTheDocument()
    })
    closeStream()
  })

  it('renders CodePermissionDock in .chat__foot for pending toolPermission and lets Escape deny permission instead of cancelling the turn', async () => {
    const cancelled = vi.fn()
    const permissionPosts: Array<Record<string, unknown>> = []
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.9"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      if (url.includes('/cancel')) {
        cancelled()
        return new Response(JSON.stringify({ conversation_id: CONVERSATION_ID, cancelling: ['turn-1'] }), {
          status: 202,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.includes('/tool-permission') && method === 'POST') {
        const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
        permissionPosts.push(body)
        return new Response(
          JSON.stringify({ request_id: body.request_id, decision: body.decision, allowed: body.decision !== 'deny' }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) {
        return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
        return new Response(
          JSON.stringify({
            ...snapshotBody({
              state: 'running',
              messages: [{ message_id: 'msg-1', role: 'user', content: 'Rode o túnel', status: 'completed', retryable: false }],
              activities: [
                activity(1, 'code_mode.decision_required', 'Permissão necessária', {
                  stage: 'waiting_decision',
                  permission_request: {
                    request_id: 'perm_dock_1',
                    turn_id: 'turn-1',
                    tool_name: 'run_command',
                    command: 'npx vite --port 5173',
                    suggested_rule: 'run_command(npx vite:*)',
                  },
                }),
              ],
            }),
            mode: 'code',
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    renderChat()

    const dock = await screen.findByTestId('code-permission-dock')
    expect(dock).toBeInTheDocument()
    expect(screen.getByTestId('chat-composer')).toContainElement(dock)

    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => {
      expect(permissionPosts).toContainEqual(
        expect.objectContaining({ request_id: 'perm_dock_1', decision: 'deny' }),
      )
    })
    expect(cancelled).not.toHaveBeenCalled()
  })

  it('keeps chat scroll pinned to scrollHeight when new tool/thought events arrive even after reaching EVENT_LIMIT (1500)', async () => {
    let emitProgressEvent: () => void = () => {}
    let closeStream: () => void = () => {}

    const initialActivities = Array.from({ length: 1500 }, (_, idx) =>
      activity(idx + 1, 'agent.thought_delta', 'Pensando', {
        delta: `p${idx} `,
        detail: `p${idx} `,
      }),
    )

    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/events?')) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            closeStream = () => controller.close()
            emitProgressEvent = () => {
              controller.enqueue(
                new TextEncoder().encode(
                  `id: a.1501\nevent: tool.progressed\ndata: ${JSON.stringify(
                    activity(1501, 'tool.progressed', 'Preparando write_file...', {
                      phase: 'generating_arguments',
                      tool_name: 'write_file',
                      invocation_id: 'inv-scroll-1',
                    }),
                  )}\n\n`,
                ),
              )
            }
          },
        })
        return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) {
        return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
        return new Response(
          JSON.stringify({
            ...snapshotBody({
              state: 'running',
              messages: [
                {
                  message_id: 'msg-scroll-1',
                  role: 'assistant',
                  content: 'Motor pronto.',
                  status: 'streaming',
                  retryable: false,
                  turn_id: 'turn-1',
                },
              ],
              activities: initialActivities,
            }),
            mode: 'code',
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    })

    const { container } = renderChat()
    await screen.findByText('Motor pronto.')

    const scrollEl = container.querySelector('.chat__scroll') as HTMLDivElement
    expect(scrollEl).toBeTruthy()

    let currentScrollHeight = 2000
    Object.defineProperty(scrollEl, 'scrollHeight', {
      configurable: true,
      get: () => currentScrollHeight,
    })
    Object.defineProperty(scrollEl, 'clientHeight', {
      configurable: true,
      get: () => 500,
    })
    scrollEl.scrollTop = 2000

    // Simulate layout growth when the new tool.progressed card is added at event 1501
    currentScrollHeight = 2060
    emitProgressEvent()

    await waitFor(() => {
      expect(screen.getByTestId('code-tool-call-block')).toHaveTextContent('Preparando write_file')
    })

    expect(scrollEl.scrollTop).toBe(2060)
    closeStream()
  })

  it('pins scrollTop to scrollHeight when .chat__thread resizes asynchronously while pinned at bottom', async () => {
    type ObserverCallback = (entries: ResizeObserverEntry[], observer: ResizeObserver) => void
    let resizeCallback: ObserverCallback | null = null
    const OrigResizeObserver = globalThis.ResizeObserver
    class MockResizeObserver {
      constructor(cb: ObserverCallback) {
        resizeCallback = cb
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver

    try {
      globalThis.fetch = stubFetch(() => ({
        state: 'running',
        messages: [
          {
            message_id: 'msg-resize-1',
            role: 'assistant',
            content: 'Renderizando bloco...',
            status: 'streaming',
            retryable: false,
            turn_id: 'turn-1',
          },
        ],
        activities: [],
      }))

      const { container } = renderChat()
      await screen.findByText('Renderizando bloco...')

      const scrollEl = container.querySelector('.chat__scroll') as HTMLDivElement
      expect(scrollEl).toBeTruthy()
      expect(resizeCallback).not.toBeNull()

      let currentScrollHeight = 1200
      Object.defineProperty(scrollEl, 'scrollHeight', {
        configurable: true,
        get: () => currentScrollHeight,
      })
      Object.defineProperty(scrollEl, 'clientHeight', {
        configurable: true,
        get: () => 400,
      })
      scrollEl.scrollTop = 1200

      // Simulate an asynchronous height expansion inside .chat__thread (e.g. diff/code block/file preview render)
      currentScrollHeight = 1480
      resizeCallback!([], {} as ResizeObserver)

      expect(scrollEl.scrollTop).toBe(1480)
    } finally {
      globalThis.ResizeObserver = OrigResizeObserver
    }
  })
})

