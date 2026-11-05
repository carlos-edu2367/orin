import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../../../app/ThemeContext'
import { ChatPage } from '../ChatPage'

const CONVERSATION_ID = 'conv_native_ask_user'

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

const QUESTION_PAYLOAD = {
  tool_name: 'ask_user',
  tool_kind: 'user_input',
  status: 'succeeded',
  wait_for_user: true,
  questions: [
    {
      id: 'q1',
      question: 'Qual banco de dados usar?',
      header: 'Banco',
      mode: 'single_choice',
      options: [
        { id: 'sqlite', label: 'SQLite', description: 'Local' },
        { id: 'postgres', label: 'Postgres', description: 'Servidor' },
      ],
    },
  ],
}

describe('ChatPage native ask_user in-turn queue flow', () => {
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

  it('envia respostas de pergunta de turno vivo pela fila', async () => {
    const calledUrls: Array<{ url: string; method: string; body: string }> = []

    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      const body = typeof init?.body === 'string' ? init.body : ''
      if (method === 'POST') {
        calledUrls.push({ url, method, body })
      }
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.2"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      if (url.endsWith(`/v1/conversations/${CONVERSATION_ID}/queue`) && method === 'POST') {
        return new Response(
          JSON.stringify({ queued: true, queue_id: 'q-123' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
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
          { status: 202, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`) && !url.includes('/queue') && !url.includes('/events')) {
        return new Response(
          JSON.stringify({
            conversation_id: CONVERSATION_ID,
            title: 'Pergunta nativa',
            state: 'running',
            mode: 'code',
            permission_mode: 'accept_edits',
            provider: 'anthropic',
            model_id: 'claude-opus-5-5',
            messages: [
              {
                message_id: 'msg-1',
                role: 'user',
                content: 'Configure o banco',
                status: 'completed',
                retryable: false,
                turn_id: 'turn-1',
              },
              {
                message_id: 'msg-2',
                role: 'assistant',
                content: 'Antes de continuar, preciso confirmar o banco.',
                status: 'streaming',
                retryable: false,
                turn_id: 'turn-1',
              },
            ],
            turns: [
              {
                turn_id: 'turn-1',
                state: 'running',
                code_mode: 'code',
                created_at: '2026-11-05T16:30:00.000Z',
                started_at: '2026-11-05T16:30:00.000Z',
                finished_at: null,
              },
            ],
            activities: [
              makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', QUESTION_PAYLOAD),
              makeActivity(2, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
            ],
            activity_cursor: 'a.2',
            workspace: {
              kind: 'local',
              path: 'C:/Projetos/demo',
              folder_name: 'demo',
              scope: 'project',
              project_name: 'Demo',
            },
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(JSON.stringify({ items: [] }), {
        headers: { 'Content-Type': 'application/json' },
      })
    })

    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={[`/chats/${CONVERSATION_ID}`]}>
          <Routes>
            <Route path="/chats/:conversationId" element={<ChatPage />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>,
    )

    const optionLabel = await screen.findByLabelText(/SQLite/)
    fireEvent.click(optionLabel)

    const submitButton = screen.getByRole('button', { name: 'Enviar respostas' })
    expect(submitButton).not.toBeDisabled()
    fireEvent.click(submitButton)

    await waitFor(() => {
      expect(calledUrls.some((item) => item.url.endsWith(`/v1/conversations/${CONVERSATION_ID}/queue`))).toBe(true)
    })
    expect(calledUrls.some((item) => item.url.endsWith(`/v1/conversations/${CONVERSATION_ID}/messages`))).toBe(false)
  })

  it('fecha a pergunta quando chega turn.started com queue_id do mesmo turno', async () => {
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.3"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`) && !url.includes('/queue') && !url.includes('/events')) {
        return new Response(
          JSON.stringify({
            conversation_id: CONVERSATION_ID,
            title: 'Pergunta nativa respondida',
            state: 'running',
            mode: 'code',
            permission_mode: 'accept_edits',
            provider: 'anthropic',
            model_id: 'claude-opus-5-5',
            messages: [
              {
                message_id: 'msg-1',
                role: 'user',
                content: 'Configure o banco',
                status: 'completed',
                retryable: false,
                turn_id: 'turn-1',
              },
              {
                message_id: 'msg-2',
                role: 'assistant',
                content: 'Antes de continuar, preciso confirmar o banco.',
                status: 'streaming',
                retryable: false,
                turn_id: 'turn-1',
              },
            ],
            turns: [
              {
                turn_id: 'turn-1',
                state: 'running',
                code_mode: 'code',
                created_at: '2026-11-05T16:30:00.000Z',
                started_at: '2026-11-05T16:30:00.000Z',
                finished_at: null,
              },
            ],
            activities: [
              makeActivity(1, 'tool.finished', 'Aguardando resposta para 1 pergunta', QUESTION_PAYLOAD),
              makeActivity(2, 'turn.waiting_user', 'Aguardando sua resposta', { in_turn: true }),
              makeActivity(3, 'turn.started', 'Mensagem da fila entregue ao agente', {
                queue_id: 'q-123',
                queued_text: 'Respostas às perguntas do agente:\nSQLite',
              }),
            ],
            activity_cursor: 'a.3',
            workspace: {
              kind: 'local',
              path: 'C:/Projetos/demo',
              folder_name: 'demo',
              scope: 'project',
              project_name: 'Demo',
            },
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(JSON.stringify({ items: [] }), {
        headers: { 'Content-Type': 'application/json' },
      })
    })

    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={[`/chats/${CONVERSATION_ID}`]}>
          <Routes>
            <Route path="/chats/:conversationId" element={<ChatPage />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>,
    )

    const card = await screen.findByTestId('user-question-card')
    expect(card).toHaveAttribute('data-active', 'false')
    expect(screen.getByText('As respostas foram enviadas para o agente.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Enviar respostas' })).toBeNull()
  })
})
