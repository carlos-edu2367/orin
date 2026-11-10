import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../../../app/ThemeContext'
import { ChatPage } from '../ChatPage'

/**
 * Regression (chat_6111d731, 10/11/2026): a read-only subagent's exit_plan_mode made the page think the leader turn
 * waited for a plan decision, so a message typed then became a new queued turn behind the live one. The leader then
 * asked with ask_user; the answer went to that last (queued) turn's path and never reached the queue the asking turn
 * reads, so the turn waited until the MCP tool timeout and the answer was lost.
 */
const CONVERSATION_ID = 'conv_ask_user_behind_queued_turn'

type Activity = {
  event_id: string
  event_type: string
  sequence: number
  summary: string
  payload: Record<string, unknown>
  occurred_at: string
  turn_id: string
  execution_id: string
  agent_id: string
  parent_agent_id: string | null
  cursor: string
}

function activity(sequence: number, type: string, summary: string, payload: Record<string, unknown> = {}, sub = false): Activity {
  return {
    event_id: `ev:${sequence}`,
    event_type: type,
    sequence,
    summary,
    payload,
    occurred_at: '2026-11-10T18:11:00.000Z',
    turn_id: 'turn-1',
    execution_id: 'exe-1',
    agent_id: sub ? 'agent:sub-explore' : 'agent:main',
    parent_agent_id: sub ? 'agent:main' : null,
    cursor: `a.${sequence}`,
  }
}

const QUESTION = {
  tool_name: 'ask_user',
  tool_kind: 'user_input',
  status: 'succeeded',
  wait_for_user: true,
  questions: [{
    id: 'q1', question: 'Aprova o plano?', header: 'Ordem', mode: 'single_choice',
    options: [{ id: 'o1', label: 'Bugs primeiro' }, { id: 'o2', label: 'Só os bugs' }],
  }],
}

/** What a read-only explore subagent's exit_plan_mode reports (it goes to the leader, not to the person). */
const SUBAGENT_PLAN = {
  agent_name: 'explore-2', ok: true, tool_kind: 'planning', plan_mode: false, plan_ready: true, plan_approval: true,
  permission_mode: 'accept_edits', plan_path: '', plan_summary: 'Diagnóstico pronto, somente leitura.', plan_content: '',
  tool_name: 'exit_plan_mode', status: 'succeeded',
}

type Scenario = { activities: Activity[]; queuedTurnBehind: boolean }

function snapshot(scenario: Scenario) {
  const turns = [{ turn_id: 'turn-1', state: 'running', code_mode: 'code', created_at: '2026-11-10T18:08:14.000Z', started_at: '2026-11-10T18:08:15.000Z', finished_at: null }]
  const messages = [
    { message_id: 'msg-1', role: 'user', content: 'vamos analisar e planejar', status: 'completed', retryable: false, turn_id: 'turn-1' },
    { message_id: 'msg-2', role: 'assistant', content: 'Analisei o código.', status: 'streaming', retryable: false, turn_id: 'turn-1' },
  ]
  if (scenario.queuedTurnBehind) {
    turns.push({ turn_id: 'turn-2', state: 'queued', code_mode: 'code', created_at: '2026-11-10T18:13:06.000Z', started_at: null as unknown as string, finished_at: null })
    messages.push(
      { message_id: 'msg-3', role: 'user', content: 'outro detalhe pra procurarmos', status: 'completed', retryable: false, turn_id: 'turn-2' },
      { message_id: 'msg-4', role: 'assistant', content: '', status: 'queued', retryable: false, turn_id: 'turn-2' },
    )
  }
  return {
    conversation_id: CONVERSATION_ID,
    title: 'Planejar',
    state: 'running',
    mode: 'code',
    permission_mode: 'full_autonomy',
    provider: 'anthropic',
    model_id: 'claude-opus-5-5',
    messages,
    turns,
    activities: scenario.activities,
    activity_cursor: scenario.activities.at(-1)?.cursor ?? 'a.0',
    workspace: { kind: 'local', path: 'C:/Projetos/demo', folder_name: 'demo', scope: 'project', project_name: 'Demo' },
  }
}

type Posted = { url: string; body: string }

