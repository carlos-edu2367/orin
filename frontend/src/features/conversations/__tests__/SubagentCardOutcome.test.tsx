import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { ConversationActivityEvent } from '../activityTypes'
import { TurnTimeline } from '../TurnTimeline'

afterEach(() => {
  cleanup()
})

const MAIN = 'agent:chat_1:main'
const SUB = 'agent:chat_1:sub-4'

// The shape these events have in orin.db: the subagent's own agent_id, parent_agent_id = main.
function subEvent(overrides: Partial<ConversationActivityEvent> & { eventId: string }): ConversationActivityEvent {
  return {
    cursor: overrides.eventId,
    turnId: 'turn-1',
    agentId: SUB,
    agentName: 'general-4',
    parentAgentId: MAIN,
    kind: 'tool',
    type: 'tool.finished',
    state: 'completed',
    status: 'succeeded',
    toolKind: 'filesystem',
    summary: '',
    occurredAt: '2026-11-10T17:47:00Z',
    ...overrides,
  }
}

function subagentTurn(outcome: ConversationActivityEvent): ConversationActivityEvent[] {
  return [
    {
      eventId: 'sent',
      cursor: 'sent',
      turnId: 'turn-1',
      agentId: MAIN,
      kind: 'agent',
      type: 'agent.message_sent',
      state: 'waiting_agent',
      label: 'general-4',
      summary: 'Enviou uma tarefa para general-4',
      occurredAt: '2026-11-10T17:47:00Z',
    },
    subEvent({ eventId: 'read', toolName: 'read_file', path: 'src/App.tsx', summary: 'Leu src/App.tsx' }),
    subEvent({
      eventId: 'refused',
      toolName: 'run_command',
      toolKind: 'terminal',
      state: 'failed',
      status: 'failed',
      errorCode: 'TOOL_REFUSED',
      summary: 'command is too long (9693 > 4096 characters).',
    }),
    subEvent({
      eventId: 'measure',
      toolName: 'run_command',
      toolKind: 'terminal',
      command: 'node .orin/tmp/measure.mjs',
      summary: '$ Measure quiz mobile layout',
    }),
    outcome,
  ]
}

const delivered: ConversationActivityEvent = {
  eventId: 'received',
  cursor: 'received',
  turnId: 'turn-1',
  agentId: SUB,
  parentAgentId: MAIN,
  kind: 'agent',
  type: 'agent.message_received',
  state: 'completed',
  label: 'general-4',
  summary: 'Recebeu a resposta de general-4',
  occurredAt: '2026-11-10T17:52:00Z',
}

const failedDelegation: ConversationActivityEvent = {
  eventId: 'delegation-failed',
  cursor: 'delegation-failed',
  turnId: 'turn-1',
  agentId: SUB,
  parentAgentId: MAIN,
  kind: 'agent',
  type: 'delegation.failed',
  state: 'failed',
  label: 'general-4',
  errorCode: 'PROVIDER_STREAM_INTERRUPTED',
  payload: { error_code: 'PROVIDER_STREAM_INTERRUPTED' },
  summary: 'general-4 não concluiu a tarefa',
  occurredAt: '2026-11-10T17:52:00Z',
}

describe('subagent card status comes from how the subagent ended', () => {
  it('shows Concluído for a subagent that delivered its report after a refused command', () => {
    render(<TurnTimeline events={subagentTurn(delivered)} mode="code" turnCompleted />)

    const card = screen.getByTestId('subagent-task-group')
    expect(card.getAttribute('data-agent-id')).toBe(SUB)
    expect(card.getAttribute('data-status')).toBe('completed')
    expect(screen.getByTestId('subagent-task-status').textContent).toBe('Concluído')

    // The refused action is still shown, as a failure, inside the card.
    fireEvent.click(screen.getByTestId('subagent-task-toggle'))
    for (const button of within(card).getAllByRole('button')) {
      if (button.getAttribute('aria-expanded') === 'false') fireEvent.click(button)
    }
    expect(card.textContent).toContain('command is too long')
  })

  it('shows Falhou for a subagent whose delegation failed', () => {
    render(<TurnTimeline events={subagentTurn(failedDelegation)} mode="code" turnCompleted />)

    const card = screen.getByTestId('subagent-task-group')
    expect(card.getAttribute('data-status')).toBe('failed')
    expect(screen.getByTestId('subagent-task-status').textContent).toBe('Falhou')
  })
})
