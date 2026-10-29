import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../ChatPage'

type Snapshot = {
  state: string
  mode?: 'chat' | 'code'
  messages: Array<Record<string, unknown>>
  activities: Array<Record<string, unknown>>
}

const CONVERSATION_ID = 'chat_ref03'

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
    agent_id: 'agent:chat_ref03:main',
    parent_agent_id: null,
    cursor: `a.${sequence}`,
  }
}

function snapshotBody(snapshot: Snapshot) {
  return {
    conversation_id: CONVERSATION_ID,
    title: 'Estudo de Precificação',
    state: snapshot.state,
    mode: snapshot.mode ?? 'chat',
    provider: 'openrouter',
    model_id: 'model-a',
    messages: snapshot.messages,
    turns: [
      {
        turn_id: 'turn-1',
        state: snapshot.state,
        created_at: '2026-08-10T20:00:00+00:00',
        started_at: '2026-08-10T20:00:01+00:00',
        finished_at: snapshot.state === 'running' ? null : '2026-08-10T20:01:00+00:00',
      },
    ],
    activities: snapshot.activities,
    activity_cursor: 'a.9',
    workspace: { kind: 'managed', path: null, folder_name: null, scope: 'chat', project_name: null },
  }
}

function stubFetch(
  current: () => Snapshot,
  options: {
    queueItems?: Array<{ queue_id: string; content: string; created_at: string | null }>
    onCancel?: () => void
    onRemoveQueued?: (queueId: string) => void
  } = {},
) {
  let queueItems = [...(options.queueItems ?? [])]

  return vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : String(input)
    const method = (init?.method ?? 'GET').toUpperCase()

    if (url.includes('/events?')) {
      return new Response('event: heartbeat\ndata: {"cursor":"a.9"}\n\n', {
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    if (url.includes('/cancel') && method === 'POST') {
      options.onCancel?.()
      return new Response(
        JSON.stringify({ conversation_id: CONVERSATION_ID, cancelling: ['turn-1'] }),
        { status: 202, headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (url.includes(`/v1/conversations/${CONVERSATION_ID}/queue/`) && method === 'DELETE') {
      const queueId = decodeURIComponent(url.split('/queue/')[1] ?? '')
      options.onRemoveQueued?.(queueId)
      queueItems = queueItems.filter((item) => item.queue_id !== queueId)
      return new Response(JSON.stringify({ cancelled: true }), {
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith(`/v1/conversations/${CONVERSATION_ID}/queue`) && method === 'GET') {
      return new Response(JSON.stringify({ items: queueItems }), {
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith('/v1/projects/sidebar')) {
      return new Response(
        JSON.stringify({
          items: [{ project_id: 'project-e2e', name: 'Projeto de teste', description: null, chats: [] }],
        }),
        { headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (url.endsWith('/v1/conversations')) {
      return new Response(
        JSON.stringify({
          items: [{ conversation_id: CONVERSATION_ID, title: 'Estudo de Precificação', state: current().state }],
        }),
        { headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
      return new Response(JSON.stringify(snapshotBody(current())), {
        headers: { 'Content-Type': 'application/json' },
      })
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
        <Route path="/projects/:projectId/chats/:conversationId" element={<ChatPage />} />
      </Routes>
      <CurrentPath />
    </MemoryRouter>,
  )
}

describe('ChatPage ref-03 and TeamActivityView (ref-10) integration', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('renders ref-03 header controls ("Abrir no Code", "Equipe em atividade", "Conversar por voz", OrinMascot) and toggles TeamActivityView', async () => {
    const user = userEvent.setup()
    globalThis.fetch = stubFetch(() => ({
      state: 'completed',
      mode: 'chat',
      messages: [
        { message_id: 'msg-1', role: 'user', content: 'Monte um estudo de precificação', status: 'completed', retryable: false },
        { message_id: 'msg-2', role: 'assistant', content: 'Aqui está a análise inicial.', status: 'completed', retryable: false },
      ],
      activities: [
        activity(1, 'turn.completed', 'Resposta concluída', { state: 'completed' }),
      ],
    }))

    const { container } = renderChat()

    expect(await screen.findByText('Monte um estudo de precificação')).toBeInTheDocument()

    const header = container.querySelector('.canvas-header') as HTMLElement
    expect(header).toBeTruthy()

    const openInCodeBtn = within(header).getByTestId('chat-open-in-code-button')
    expect(openInCodeBtn).toHaveTextContent('Abrir no Code')

    const voiceBtn = within(header).getByTestId('chat-voice-button')
    expect(voiceBtn).toHaveTextContent('Conversar por voz')

    const headerMascot = within(header).getByTestId('orin-mascot')
    expect(headerMascot).toBeInTheDocument()
    expect(within(header).getByTestId('orin-mascot-status-dot')).toBeInTheDocument()

    const teamToggleBtn = within(header).getByTestId('chat-team-view-toggle')
    expect(teamToggleBtn).toHaveTextContent('Equipe em atividade')

    // Toggle to TeamActivityView
    await user.click(teamToggleBtn)
    expect(await screen.findByTestId('team-activity-view')).toBeInTheDocument()

    // Return to conversation via "Voltar à conversa" button inside TeamActivityView
    await user.click(screen.getByTestId('team-back-to-chat-button'))
    expect(screen.queryByTestId('team-activity-view')).not.toBeInTheDocument()
    expect(screen.getByText('Monte um estudo de precificação')).toBeInTheDocument()

    // Toggle again via header button and return via header toggle button
    await user.click(teamToggleBtn)
    expect(await screen.findByTestId('team-activity-view')).toBeInTheDocument()
    await user.click(teamToggleBtn)
    expect(screen.queryByTestId('team-activity-view')).not.toBeInTheDocument()
  })

  it('renders user circular avatar, assistant OrinMascot, and Live Work Progress Card with "Ver detalhes ›" and "Parar" when running', async () => {
    const user = userEvent.setup()
    let cancelCalled = false

    globalThis.fetch = stubFetch(
      () => ({
        state: 'running',
        mode: 'chat',
        messages: [
          { message_id: 'msg-1', role: 'user', content: 'Pesquise referências de mercado', status: 'completed', retryable: false },
          { message_id: 'msg-2', role: 'assistant', content: 'Iniciando pesquisa de benchmarks.', status: 'streaming', retryable: false },
        ],
        activities: [
          activity(1, 'tool.started', 'Comparando referências', {
            tool_name: 'web_search',
            tool_kind: 'web',
            status: 'running',
            invocation_id: 'call-1',
            label: 'Buscando benchmarks e casos de mercado…',
          }),
        ],
      }),
      {
        onCancel: () => {
          cancelCalled = true
        },
      },
    )

    const { container } = renderChat()

    expect(await screen.findByText('Pesquise referências de mercado')).toBeInTheDocument()

    const userBubble = container.querySelector('.bubble--user') as HTMLElement
    expect(userBubble).toBeTruthy()
    expect(within(userBubble).getByTestId('user-message-avatar')).toBeInTheDocument()

    const assistantBubble = container.querySelector('.bubble--assistant') as HTMLElement
    expect(assistantBubble).toBeTruthy()
    expect(within(assistantBubble).getByTestId('assistant-turn-mascot')).toBeInTheDocument()

    const liveCard = await screen.findByTestId('live-work-progress-card')
    expect(liveCard).toBeInTheDocument()

    const viewDetailsBtn = within(liveCard).getByTestId('live-work-view-details')
    expect(viewDetailsBtn).toHaveTextContent('Ver detalhes')
    await user.click(viewDetailsBtn)

    expect(await screen.findByTestId('team-activity-view')).toBeInTheDocument()
    await user.click(screen.getByTestId('team-back-to-chat-button'))

    const liveCardAfterReturn = await screen.findByTestId('live-work-progress-card')
    const stopBtn = within(liveCardAfterReturn).getByTestId('live-work-stop-button')
    expect(stopBtn).toHaveTextContent('Parar')
    await user.click(stopBtn)
    expect(cancelCalled).toBe(true)
  })

  it('removes a queued item and populates the Composer when clicking Edit in QueuePanel', async () => {
    const user = userEvent.setup()
    const removedIds: string[] = []

    globalThis.fetch = stubFetch(
      () => ({
        state: 'running',
        mode: 'chat',
        messages: [
          { message_id: 'msg-1', role: 'user', content: 'Primeira pergunta', status: 'completed', retryable: false },
        ],
        activities: [
          activity(1, 'turn.started', 'Executando turno', { state: 'running' }),
        ],
      }),
      {
        queueItems: [
          {
            queue_id: 'q-edit-1',
            content: 'Incluir também empresas da América Latina',
            created_at: '2026-08-10T20:00:05+00:00',
          },
        ],
        onRemoveQueued: (queueId) => {
          removedIds.push(queueId)
        },
      },
    )

    renderChat()

    const editBtn = await screen.findByTestId('queue-edit-button-q-edit-1')
    expect(screen.getByText('Incluir também empresas da América Latina')).toBeInTheDocument()

    await user.click(editBtn)

    await waitFor(() => {
      expect(removedIds).toContain('q-edit-1')
    })

    const composerInput = screen.getByRole('textbox', { name: 'Mensagem' }) as HTMLTextAreaElement
    expect(composerInput.value).toBe('Incluir também empresas da América Latina')
    expect(screen.queryByTestId('queue-edit-button-q-edit-1')).not.toBeInTheDocument()
  })
})
