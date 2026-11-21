import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import type { ConversationActivityEvent } from '../activityTypes'
import {
  applyFriendDeliveryStatusEvent,
  FriendDeliveryCard,
  type FriendDeliveryItem,
} from '../FriendDeliveryCard'
import { TurnTimeline } from '../TurnTimeline'
import { groupLabel, isRenderable } from '../activitySummary'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('FriendDeliveryCard (WS-B9a)', () => {
  it('shows "Aguardando o próximo ponto seguro" with "Deixar para depois do turno" and calls defer endpoint', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      expect(init?.method).toBe('POST')
      expect(url).toContain('/v1/conversations/target-conv-1/queue/q-42/defer')
      return jsonResponse({
        queue_id: 'q-42',
        deferred: true,
        delivery: 'queued_next_turn',
        turn_id: 'turn-next-1',
        message_id: 'msg-next-1',
        state: 'queued',
      })
    })
    const client = new ApiClient({ fetchImpl: fetchMock })

    const delivery: FriendDeliveryItem = {
      id: 'q-42',
      targetConversationId: 'target-conv-1',
      queueId: 'q-42',
      turnId: 'turn-live-1',
      status: 'pending_in_turn',
      summary: 'Mensagem enviada para a sessão de código',
    }

    render(<FriendDeliveryCard delivery={delivery} client={client} />)

    expect(screen.getByTestId('friend-delivery-status').textContent).toBe(
      'Aguardando o próximo ponto seguro',
    )
    const deferButton = screen.getByRole('button', { name: /Deixar para depois do turno/i })
    expect(deferButton).toBeInTheDocument()

    await user.click(deferButton)

    await waitFor(() => {
      expect(screen.getByTestId('friend-delivery-status').textContent).toBe(
        'Na fila para depois do turno',
      )
    })
    expect(screen.queryByRole('button', { name: /Deixar para depois do turno/i })).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('transitions from pending_in_turn to injected, promoted, new_turn, and blocked via SSE events and hides defer button', () => {
    let map: Record<string, FriendDeliveryItem> = {}
    const toolFinished: ConversationActivityEvent = {
      eventId: 'ev-tool-1',
      cursor: '1',
      type: 'tool.finished',
      kind: 'tool',
      state: 'completed',
      agentId: 'friend-main',
      toolName: 'manage_session',
      summary: 'Mensagem enviada para a sessão target-2',
      payload: {
        action: 'send_message',
        conversation_id: 'target-2',
        queue_id: 'q-100',
        turn_id: 'turn-live',
        delivery: 'pending_in_turn',
      },
    }
    map = applyFriendDeliveryStatusEvent(map, toolFinished)
    expect(map['q-100']?.status).toBe('pending_in_turn')

    const { rerender } = render(<FriendDeliveryCard delivery={map['q-100']} />)
    expect(screen.getByTestId('friend-delivery-status').textContent).toBe(
      'Aguardando o próximo ponto seguro',
    )
    expect(screen.getByRole('button', { name: /Deixar para depois do turno/i })).toBeInTheDocument()

    // Receive injected confirmation
    const injectedEvent: ConversationActivityEvent = {
      eventId: 'ev-del-1',
      cursor: '2',
      type: 'friend.delivery_status',
      kind: 'lifecycle',
      state: 'completed',
      agentId: 'friend-main',
      summary: 'A mensagem enviada à sessão target-2 foi entregue no turno em andamento.',
      payload: {
        status: 'injected',
        queue_id: 'q-100',
        target_conversation_id: 'target-2',
        target_turn_id: 'turn-live',
      },
    }
    map = applyFriendDeliveryStatusEvent(map, injectedEvent)
    rerender(<FriendDeliveryCard delivery={map['q-100']} />)

    expect(screen.getByTestId('friend-delivery-status').textContent).toBe(
      'Entregue no turno em andamento',
    )
    expect(screen.queryByRole('button', { name: /Deixar para depois do turno/i })).toBeNull()

    // New turn state
    rerender(
      <FriendDeliveryCard
        delivery={{
          id: 'turn-new',
          targetConversationId: 'target-3',
          queueId: null,
          turnId: 'turn-new',
          status: 'new_turn',
        }}
      />,
    )
    expect(screen.getByTestId('friend-delivery-status').textContent).toBe('Novo turno iniciado')
    expect(screen.queryByRole('button', { name: /Deixar para depois do turno/i })).toBeNull()

    // Blocked by USER_PROMPT_SUBMIT hook — must say "Bloqueada", never "Entregue"
    let blockedMap: Record<string, FriendDeliveryItem> = {}
    const blockedEvent: ConversationActivityEvent = {
      eventId: 'ev-blk-1',
      cursor: '3',
      type: 'friend.delivery_status',
      kind: 'lifecycle',
      state: 'failed',
      agentId: 'friend-main',
      summary: 'Bloqueada pelo hook',
      payload: {
        status: 'blocked',
        queue_id: 'q-200',
        target_conversation_id: 'target-4',
        reason: 'USER_PROMPT_SUBMIT recusou',
      },
    }
    blockedMap = applyFriendDeliveryStatusEvent(blockedMap, blockedEvent)
    rerender(<FriendDeliveryCard delivery={blockedMap['q-200']} />)
    expect(screen.getByTestId('friend-delivery-status').textContent).toBe('Bloqueada')
    expect(screen.getByText(/USER_PROMPT_SUBMIT recusou/)).toBeInTheDocument()
    expect(screen.queryByText(/Entregue/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /Deixar para depois do turno/i })).toBeNull()
  })

  it('shows "via Friend" badge in TurnTimeline for friend-originated queued_input and clean summaries for agent.message_*', () => {
    const friendQueuedEvent: ConversationActivityEvent = {
      eventId: 'ev-q-friend',
      cursor: '10',
      type: 'turn.started',
      kind: 'lifecycle',
      state: 'working',
      agentId: 'agent-main',
      turnId: 'turn-1',
      summary: 'Mensagem via Friend entregue ao agente',
      payload: {
        origin: 'friend',
        origin_conversation_id: 'friend-conv-1',
        queue_id: 'q-55',
        queued_text: 'Mensagem enviada pelo Orin Friend em nome do usuário:\nRode os testes de contrato',
      },
    }

    expect(isRenderable(friendQueuedEvent, new Set())).toBe(true)

    render(
      <TurnTimeline
        events={[friendQueuedEvent]}
        conversationId="target-conv-1"
        mode="code"
      />,
    )

    expect(screen.getByTestId('via-friend-badge').textContent).toBe('via Friend')
    expect(screen.getByText(/Rode os testes de contrato/)).toBeInTheDocument()

    // Clean summaries for agent.message_* events
    const queuedSummary = groupLabel({
      id: 'agent:sub-1:t1:agent.message_queued:',
      kind: 'agent',
      state: 'queued',
      label: '',
      count: 1,
      agentId: 'sub-1',
      agentName: 'Analista',
      failed: false,
      events: [
        {
          eventId: 'e1',
          cursor: '1',
          type: 'agent.message_queued',
          kind: 'agent',
          state: 'queued',
          agentId: 'sub-1',
          agentName: 'Analista',
          summary: '',
        },
      ],
    })
    expect(queuedSummary).toContain('Na fila')
    expect(queuedSummary).not.toContain('Entregue')

    const deliveredSummary = groupLabel({
      id: 'agent:sub-1:t1:agent.message_delivered:',
      kind: 'agent',
      state: 'completed',
      label: '',
      count: 1,
      agentId: 'sub-1',
      agentName: 'Analista',
      failed: false,
      events: [
        {
          eventId: 'e2',
          cursor: '2',
          type: 'agent.message_delivered',
          kind: 'agent',
          state: 'completed',
          agentId: 'sub-1',
          agentName: 'Analista',
          summary: '',
        },
      ],
    })
    expect(deliveredSummary).toContain('Entregue')
  })
})
