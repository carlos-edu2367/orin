import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { parseConversationActivityEvent } from '../../../api/conversations'
import { AgentPulse, modeFromEvents, pulseDetailFromEvents } from '../AgentPulse'
import { stateFor, type ConversationActivityEvent } from '../activityTypes'

afterEach(() => {
  cleanup()
})

const limitWaitingEvent: ConversationActivityEvent = {
  eventId: 'ev-wait-limit',
  cursor: '1',
  turnId: 'turn-1',
  type: 'turn.started',
  kind: 'lifecycle',
  state: 'queued',
  agentId: 'agent:main',
  summary: 'Aguardando vaga na CLI (2º na fila, limite de 3)',
  status: 'queued',
  waitingForSlot: true,
  queuePosition: 2,
}

const memoryWaitingEvent: ConversationActivityEvent = {
  eventId: 'ev-wait-mem',
  cursor: '2',
  turnId: 'turn-1',
  type: 'turn.started',
  kind: 'lifecycle',
  state: 'queued',
  agentId: 'agent:main',
  summary: 'Aguardando memória livre para abrir outro processo (1º na fila)',
  status: 'queued',
  waitingForSlot: true,
  queuePosition: 1,
}

describe('RedTeam Fase 3 Group 4 — CLI Slot Waiting UI (Surface 5)', () => {
  it('parses valid waiting_for_slot and queue_position and sanitizes malformed values', () => {
    const valid = parseConversationActivityEvent(
      {
        event_id: 'e1',
        event_type: 'turn.started',
        turn_id: 't1',
        agent_id: 'agent:main',
        summary: 'Aguardando vaga na CLI (3º na fila, limite de 4)',
        payload: {
          status: 'queued',
          waiting_for_slot: true,
          queue_position: 3.8,
        },
      },
      '1',
    )
    expect(valid.state).toBe('queued')
    expect(valid.waitingForSlot).toBe(true)
    expect(valid.queuePosition).toBe(3)

    // Non-boolean waiting_for_slot and non-positive / non-finite queue_position
    const malformed = parseConversationActivityEvent(
      {
        event_id: 'e2',
        event_type: 'turn.started',
        turn_id: 't1',
        agent_id: 'agent:main',
        summary: 'Iniciando',
        payload: {
          status: 'running',
          waiting_for_slot: 'true',
          queue_position: -5,
        },
      },
      '2',
    )
    expect(malformed.state).toBe('working')
    expect(malformed.waitingForSlot).toBeUndefined()
    expect(malformed.queuePosition).toBe(1)

    const nanPos = parseConversationActivityEvent(
      {
        event_id: 'e3',
        event_type: 'turn.started',
        turn_id: 't1',
        agent_id: 'agent:main',
        summary: 'Iniciando',
        payload: {
          waiting_for_slot: false,
          queue_position: Number.NaN,
        },
      },
      '3',
    )
    expect(nanPos.waitingForSlot).toBeUndefined()
    expect(nanPos.queuePosition).toBeUndefined()
  })

  it('maps turn.started to queued only when status is queued', () => {
    expect(stateFor('turn.started', 'queued')).toBe('queued')
    expect(stateFor('TURN_STARTED', 'queued')).toBe('queued')
    expect(stateFor('turn.started', 'running')).toBe('working')
    expect(stateFor('turn.started', undefined)).toBe('working')
  })

  it('displays both CLI slot limit waiting and free memory waiting in AgentPulse', () => {
    const { rerender } = render(
      <AgentPulse
        mode={modeFromEvents([limitWaitingEvent], 'queued')}
        state={limitWaitingEvent.state}
        detail={pulseDetailFromEvents([limitWaitingEvent])}
        activeTurnId="turn-1"
      />,
    )
    expect(
      screen.getByText('Aguardando vaga na CLI (2º na fila, limite de 3)'),
    ).toBeInTheDocument()

    rerender(
      <AgentPulse
        mode={modeFromEvents([limitWaitingEvent, memoryWaitingEvent], 'queued')}
        state={memoryWaitingEvent.state}
        detail={pulseDetailFromEvents([limitWaitingEvent, memoryWaitingEvent])}
        activeTurnId="turn-1"
      />,
    )
    expect(
      screen.getByText(
        'Aguardando memória livre para abrir outro processo (1º na fila)',
      ),
    ).toBeInTheDocument()
  })

  it('clears the waiting label as soon as thinking, tool.started, assistant.delta, or turn.completed arrives', () => {
    const followUps: ConversationActivityEvent[] = [
      {
        eventId: 'ev-think',
        cursor: '3',
        turnId: 'turn-1',
        type: 'agent.thought_delta',
        kind: 'lifecycle',
        state: 'working',
        agentId: 'agent:main',
        summary: 'Pensando',
      },
      {
        eventId: 'ev-tool',
        cursor: '4',
        turnId: 'turn-1',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'agent:main',
        toolName: 'read_file',
        toolKind: 'filesystem',
        summary: 'Lendo arquivo',
      },
      {
        eventId: 'ev-delta',
        cursor: '5',
        turnId: 'turn-1',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        agentId: 'agent:main',
        summary: 'Respondendo',
      },
      {
        eventId: 'ev-done',
        cursor: '6',
        turnId: 'turn-1',
        type: 'turn.completed',
        kind: 'lifecycle',
        state: 'completed',
        agentId: 'agent:main',
        summary: 'Concluído',
      },
    ]

    for (const nextEvent of followUps) {
      const stream = [limitWaitingEvent, nextEvent]
      expect(pulseDetailFromEvents(stream)).toBeUndefined()

      const mode = modeFromEvents(stream, nextEvent.state)
      const { container } = render(
        <AgentPulse
          mode={mode}
          state={nextEvent.state}
          detail={pulseDetailFromEvents(stream)}
          activeTurnId="turn-1"
        />,
      )
      expect(container.textContent ?? '').not.toContain('Aguardando vaga')
      cleanup()
    }
  })

  it('preserves waiting label if a non-visual context.updated metadata event is appended while waiting', () => {
    const contextUpdatedEvent: ConversationActivityEvent = {
      eventId: 'ev-ctx',
      cursor: '2',
      turnId: 'turn-1',
      type: 'context.updated',
      kind: 'lifecycle',
      state: 'working',
      agentId: 'agent:main',
      summary: 'Contexto atualizado',
    }
    const events = [limitWaitingEvent, contextUpdatedEvent]
    expect(pulseDetailFromEvents(events)).toBe(
      'Aguardando vaga na CLI (2º na fila, limite de 3)',
    )
  })
})
