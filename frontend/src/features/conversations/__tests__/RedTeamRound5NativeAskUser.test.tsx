import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../../../app/ThemeContext'
import { ChatPage } from '../ChatPage'

const CONVERSATION_ID = 'conv_redteam_round5'

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
    if (url.endsWith('/v1/uploads') && method === 'POST') {
      return new Response(JSON.stringify({ upload_id: 'up-1', filename: 'shot.png', media_type: 'image/png', kind: 'image', bytes: 4 }), {
        status: 201, headers: { 'Content-Type': 'application/json' },
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



const queuedTextOf = (body: string) => (JSON.parse(body) as { message: string }).message
const userBubbleTexts = () => Array.from(document.querySelectorAll('.bubble--user')).map((node) => node.textContent ?? '')

describe('Red team round 5: native ask_user in a live turn', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    localStorage.clear()
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
    if (!('createObjectURL' in URL)) Object.assign(URL, { createObjectURL: () => 'blob:x', revokeObjectURL: () => undefined })
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('an answer typed with an attachment while the in-turn question is open still reaches the waiting turn', async () => {
    const scenario: Scenario = {
      turnState: 'running',
      conversationState: 'running',
      activities: [
        makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1),
        makeActivity(2, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
      ],
    }
    const posted = installFetch(scenario)
    const { container } = { container: document.body }
    renderChat()
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'true'))
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(fileInput, { target: { files: [new File(['abcd'], 'shot.png', { type: 'image/png' })] } })
    await waitFor(() => expect(posted.some((p) => p.url.endsWith('/v1/uploads'))).toBe(true))
    await new Promise((resolve) => setTimeout(resolve, 100))
    const input = screen.getByRole('textbox', { name: 'Mensagem' }) as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: 'Postgres, veja o print' } })
    fireEvent.submit(input.closest('form') as HTMLFormElement)
    await new Promise((resolve) => setTimeout(resolve, 500))
    // POST /messages starts a new turn queued behind the native turn, which keeps waiting on the queue until its deadline.
    expect(messagePosts(posted)).toHaveLength(0)
  })

  it('an answer equal to an earlier message of the person still shows as a bubble', async () => {
    const scenario: Scenario = {
      turnState: 'running',
      conversationState: 'running',
      activities: [
        makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1),
        makeActivity(2, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
      ],
    }
    const posted = installFetch(scenario, (body) => {
      emit(scenario, queuedInputActivity(scenario.activities.length + 1, 'turn.started', queuedTextOf(body), 'q-dup'))
    })
    renderChat()
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'true'))
    const input = screen.getByRole('textbox', { name: 'Mensagem' }) as HTMLTextAreaElement
    // The conversation's first message (msg-1) is "Configure o banco"; the answer happens to be the same text.
    fireEvent.change(input, { target: { value: 'Configure o banco' } })
    fireEvent.submit(input.closest('form') as HTMLFormElement)
    await waitFor(() => expect(queuePosts(posted)).toHaveLength(1))
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'false'), { timeout: 3000 })
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(userBubbleTexts().filter((text) => text.includes('Configure o banco'))).toHaveLength(2)
  })

  it('a composer text sent before the turn starts waiting is not shown as the answer when the turn did not take it', async () => {
    // The card is live as soon as tool.finished(questions) arrives; the back-end only waits after the batch's hooks.
    // A text it claims before waiting (pre_claimed, not starting with the answers header) becomes a reminder, not
    // the answer, and the turn goes on waiting. The UI must not close the card on that delivery.
    const scenario: Scenario = {
      turnState: 'running',
      conversationState: 'running',
      activities: [makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1)],
    }
    // The delivery carries the queue id the enqueue receipt returned (the fake answers queue_id q-<posts so far>).
    const posted: Posted[] = installFetch(scenario, (body) => {
      emit(scenario, queuedInputActivity(scenario.activities.length + 1, 'turn.started', queuedTextOf(body), `q-${posted.length}`))
      emit(scenario, makeActivity(scenario.activities.length + 1, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }))
    })
    renderChat()
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'true'))
    const input = screen.getByRole('textbox', { name: 'Mensagem' }) as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: 'Postgres' } })
    fireEvent.submit(input.closest('form') as HTMLFormElement)
    await waitFor(() => expect(queuePosts(posted)).toHaveLength(1))
    await new Promise((resolve) => setTimeout(resolve, 1500))
    expect(cards()[0]).toHaveAttribute('data-active', 'true')
  })

  it('after a reload mid-wait the card is active and the composer queues', async () => {
    const scenario: Scenario = {
      turnState: 'running',
      conversationState: 'running',
      activities: [
        queuedInputActivity(1, 'turn.started', 'Lembrete antigo', 'q-old'),
        makeActivity(2, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1),
        makeActivity(3, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
      ],
    }
    const posted = installFetch(scenario)
    renderChat()
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'true'))
    const input = screen.getByRole('textbox', { name: 'Mensagem' }) as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: 'SQLite' } })
    fireEvent.submit(input.closest('form') as HTMLFormElement)
    await waitFor(() => expect(queuePosts(posted)).toHaveLength(1))
    expect(messagePosts(posted)).toHaveLength(0)
  })

  it('a cancelled turn closes the card and the composer sends normally again', async () => {
    const scenario: Scenario = {
      turnState: 'cancelled',
      conversationState: 'idle',
      activities: [
        makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1),
        makeActivity(2, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
        makeActivity(3, 'turn.cancelled', 'Cancelado'),
      ],
    }
    const posted = installFetch(scenario)
    renderChat()
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'false'))
    const input = screen.getByRole('textbox', { name: 'Mensagem' }) as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: 'outra coisa' } })
    fireEvent.submit(input.closest('form') as HTMLFormElement)
    await waitFor(() => expect(messagePosts(posted)).toHaveLength(1))
    expect(queuePosts(posted)).toHaveLength(0)
  })
})
