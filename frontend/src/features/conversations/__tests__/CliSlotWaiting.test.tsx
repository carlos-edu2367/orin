import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { parseConversationActivityEvent } from '../../../api/conversations'
import { AgentPulse, pulseDetailFromEvents } from '../AgentPulse'
import { stateFor, type ConversationActivityEvent } from '../activityTypes'

const waiting: ConversationActivityEvent = {
  eventId: 'w1',
  cursor: '1',
  turnId: 't1',
  type: 'turn.started',
  kind: 'lifecycle',
  state: 'queued',
  agentId: 'agent:main',
  summary: 'Aguardando vaga na CLI (2º na fila, limite de 3)',
  status: 'queued',
  waitingForSlot: true,
  queuePosition: 2,
}

describe('CliSlotWaiting UI (R3.6)', () => {
  it('maps a queued turn.started to the queued state', () => {
    expect(stateFor('turn.started', 'queued')).toBe('queued')
    expect(stateFor('TURN_STARTED', 'queued')).toBe('queued')
  })

  it('parses waiting_for_slot and queue_position from activity payload', () => {
    const parsed = parseConversationActivityEvent(
      {
        event_id: 'w-raw',
        event_type: 'turn.started',
        turn_id: 't1',
        agent_id: 'agent:main',
        summary: 'Aguardando vaga na CLI (2º na fila, limite de 3)',
        payload: {
          status: 'queued',
          waiting_for_slot: true,
          queue_position: 2,
        },
      },
      '1',
    )
    expect(parsed.state).toBe('queued')
    expect(parsed.waitingForSlot).toBe(true)
    expect(parsed.queuePosition).toBe(2)
  })

  it('formats waiting_subagents as "Aguardando Antigravity (7 min)" in pulseDetailFromEvents and AgentPulse', () => {
    const parsed = parseConversationActivityEvent(
      {
        event_id: 'ws-raw',
        event_type: 'turn.started',
        turn_id: 't1',
        agent_id: 'agent:main',
        summary: 'Aguardando Antigravity',
        occurred_at: '2026-08-10T20:07:00+00:00',
        payload: {
          state: 'waiting_subagents',
          waiting_subagents: true,
          agents: ['Antigravity'],
        },
      },
      '2',
    )
    expect(parsed.waitingSubagents).toBe(true)
    expect(parsed.waitingAgents).toEqual(['Antigravity'])
    expect(parsed.state).toBe('waiting_agent')
    expect(
      pulseDetailFromEvents([parsed], '2026-08-10T20:00:00+00:00'),
    ).toBe('Aguardando Antigravity (7 min)')
    render(
      <AgentPulse
        mode="waiting_agent"
        state="waiting_agent"
        detail={pulseDetailFromEvents([parsed], '2026-08-10T20:00:00+00:00')}
      />,
    )
    expect(screen.getByText('Aguardando Antigravity (7 min)')).toBeInTheDocument()
  })

  it('keeps modeFromEvents as waiting_agent when subagent emits child tool events while root is waiting_subagents', async () => {
    const { modeFromEvents } = await import('../AgentPulse')
    const rootWaiting = parseConversationActivityEvent(
      {
        event_id: 'ws-root',
        event_type: 'turn.started',
        turn_id: 't1',
        agent_id: 'agent:main',
        summary: 'Aguardando Antigravity',
        occurred_at: '2026-08-10T20:02:00+00:00',
        payload: {
          state: 'waiting_subagents',
          waiting_subagents: true,
          agents: ['Antigravity'],
        },
      },
      '1',
    )
    const childToolFinished = parseConversationActivityEvent(
      {
        event_id: 'child-1',
        event_type: 'tool.finished',
        turn_id: 't1',
        agent_id: 'agent:sub:1',
        parent_agent_id: 'agent:main',
        summary: 'read_file succeeded',
        occurred_at: '2026-08-10T20:02:30+00:00',
        payload: { tool_name: 'read_file', status: 'succeeded' },
      },
      '2',
    )
    expect(modeFromEvents([rootWaiting, childToolFinished], true, 't1')).toBe('waiting_agent')
  })

  it('shows the waiting summary while the last event is waiting for a slot', () => {
    expect(pulseDetailFromEvents([waiting])).toBe(
      'Aguardando vaga na CLI (2º na fila, limite de 3)',
    )
    expect(
      pulseDetailFromEvents([
        waiting,
        {
          ...waiting,
          eventId: 'w2',
          waitingForSlot: undefined,
          summary: 'Pensando',
        },
      ]),
    ).toBeUndefined()
    render(
      <AgentPulse
        mode="thinking"
        state="queued"
        detail={pulseDetailFromEvents([waiting])}
      />,
    )
    expect(
      screen.getByText('Aguardando vaga na CLI (2º na fila, limite de 3)'),
    ).toBeInTheDocument()
  })
})
