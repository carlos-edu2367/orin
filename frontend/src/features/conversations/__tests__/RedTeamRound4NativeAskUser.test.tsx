import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../../../app/ThemeContext'
import { ChatPage } from '../ChatPage'

const CONVERSATION_ID = 'conv_redteam_round4'

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
function installFetch(scenario: Scenario, onQueue: (body: string) => void = () => undefined, queueStatus = 200) {
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
      if (queueStatus !== 200) return new Response(JSON.stringify({ detail: 'boom' }), { status: queueStatus, headers: { 'Content-Type': 'application/json' } })
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
const cards = () => screen.queryAllByTestId('user-question-card')


describe('Red team round 4: native ask_user in a live turn', () => {
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

  it('the answer the person gave stays visible somewhere after a live answer (spec: "a resposta aparece uma vez só")', async () => {
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
      const text = (JSON.parse(body) as { message?: string }).message ?? body
      emit(scenario, queuedInputActivity(scenario.activities.length + 1, 'turn.started', text, `q-${queueCalls}`))
    })
    renderChat()

    fireEvent.click(await screen.findByLabelText(/Postgres/))
    fireEvent.click(screen.getByRole('button', { name: 'Enviar respostas' }))
    await waitFor(() => expect(queuePosts(posted)).toHaveLength(1))
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'false'))
    await new Promise((resolve) => setTimeout(resolve, 400))
    // Nothing on the page says "Postgres" any more: no bubble, and the closed card shows no answers.
    expect(screen.queryAllByText(/Postgres/).length).toBeGreaterThan(0)
  })

  it('a normal message queued earlier and delivered right after the question does not close the still-open card', async () => {
    // Back-end order in _execute_batch: tool.finished(questions) -> pre_claimed queued_input (turn.started+queue_id)
    // -> waiting_user_in_turn. The queued text was a normal message, not the answer: the turn is still waiting.
    const scenario: Scenario = {
      turnState: 'running',
      conversationState: 'running',
      activities: [
        makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1),
        queuedInputActivity(2, 'turn.started', 'Lembrete: use Python 3.12', 'q-pre'),
        makeActivity(3, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
      ],
    }
    installFetch(scenario)
    renderChat()
    const card = await screen.findByTestId('user-question-card')
    await new Promise((resolve) => setTimeout(resolve, 200))
    expect(card).toHaveAttribute('data-active', 'true')
  })

  it('typing the answer in the composer while an in-turn question is open goes to the running turn (queue), not to a new turn', async () => {
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
    await screen.findByTestId('user-question-card')
    const input = screen.getByRole('textbox', { name: 'Mensagem' }) as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: 'Use Postgres' } })
    fireEvent.submit(input.closest('form') as HTMLFormElement)
    await new Promise((resolve) => setTimeout(resolve, 400))
    // The native turn only reads the queue: a POST /messages starts another turn behind the one waiting forever.
    expect(messagePosts(posted)).toHaveLength(0)
    expect(queuePosts(posted)).toHaveLength(1)
  })

  it('a failed enqueue re-opens the answered card and shows the error', async () => {
    const scenario: Scenario = {
      turnState: 'running',
      conversationState: 'running',
      activities: [
        makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1),
        makeActivity(2, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
      ],
    }
    const posted = installFetch(scenario, () => undefined, 500)
    renderChat()
    fireEvent.click(await screen.findByLabelText(/SQLite/))
    fireEvent.click(screen.getByRole('button', { name: 'Enviar respostas' }))
    await waitFor(() => expect(posted.some((p) => p.url.endsWith('/queue'))).toBe(true))
    await waitFor(() => expect(screen.getAllByText('Não foi possível enviar as respostas. Tente novamente.').length).toBeGreaterThan(0))
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'true'))
    expect(messagePosts(posted)).toHaveLength(0)
    expect(userBubbles()).toHaveLength(1)
  })
})
