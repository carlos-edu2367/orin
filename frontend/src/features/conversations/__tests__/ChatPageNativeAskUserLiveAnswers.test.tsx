import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../../../app/ThemeContext'
import { ChatPage } from '../ChatPage'

const CONVERSATION_ID = 'conv_native_live_answers'

function makeActivity(
  sequence: number,
  type: string,
  summary: string,
  payload: Record<string, unknown> = {},
) {
  return {
    event_id: `ev:${sequence}`,
    event_type: type,
    sequence,
    summary,
    payload,
    occurred_at: '2026-11-05T16:30:00.000Z',
    turn_id: 'turn-1',
    execution_id: 'exe-1',
    agent_id: 'agent:main',
    parent_agent_id: null,
    cursor: `a.${sequence}`,
  }
}

function questionPayload(id: string, question: string, options: Array<{ id: string; label: string }>) {
  return {
    tool_name: 'ask_user',
    tool_kind: 'user_input',
    status: 'succeeded',
    wait_for_user: true,
    questions: [{ id, question, header: 'Escolha', mode: 'single_choice', options }],
  }
}

const Q1 = questionPayload('q1', 'Qual banco de dados usar?', [
  { id: 'sqlite', label: 'SQLite' },
  { id: 'postgres', label: 'Postgres' },
])
const Q2 = questionPayload('q2', 'Qual cache usar?', [
  { id: 'redis', label: 'Redis' },
  { id: 'memcached', label: 'Memcached' },
])

/** How the back-end tells the turn read a queued input (the answer). */
type QueuedInputShape = 'turn.queued_input' | 'turn.started'

function queuedInputActivity(sequence: number, shape: QueuedInputShape, text: string, queueId: string) {
  return makeActivity(sequence, shape, 'Mensagem da fila entregue ao agente', { queue_id: queueId, queued_text: text })
}

type Activity = ReturnType<typeof makeActivity>

type Scenario = {
  turnState: string
  conversationState: string
  activities: Activity[]
  /** Activities not yet streamed: the next events request delivers them, as the live SSE stream would. */
  live?: Activity[]
}

/** A new activity of the running turn: it reaches the page through the event stream (and later snapshots). */
function emit(scenario: Scenario, activity: Activity) {
  scenario.activities = [...scenario.activities, activity]
  scenario.live = [...(scenario.live ?? []), activity]
}

function snapshot(scenario: Scenario) {
  return {
    conversation_id: CONVERSATION_ID,
    title: 'Pergunta nativa',
    state: scenario.conversationState,
    mode: 'code',
    permission_mode: 'accept_edits',
    provider: 'anthropic',
    model_id: 'claude-opus-5-5',
    messages: [
      { message_id: 'msg-1', role: 'user', content: 'Configure o banco', status: 'completed', retryable: false, turn_id: 'turn-1' },
      {
        message_id: 'msg-2',
        role: 'assistant',
        content: 'Antes de continuar, preciso confirmar o banco.',
        status: scenario.turnState === 'running' ? 'streaming' : 'completed',
        retryable: false,
        turn_id: 'turn-1',
      },
    ],
    turns: [
      {
        turn_id: 'turn-1',
        state: scenario.turnState,
        code_mode: 'code',
        created_at: '2026-11-05T16:30:00.000Z',
        started_at: '2026-11-05T16:30:00.000Z',
        finished_at: null,
      },
    ],
    activities: scenario.activities,
    activity_cursor: scenario.activities.at(-1)?.cursor ?? 'a.0',
    workspace: { kind: 'local', path: 'C:/Projetos/demo', folder_name: 'demo', scope: 'project', project_name: 'Demo' },
  }
}

type Posted = { url: string; body: string }

/**
 * A fetch double whose snapshot the test changes as the turn moves on. The page reloads the snapshot every
 * time the (one-shot) event stream closes, so a new activity reaches it within a reconnect cycle.
 */
