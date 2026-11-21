import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import type { ConversationActivityEvent } from '../activityTypes'
import { ActivityCard } from '../ActivityCard'
import { AgentMessageBox } from '../AgentMessageBox'
import {
  applyAgentMessageEvent,
  getAgentMessages,
  type AgentMessagesState,
} from '../agentMessages'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('AgentMessageBox (WS-B9a)', () => {
  it('shows "Na fila" on send (never "Entregue") and updates to "Entregue" with local time only on agent.message_delivered', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.includes('/agents/agent-1/messages') && (!init?.method || init.method === 'GET')) {
        return jsonResponse({ conversation_id: 'conv-1', agent_id: 'agent-1', messages: [] })
      }
      if (url.includes('/agents/agent-1/messages') && init?.method === 'POST') {
        return jsonResponse({ agent_id: 'agent-1', status: 'queued', control_id: 'ctrl-1' }, 202)
      }
      throw new Error(`Unexpected request: ${init?.method ?? 'GET'} ${url}`)
    })
    const client = new ApiClient({ fetchImpl: fetchMock as typeof fetch })

    let liveState: AgentMessagesState = {}
    const { rerender } = render(
      <AgentMessageBox
        conversationId="conv-1"
        agentId="agent-1"
        agentName="Analista"
        running={true}
        client={client}
        liveMessages={getAgentMessages(liveState, 'agent-1')}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Mandar mensagem/i }))
    const input = screen.getByLabelText(/Mensagem para Analista/i)
    await user.type(input, 'pare de editar X')
    await user.click(screen.getByRole('button', { name: /^Enviar$/i }))

    await waitFor(() => {
      expect(screen.getByText('pare de editar X')).toBeInTheDocument()
    })

    // Strictly "Na fila" before agent.message_delivered — never "Entregue".
    const statusBadge = screen.getByTestId('agent-message-status-ctrl-1')
    expect(statusBadge.textContent).toBe('Na fila')
    expect(screen.queryByText(/Entregue/i)).toBeNull()

    // Live SSE event promotes state to delivered with delivered_at timestamp.
    liveState = applyAgentMessageEvent(liveState, {
      type: 'agent.message_delivered',
      agentId: 'agent-1',
      occurredAt: '2026-11-21T15:04:05Z',
      payload: {
        control_id: 'ctrl-1',
        agent_id: 'agent-1',
        sender_kind: 'user',
        preview: 'pare de editar X',
        delivered_at: '2026-11-21T15:04:05Z',
      },
    })

    rerender(
      <AgentMessageBox
        conversationId="conv-1"
        agentId="agent-1"
        agentName="Analista"
        running={true}
        client={client}
        liveMessages={getAgentMessages(liveState, 'agent-1')}
      />,
    )

    expect(screen.getByTestId('agent-message-status-ctrl-1').textContent).toContain('Entregue')
    expect(screen.getByTestId('agent-message-delivered-time-ctrl-1').textContent?.trim().length).toBeGreaterThan(0)
  })

  it('reconstructs persisted states from GET /messages on mount (injected stays "Na fila", forwarded, expired, stopped)', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        conversation_id: 'conv-reload',
        agent_id: 'agent-2',
        messages: [
          {
            control_id: 'ctrl-inj',
            agent_id: 'agent-2',
            sender_kind: 'user',
            sender_agent_id: null,
            message: 'ainda injetada',
            preview: 'ainda injetada',
            state: 'injected',
            created_at: '2026-11-21T14:00:00Z',
            delivered_at: null,
          },
          {
            control_id: 'ctrl-del',
            agent_id: 'agent-2',
            sender_kind: 'parent',
            sender_agent_id: 'agent-main',
            message: 'foco nos testes',
            preview: 'foco nos testes',
            state: 'delivered',
            created_at: '2026-11-21T14:01:00Z',
            delivered_at: '2026-11-21T14:01:12Z',
          },
          {
            control_id: 'ctrl-fwd',
            agent_id: 'agent-2',
            sender_kind: 'user',
            sender_agent_id: null,
            message: 'repassar ao pai',
            preview: 'repassar ao pai',
            state: 'forwarded',
            created_at: '2026-11-21T14:02:00Z',
            delivered_at: null,
          },
          {
            control_id: 'ctrl-exp',
            agent_id: 'agent-2',
            sender_kind: 'user',
            sender_agent_id: null,
            message: 'expirou',
            preview: 'expirou',
            state: 'expired',
            created_at: '2026-11-21T14:03:00Z',
            delivered_at: null,
          },
          {
            control_id: 'ctrl-stop',
            agent_id: 'agent-2',
            sender_kind: 'user',
            sender_agent_id: null,
            message: 'agente parou',
            preview: 'agente parou',
            state: 'stopped',
            created_at: '2026-11-21T14:04:00Z',
            delivered_at: null,
          },
        ],
      }),
    )
    const client = new ApiClient({ fetchImpl: fetchMock as typeof fetch })

    render(
      <AgentMessageBox
        conversationId="conv-reload"
        agentId="agent-2"
        agentName="Worker"
        running={false}
        client={client}
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('agent-message-status-ctrl-inj')).toBeInTheDocument()
    })

    expect(screen.getByTestId('agent-message-status-ctrl-inj').textContent).toBe('Na fila')
    expect(screen.getByTestId('agent-message-status-ctrl-del').textContent).toContain('Entregue')
    expect(screen.getByTestId('agent-message-delivered-time-ctrl-del')).toBeInTheDocument()
    expect(screen.getByTestId('agent-message-status-ctrl-fwd').textContent).toBe('Repassada ao Orin')
    expect(screen.getByTestId('agent-message-status-ctrl-exp').textContent).toBe('Expirou')
    expect(screen.getByTestId('agent-message-status-ctrl-stop').textContent).toBe('Expirou')
    // When not running, compose trigger is not shown, but history remains visible.
    expect(screen.queryByRole('button', { name: /Mandar mensagem/i })).toBeNull()
  })

  it('surfaces a clear alert on 409 AGENT_NOT_RUNNING without adding a delivered item', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn<typeof fetch>(async (_input, init) => {
      if (!init?.method || init.method === 'GET') {
        return jsonResponse({ conversation_id: 'conv-1', agent_id: 'agent-3', messages: [] })
      }
      return jsonResponse({ error: { code: 'AGENT_NOT_RUNNING', message_key: 'AGENT_NOT_RUNNING' } }, 409)
    })
    const client = new ApiClient({ fetchImpl: fetchMock as typeof fetch })

    render(
      <AgentMessageBox
        conversationId="conv-1"
        agentId="agent-3"
        agentName="Revisor"
        running={true}
        defaultOpen={true}
        client={client}
      />,
    )

    await user.type(screen.getByLabelText(/Mensagem para Revisor/i), 'tarde demais')
    await user.click(screen.getByRole('button', { name: /^Enviar$/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('já terminou')
    })
    expect(screen.queryByText(/Entregue/i)).toBeNull()
  })

  it('renders AgentMessageBox inside a running subagent ActivityCard', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ conversation_id: 'conv-card', agent_id: 'sub-9', messages: [] }),
    )
    const client = new ApiClient({ fetchImpl: fetchMock as typeof fetch })

    const event: ConversationActivityEvent = {
      eventId: 'ev-sub-1',
      cursor: '1',
      type: 'tool.started',
      kind: 'tool',
      state: 'waiting_tool',
      agentId: 'sub-9',
      agentName: 'Explorador',
      parentAgentId: 'agent-main',
      toolName: 'bash',
      summary: 'Executando testes',
    }

    render(
      <ActivityCard
        group={{
          id: 'tool:sub-9:turn-1',
          kind: 'tool',
          state: 'waiting_tool',
          label: 'Executando testes',
          count: 1,
          events: [event],
          agentId: 'sub-9',
          agentName: 'Explorador',
          failed: false,
        }}
        conversationId="conv-card"
        client={client}
      />,
    )

    expect(await screen.findByRole('button', { name: /Mandar mensagem/i })).toBeInTheDocument()
  })
})
