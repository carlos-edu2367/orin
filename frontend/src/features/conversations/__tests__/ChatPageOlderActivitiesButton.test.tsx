import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../ChatPage'

const CONVERSATION_ID = 'chat_older_acts'

function makeActivity(
  sequence: number,
  type: string,
  summary: string,
  payload: Record<string, unknown> = {},
) {
  return {
    event_id: `activity:turn-1:${sequence}`,
    event_type: type,
    sequence,
    summary,
    payload,
    occurred_at: '2026-08-10T20:00:00+00:00',
    turn_id: 'turn-1',
    execution_id: 'exe-1',
    agent_id: `agent:${CONVERSATION_ID}:main`,
    parent_agent_id: null,
    cursor: `a.${sequence}`,
  }
}

describe('ChatPage — top "Carregar ações anteriores" button', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it('shows the top "Carregar ações anteriores" button even when has_older_messages is false, and loads older activities on click', async () => {
    const user = userEvent.setup()
    let olderRequests = 0

    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.20"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}/activities`)) {
        olderRequests += 1
        return new Response(
          JSON.stringify({
            activities: [
              makeActivity(5, 'tool.finished', 'Leu arquivo antigo.ts', {
                tool_name: 'read_file',
                tool_kind: 'filesystem',
                path: 'antigo.ts',
                status: 'succeeded',
              }),
            ],
            has_older_activities: false,
            oldest_activity_id: null,
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.endsWith(`/v1/conversations/${CONVERSATION_ID}/queue`)) {
        return new Response(JSON.stringify({ items: [] }), {
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/v1/projects/sidebar')) {
        return new Response(JSON.stringify({ items: [] }), {
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/v1/conversations')) {
        return new Response(
          JSON.stringify({
            items: [{ conversation_id: CONVERSATION_ID, title: 'Turno longo', state: 'completed' }],
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.includes(`/v1/conversations/${CONVERSATION_ID}`)) {
        return new Response(
          JSON.stringify({
            conversation_id: CONVERSATION_ID,
            title: 'Turno longo',
            state: 'completed',
            mode: 'code',
            provider: 'openrouter',
            model_id: 'model-a',
            has_older_messages: false,
            oldest_message_sequence: null,
            has_older_activities: true,
            oldest_activity_id: 10,
            turns: [
              {
                turn_id: 'turn-1',
                state: 'completed',
                created_at: '2026-08-10T20:00:00+00:00',
                started_at: '2026-08-10T20:00:01+00:00',
                finished_at: '2026-08-10T20:01:00+00:00',
              },
            ],
            messages: [
              {
                message_id: 'u-1',
                role: 'user',
                content: 'Execute a auditoria completa',
                status: 'completed',
                retryable: false,
                turn_id: 'turn-1',
              },
              {
                message_id: 'a-1',
                role: 'assistant',
                content: 'Auditoria concluída.',
                status: 'completed',
                retryable: false,
                turn_id: 'turn-1',
              },
            ],
            activities: [
              makeActivity(10, 'tool.finished', 'Leu arquivo recente.ts', {
                tool_name: 'read_file',
                tool_kind: 'filesystem',
                path: 'recente.ts',
                status: 'succeeded',
              }),
              makeActivity(11, 'assistant.delta', 'Auditoria concluída.', {
                content: 'Auditoria concluída.',
              }),
            ],
            activity_cursor: 'a.11',
            workspace: {
              kind: 'managed',
              path: null,
              folder_name: null,
              scope: 'chat',
              project_name: null,
            },
          }),
          { headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(JSON.stringify({}), {
        headers: { 'Content-Type': 'application/json' },
      })
    })

    render(
      <MemoryRouter initialEntries={[`/chat/${CONVERSATION_ID}`]}>
        <Routes>
          <Route path="/chat/:conversationId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>,
    )

    const topBtn = await screen.findByTestId('load-older-activities-top')
    expect(topBtn).toBeInTheDocument()
    expect(topBtn).toHaveTextContent('Carregar ações anteriores')
    expect(screen.queryByTestId('load-older-messages')).not.toBeInTheDocument()

    await user.click(topBtn)

    await waitFor(() => {
      expect(olderRequests).toBe(1)
    })
    await waitFor(() => {
      expect(screen.queryByTestId('load-older-activities-top')).not.toBeInTheDocument()
    })
  })
})