function installFetch(scenario: Scenario, onQueue: (body: string) => void = () => undefined) {
  const posted: Posted[] = []
  globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = typeof init?.body === 'string' ? init.body : ''
    if (method === 'POST') posted.push({ url, body })
    if (url.includes('/events?')) {
      const live = scenario.live ?? []
      scenario.live = []
      const records = live.map((activity) => `id: ${activity.cursor}\ndata: ${JSON.stringify(activity)}\n\n`).join('')
      return new Response(`${records}event: heartbeat\ndata: {"cursor":"${scenario.activities.at(-1)?.cursor ?? 'a.0'}"}\n\n`, {
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    if (url.endsWith(`/v1/conversations/${CONVERSATION_ID}/queue`) && method === 'POST') {
      onQueue(body)
      return new Response(JSON.stringify({ queued: true, queue_id: `q-${posted.length}`, turn_id: 'turn-1' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith(`/v1/conversations/${CONVERSATION_ID}/messages`) && method === 'POST') {
      return new Response(
        JSON.stringify({
          conversation_id: CONVERSATION_ID,
          title: 'Pergunta nativa',
          turn_id: 'turn-2',
          message_id: 'msg-3',
          state: 'running',
          mode: 'code',
          permission_mode: 'accept_edits',
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (url.includes(`/v1/conversations/${CONVERSATION_ID}`) && !url.includes('/queue') && !url.includes('/events')) {
      return new Response(JSON.stringify(snapshot(scenario)), { headers: { 'Content-Type': 'application/json' } })
    }
    return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } })
  })
  return posted
}

function renderChat() {
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/chats/${CONVERSATION_ID}`]}>
        <Routes>
          <Route path="/chats/:conversationId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  )
}

const queuePosts = (posted: Posted[]) => posted.filter((item) => item.url.endsWith(`/v1/conversations/${CONVERSATION_ID}/queue`))
const messagePosts = (posted: Posted[]) => posted.filter((item) => item.url.endsWith(`/v1/conversations/${CONVERSATION_ID}/messages`))
const userBubbles = () => screen.queryAllByTestId('user-message-avatar')
const userBubbleTexts = () => Array.from(document.querySelectorAll('.bubble--user')).map((node) => node.textContent ?? '')
/** The text the page enqueued: the back-end reports exactly this as the delivered queued_text. */
const queuedText = (body: string) => (JSON.parse(body) as { message: string }).message
const cards = () => screen.queryAllByTestId('user-question-card')

describe('ChatPage native ask_user: answers inside a live turn', () => {
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

  it.each<QueuedInputShape>(['turn.queued_input', 'turn.started'])(
    'answers without a duplicate user bubble, closes the answered card on %s, then answers a second ask_user of the same turn',
    async (shape) => {
      const scenario: Scenario = {
        turnState: 'running',
        conversationState: 'running',
        activities: [
          makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1),
          makeActivity(2, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
        ],
      }
      let queueCalls = 0
      const posted = installFetch(scenario, (body) => {
        queueCalls += 1
        // The turn reads the answer and the back-end reports it (the answer's own bubble comes from this event).
        emit(scenario, queuedInputActivity(scenario.activities.length + 1, shape, queuedText(body), `q-${queueCalls}`))
      })
      renderChat()

      fireEvent.click(await screen.findByLabelText(/SQLite/))
      expect(userBubbles()).toHaveLength(1)
      fireEvent.click(screen.getByRole('button', { name: 'Enviar respostas' }))

      await waitFor(() => expect(queuePosts(posted)).toHaveLength(1))
      expect(queuePosts(posted)[0].body).toContain('SQLite')
      expect(messagePosts(posted)).toHaveLength(0)
      await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'false'))
      // No optimistic pending bubble on the live path: the answer shows once, from the delivered queued input.
      await waitFor(() => expect(userBubbles()).toHaveLength(2), { timeout: 3000 })
      expect(userBubbleTexts().filter((text) => text.includes('Seleção: SQLite'))).toHaveLength(1)

      // A second ask_user arrives later in the same live turn, with a new event id.
      emit(scenario, makeActivity(scenario.activities.length + 1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q2))
      emit(scenario, makeActivity(scenario.activities.length + 1, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }))
      await waitFor(() => expect(cards()).toHaveLength(2), { timeout: 3000 })
      await waitFor(() => expect(cards()[1]).toHaveAttribute('data-active', 'true'))
      expect(cards()[0]).toHaveAttribute('data-active', 'false')

      fireEvent.click(screen.getByLabelText(/Redis/))
      fireEvent.click(screen.getByRole('button', { name: 'Enviar respostas' }))

      await waitFor(() => expect(queuePosts(posted)).toHaveLength(2))
      expect(queuePosts(posted)[1].body).toContain('Redis')
      expect(messagePosts(posted)).toHaveLength(0)
      await waitFor(() => expect(cards()[1]).toHaveAttribute('data-active', 'false'))
      expect(cards()[0]).toHaveAttribute('data-active', 'false')
      await waitFor(() => expect(userBubbles()).toHaveLength(3), { timeout: 3000 })
      expect(userBubbleTexts().filter((text) => text.includes('Seleção: SQLite'))).toHaveLength(1)
      expect(userBubbleTexts().filter((text) => text.includes('Seleção: Redis'))).toHaveLength(1)
    },
  )

  it('ignores a second submit of a card already answered while turn.queued_input has not arrived yet', async () => {
    const scenario: Scenario = {
      turnState: 'running',
      conversationState: 'running',
      activities: [
        makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1),
        makeActivity(2, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
      ],
    }
    const posted = installFetch(scenario)
    renderChat()

    fireEvent.click(await screen.findByLabelText(/SQLite/))
    const submit = screen.getByRole('button', { name: 'Enviar respostas' })
    fireEvent.click(submit)
    fireEvent.click(submit)

    await waitFor(() => expect(queuePosts(posted)).toHaveLength(1))
    // Answered locally: the card closes even before the back-end reports the queued input.
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'false'))
    expect(screen.queryByRole('button', { name: 'Enviar respostas' })).toBeNull()
    await new Promise((resolve) => setTimeout(resolve, 600))
    expect(queuePosts(posted)).toHaveLength(1)
    expect(cards()[0]).toHaveAttribute('data-active', 'false')
  })

  it('after a reload, an answered question stays closed and its answer shows once; a normal queued message does not close it', async () => {
    const answer = 'Respostas às perguntas do agente:\n\nQual banco de dados usar?\nSeleção: Postgres'
    const scenario: Scenario = {
      turnState: 'running',
      conversationState: 'running',
      activities: [
        makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1),
        // A normal message queued before the question, delivered before the turn waits: not the answer.
        queuedInputActivity(2, 'turn.started', 'Lembrete: use Python 3.12', 'q-pre'),
        makeActivity(3, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
        queuedInputActivity(4, 'turn.started', answer, 'q-answer'),
        makeActivity(5, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q2),
        makeActivity(6, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
      ],
    }
    installFetch(scenario)
    renderChat()

    await waitFor(() => expect(cards()).toHaveLength(2))
    expect(cards()[0]).toHaveAttribute('data-active', 'false')
    expect(cards()[1]).toHaveAttribute('data-active', 'true')
    expect(userBubbles()).toHaveLength(2)
    expect(userBubbleTexts().filter((text) => text.includes('Seleção: Postgres'))).toHaveLength(1)
    // The normal queued message renders as before: no bubble of its own.
    expect(userBubbleTexts().some((text) => text.includes('Lembrete'))).toBe(false)
  })

  it('an answer typed in the composer goes to the queue and closes the open question once the turn reads it', async () => {
    const scenario: Scenario = {
      turnState: 'running',
      conversationState: 'running',
      activities: [
        makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1),
        makeActivity(2, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
      ],
    }
    const posted = installFetch(scenario, (body) => {
      emit(scenario, queuedInputActivity(scenario.activities.length + 1, 'turn.started', queuedText(body), 'q-composer'))
    })
    renderChat()

    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'true'))
    const input = screen.getByRole('textbox', { name: 'Mensagem' }) as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: 'Use Postgres, por favor' } })
    fireEvent.submit(input.closest('form') as HTMLFormElement)

    await waitFor(() => expect(queuePosts(posted)).toHaveLength(1))
    expect(queuedText(queuePosts(posted)[0].body)).toBe('Use Postgres, por favor')
    expect(messagePosts(posted)).toHaveLength(0)
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'false'), { timeout: 3000 })
    await waitFor(() => expect(userBubbleTexts().filter((text) => text.includes('Use Postgres, por favor'))).toHaveLength(1))
    expect(userBubbles()).toHaveLength(2)
  })

  it('keeps the classic waiting_user flow: a new message and the optimistic user bubble', async () => {
    const scenario: Scenario = {
      turnState: 'waiting_user',
      conversationState: 'waiting_user',
      activities: [
        makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1),
        makeActivity(2, 'turn.waiting_user', 'Aguardando sua resposta'),
      ],
    }
    const posted = installFetch(scenario)
    renderChat()

    fireEvent.click(await screen.findByLabelText(/SQLite/))
    expect(cards()[0]).toHaveAttribute('data-active', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Enviar respostas' }))

    await waitFor(() => expect(messagePosts(posted)).toHaveLength(1))
    expect(messagePosts(posted)[0].body).toContain('SQLite')
    expect(queuePosts(posted)).toHaveLength(0)
    expect(userBubbles()).toHaveLength(2)
  })
})
