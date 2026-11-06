import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { parseConversationActivityEvent } from '../../../api/conversations'
import { CodeModeCard } from '../CodeModeCard'
import { TurnTimeline } from '../TurnTimeline'
import type { ConversationActivityEvent } from '../activityTypes'

const unverified: ConversationActivityEvent = {
  eventId: 'evt-unv',
  cursor: '9',
  turnId: 'turn-1',
  type: 'code_mode.unverified',
  kind: 'lifecycle',
  state: 'completed',
  agentId: 'agent:main',
  summary: 'Entrega não verificada',
  verification: 'unverified',
}

describe('CodeUnverified', () => {
  it('shows "não verificado" in the code-mode timeline', () => {
    render(
      <TurnTimeline
        mode="code"
        items={[
          {
            id: 'code-mode:turn-1',
            kind: 'activity',
            group: {
              id: 'code-mode:turn-1',
              kind: 'lifecycle',
              state: 'completed',
              label: 'Modo Code',
              count: 1,
              agentId: 'agent:main',
              failed: false,
              events: [unverified],
            },
          },
        ]}
      />,
    )
    expect(screen.getByTestId('code-unverified')).toHaveTextContent('Não verificado')
  })

  it('labels the code-mode card as not verified', () => {
    const completed: ConversationActivityEvent = {
      ...unverified,
      eventId: 'evt-done',
      type: 'code_mode.completed',
      codeStage: 'completed',
      verification: undefined,
    }
    render(
      <CodeModeCard
        group={{
          id: 'g',
          kind: 'lifecycle',
          state: 'completed',
          label: 'Modo Code',
          count: 2,
          agentId: 'agent:main',
          failed: false,
          events: [unverified, completed],
        }}
      />,
    )
    expect(screen.getByText('Concluído · não verificado')).toBeInTheDocument()
  })

  it('parses verification from the activity payload', () => {
    const event = parseConversationActivityEvent(
      {
        event_id: 'e1',
        event_type: 'code_mode.unverified',
        agent_id: 'agent:main',
        turn_id: 'turn-1',
        summary: 'Entrega não verificada',
        payload: { verification: 'unverified', changed_paths: 2 },
      },
      'c1',
    )
    expect(event.verification).toBe('unverified')
  })
})
