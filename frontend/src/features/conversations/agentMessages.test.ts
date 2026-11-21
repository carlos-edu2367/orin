import { describe, expect, it } from 'vitest'
import {
  agentMessageStateLabel,
  applyAgentMessageEvent,
  getAgentMessages,
  hydrateAgentMessages,
  recordOptimisticAgentMessage,
  toVisualAgentMessageState,
} from './agentMessages'

describe('agentMessages reducer (WS-B7)', () => {
  it('keeps visual state as queued before agent.message_delivered', () => {
    expect(toVisualAgentMessageState('queued')).toBe('queued')
    expect(toVisualAgentMessageState('injected')).toBe('queued')
    expect(agentMessageStateLabel('queued')).toBe('Na fila')
    expect(agentMessageStateLabel('injected')).toBe('Na fila')
    expect(agentMessageStateLabel('delivered')).toBe('Entregue')
    expect(agentMessageStateLabel('forwarded')).toBe('Repassada ao Orin')
    expect(agentMessageStateLabel('expired')).toBe('Expirou')

    let state = recordOptimisticAgentMessage({}, {
      controlId: 'ctrl_1',
      agentId: 'agent-1',
      message: 'pare de editar X',
      createdAt: '2026-11-21T12:00:00Z',
    })
    expect(getAgentMessages(state, 'agent-1')[0].visualState).toBe('queued')

    // Hydrating with backend state='injected' still renders as 'queued' (never 'delivered').
    state = hydrateAgentMessages(state, 'agent-1', [
      {
        control_id: 'ctrl_1',
        agent_id: 'agent-1',
        sender_kind: 'user',
        sender_agent_id: null,
        message: 'pare de editar X',
        preview: 'pare de editar X',
        state: 'injected',
        created_at: '2026-11-21T12:00:00Z',
        delivered_at: null,
      },
    ])
    expect(getAgentMessages(state, 'agent-1')[0].state).toBe('injected')
    expect(getAgentMessages(state, 'agent-1')[0].visualState).toBe('queued')

    // Only agent.message_delivered promotes to 'delivered' with deliveredAt.
    state = applyAgentMessageEvent(state, {
      type: 'agent.message_delivered',
      agentId: 'agent-1',
      occurredAt: '2026-11-21T12:00:05Z',
      payload: {
        control_id: 'ctrl_1',
        agent_id: 'agent-1',
        sender_kind: 'user',
        preview: 'pare de editar X',
        delivered_at: '2026-11-21T12:00:05Z',
      },
    })
    const delivered = getAgentMessages(state, 'agent-1')[0]
    expect(delivered.state).toBe('delivered')
    expect(delivered.visualState).toBe('delivered')
    expect(delivered.deliveredAt).toBe('2026-11-21T12:00:05Z')

    // Stale queued event arriving out of order must not regress delivered state.
    state = applyAgentMessageEvent(state, {
      type: 'agent.message_queued',
      agentId: 'agent-1',
      occurredAt: '2026-11-21T12:00:01Z',
      payload: {
        control_id: 'ctrl_1',
        agent_id: 'agent-1',
        sender_kind: 'user',
        preview: 'pare de editar X',
      },
    })
    expect(getAgentMessages(state, 'agent-1')[0].state).toBe('delivered')
  })

  it('handles forwarded and expired SSE events', () => {
    let state = applyAgentMessageEvent({}, {
      type: 'agent.message_queued',
      agentId: 'agent-2',
      occurredAt: '2026-11-21T12:01:00Z',
      payload: {
        control_id: 'ctrl_2',
        agent_id: 'agent-2',
        sender_kind: 'parent',
        sender_agent_id: 'agent-main',
        preview: 'foco no teste',
      },
    })
    state = applyAgentMessageEvent(state, {
      type: 'agent.message_forwarded',
      agentId: 'agent-2',
      occurredAt: '2026-11-21T12:01:10Z',
      payload: {
        control_id: 'ctrl_2',
        agent_id: 'agent-2',
        sender_kind: 'parent',
        preview: 'foco no teste',
      },
    })
    expect(getAgentMessages(state, 'agent-2')[0].visualState).toBe('forwarded')

    state = applyAgentMessageEvent(state, {
      type: 'agent.message_expired',
      agentId: 'agent-2',
      occurredAt: '2026-11-21T12:02:00Z',
      payload: {
        control_id: 'ctrl_3',
        agent_id: 'agent-2',
        sender_kind: 'user',
        preview: 'expirada',
      },
    })
    expect(getAgentMessages(state, 'agent-2')[1].visualState).toBe('expired')
  })
})
