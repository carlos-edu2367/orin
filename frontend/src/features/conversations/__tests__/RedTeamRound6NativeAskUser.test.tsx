import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../../../app/ThemeContext'
import { ChatPage } from '../ChatPage'

const CONVERSATION_ID = 'conv_redteam_round6'

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

type Activity = ReturnType<typeof makeActivity>

type Scenario = {
  turnState: string
  conversationState: string
  activities: Activity[]
  /** Activities not yet streamed: the next events request delivers them, as the live SSE stream would. */
  live?: Activity[]
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
    if (url.endsWith(`/v1/conversations/${CONVERSATION_ID}/shell`) && method === 'POST') {
      return new Response(JSON.stringify({ turn_id: 'turn-shell' }), { status: 201, headers: { 'Content-Type': 'application/json' } })
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




const shellPosts = (posted: Posted[]) => posted.filter((item) => item.url.endsWith(`/v1/conversations/${CONVERSATION_ID}/shell`))

function waitingScenario(): Scenario {
  return {
    turnState: 'running',
    conversationState: 'running',
    activities: [
      makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', Q1),
      makeActivity(2, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
    ],
  }
}

async function attach(posted: Posted[]) {
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, { target: { files: [new File(['abcd'], 'shot.png', { type: 'image/png' })] } })
  await waitFor(() => expect(posted.some((p) => p.url.endsWith('/v1/uploads'))).toBe(true))
  await new Promise((resolve) => setTimeout(resolve, 100))
}

function send(text: string) {
  const input = screen.getByRole('textbox', { name: 'Mensagem' }) as HTMLTextAreaElement
  fireEvent.change(input, { target: { value: text } })
  fireEvent.submit(input.closest('form') as HTMLFormElement)
  return input
}

describe('Red team round 6: composer while a live turn waits for an answer', () => {
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

  it('text with a file: the text goes to the queue, nothing posts /messages, the file stays', async () => {
    const posted = installFetch(waitingScenario())
    renderChat()
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'true'))
    await attach(posted)
    send('Postgres, veja o print')
    await waitFor(() => expect(queuePosts(posted)).toHaveLength(1))
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(messagePosts(posted)).toHaveLength(0)
    expect(screen.getAllByText(/shot\.png/).length).toBeGreaterThan(0)
  })

  it('a file alone is blocked with a notice and nothing is posted', async () => {
    const posted = installFetch(waitingScenario())
    renderChat()
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'true'))
    await attach(posted)
    const input = screen.getByRole('textbox', { name: 'Mensagem' }) as HTMLTextAreaElement
    fireEvent.submit(input.closest('form') as HTMLFormElement)
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(messagePosts(posted)).toHaveLength(0)
    expect(queuePosts(posted)).toHaveLength(0)
    expect(userBubbles()).toHaveLength(1)
  })

  it('an oversized text is blocked and stays in the composer', async () => {
    const posted = installFetch(waitingScenario())
    renderChat()
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'true'))
    const big = 'x'.repeat(200_000)
    const input = send(big)
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(messagePosts(posted)).toHaveLength(0)
    expect(queuePosts(posted)).toHaveLength(0)
    expect(posted.some((p) => p.url.endsWith('/v1/uploads'))).toBe(false)
    expect(input.value.length).toBe(200_000)
    expect(userBubbles()).toHaveLength(1)
  })

  it('a !command typed while the turn waits does not start a shell turn queued behind it', async () => {
    // POST /shell is a new turn (ChatApplication.shell -> send); it waits behind the native turn, which waits for an answer.
    const posted = installFetch(waitingScenario())
    renderChat()
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'true'))
    send('!git status')
    await new Promise((resolve) => setTimeout(resolve, 400))
    expect(shellPosts(posted)).toHaveLength(0)
    expect(userBubbles()).toHaveLength(1)
  })
})
