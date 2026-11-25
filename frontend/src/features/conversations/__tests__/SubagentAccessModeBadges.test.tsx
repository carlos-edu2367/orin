import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ActivityCard } from '../ActivityCard'
import { TurnTimeline } from '../TurnTimeline'
import type { ActivityGroup, ConversationActivityEvent } from '../activityTypes'

function makeEvent(partial: Partial<ConversationActivityEvent>): ConversationActivityEvent {
  return {
    eventId: partial.eventId ?? 'ev-1',
    cursor: partial.cursor ?? '1',
    turnId: partial.turnId ?? 'turn-1',
    agentId: partial.agentId ?? 'agent-sub-1',
    parentAgentId: partial.parentAgentId,
    agentName: partial.agentName ?? 'validador-visual',
    type: partial.type ?? 'agent.message_sent',
    kind: partial.kind ?? 'agent',
    state: partial.state ?? 'completed',
    status: partial.status ?? 'completed',
    summary: partial.summary ?? 'Enviou uma tarefa para validador-visual',
    label: partial.label ?? 'validador-visual',
    occurredAt: partial.occurredAt ?? '2026-11-25T15:00:00Z',
    payload: partial.payload ?? {},
    toolName: partial.toolName,
    toolKind: partial.toolKind,
  }
}

describe('Subagent access_mode and attachment badges (Frente B)', () => {
  it('renders Somente pergunta and 2 anexos badges on ActivityCard', () => {
    const ev = makeEvent({
      payload: { access_mode: 'prompt_only', attachment_count: 2, attachment_names: ['ref.png', 'out.png'] },
    })
    const group: ActivityGroup = {
      id: 'grp-1',
      agentId: 'agent-sub-1',
      agentName: 'validador-visual',
      kind: 'agent',
      state: 'completed',
      label: 'validador-visual',
      count: 1,
      failed: false,
      events: [ev],
    }

    render(<ActivityCard group={group} />)
    expect(screen.getByTestId('activity-card-access-mode-badge')).toHaveTextContent('Somente pergunta')
    expect(screen.getByTestId('activity-card-attachments-badge')).toHaveTextContent('2 anexos')
  })

  it('renders Somente leitura badge on TurnTimeline SubagentTaskContainer', () => {
    const sent = makeEvent({
      eventId: 'ev-sent',
      cursor: '1',
      agentId: 'agent-main',
      parentAgentId: undefined,
      agentName: 'auditor',
      type: 'agent.message_sent',
      kind: 'agent',
      payload: {
        to_agent_id: 'agent-sub-ro',
        access_mode: 'read_only',
        attachment_count: 1,
        attachment_names: ['spec.md'],
      },
    })
    const toolEv = makeEvent({
      eventId: 'ev-tool',
      cursor: '2',
      agentId: 'agent-sub-ro',
      parentAgentId: 'agent-main',
      agentName: 'auditor',
      type: 'tool.finished',
      kind: 'tool',
      toolName: 'read_file',
      toolKind: 'filesystem',
      summary: 'Leu spec.md',
      payload: { tool_name: 'read_file', path: 'spec.md' },
    })

    render(
      <TurnTimeline
        events={[sent, toolEv]}
        turnCompleted
      />,
    )

    expect(screen.getByTestId('subagent-access-mode-badge')).toHaveTextContent('Somente leitura')
    expect(screen.getByTestId('subagent-attachments-badge')).toHaveTextContent('1 anexo')
  })
})