function installFetch(scenario: Scenario) {
  const posted: Posted[] = []
  globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = typeof init?.body === 'string' ? init.body : ''
    if (method === 'POST') posted.push({ url, body })
    if (url.includes('/events?')) {
      return new Response(`event: heartbeat\ndata: {"cursor":"${scenario.activities.at(-1)?.cursor ?? 'a.0'}"}\n\n`, {
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    if (url.endsWith(`/v1/conversations/${CONVERSATION_ID}/queue`) && method === 'POST') {
      return new Response(JSON.stringify({ queued: true, queue_id: `q-${posted.length}`, turn_id: 'turn-1' }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith(`/v1/conversations/${CONVERSATION_ID}/messages`) && method === 'POST') {
      return new Response(JSON.stringify({
        conversation_id: CONVERSATION_ID, title: 'Planejar', turn_id: 'turn-3', message_id: 'msg-5', state: 'queued', mode: 'code', permission_mode: 'full_autonomy',
      }), { status: 201, headers: { 'Content-Type': 'application/json' } })
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
const queuedText = (body: string) => (JSON.parse(body) as { message: string }).message
const cards = () => screen.queryAllByTestId('user-question-card')
const composer = () => screen.getByRole('textbox', { name: 'Mensagem' }) as HTMLTextAreaElement

describe('ChatPage: a live turn asking behind a queued turn, and a subagent plan', () => {
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

  it("a subagent's exit_plan_mode keeps the leader turn running: a typed message goes to the queue, not to a new turn", async () => {
    const scenario: Scenario = {
      queuedTurnBehind: false,
      activities: [
        activity(1, 'tool.finished', 'Plano pronto', SUBAGENT_PLAN, true),
        activity(2, 'tool.finished', 'Recebeu a resposta de explore-2', { tool_name: 'check_subagent', status: 'succeeded' }),
      ],
    }
    const posted = installFetch(scenario)
    renderChat()

    await waitFor(() => expect(document.querySelector('form.composer')).toHaveClass('is-running'))
    fireEvent.change(composer(), { target: { value: 'outro detalhe pra procurarmos' } })
    fireEvent.keyDown(composer(), { key: 'Enter', code: 'Enter' })

    await waitFor(() => expect(queuePosts(posted)).toHaveLength(1))
    expect(queuedText(queuePosts(posted)[0].body)).toBe('outro detalhe pra procurarmos')
    expect(messagePosts(posted)).toHaveLength(0)
  })

  it('the card answer goes to the queue of the asking turn, even with a message queued as a new turn behind it', async () => {
    const scenario: Scenario = {
      queuedTurnBehind: true,
      activities: [
        activity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', QUESTION),
        activity(2, 'turn.waiting_user', 'Aguardando sua resposta', { state: 'waiting_user_in_turn', in_turn: true }),
      ],
    }
    const posted = installFetch(scenario)
    renderChat()

    fireEvent.click(await screen.findByLabelText(/Bugs primeiro/))
    fireEvent.click(screen.getByRole('button', { name: 'Enviar respostas' }))

    await waitFor(() => expect(queuePosts(posted)).toHaveLength(1))
    expect(queuedText(queuePosts(posted)[0].body)).toContain('Seleção: Bugs primeiro')
    expect(messagePosts(posted)).toHaveLength(0)
    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'false'))
  })

  it('text typed in the composer while that turn waits is its answer: queue, never a new turn', async () => {
    const scenario: Scenario = {
      queuedTurnBehind: true,
      activities: [
        activity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', QUESTION),
        activity(2, 'turn.waiting_user', 'Aguardando sua resposta', { state: 'waiting_user_in_turn', in_turn: true }),
      ],
    }
    const posted = installFetch(scenario)
    renderChat()

    await waitFor(() => expect(cards()[0]).toHaveAttribute('data-active', 'true'))
    fireEvent.change(composer(), { target: { value: '??' } })
    fireEvent.keyDown(composer(), { key: 'Enter', code: 'Enter' })

    await waitFor(() => expect(queuePosts(posted)).toHaveLength(1))
    expect(queuedText(queuePosts(posted)[0].body)).toBe('??')
    expect(messagePosts(posted)).toHaveLength(0)
  })
})
