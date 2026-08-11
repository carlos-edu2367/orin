import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TurnTimeline } from '../../src/features/conversations/TurnTimeline'
import type { TimelineItem } from '../../src/features/conversations/turnTimelineFold'

describe('TurnTimeline', () => {
  it('renders text and activity items in the order given', () => {
    const items: TimelineItem[] = [
      { id: 'text:1', kind: 'text', content: 'Vou criar o subagente.' },
      {
        id: 'activity:agent:c:sub:agent.created:2',
        kind: 'activity',
        group: {
          id: 'agent:c:sub:agent.created:',
          kind: 'agent',
          state: 'completed',
          label: 'Criou o agente Analista',
          count: 1,
          agentId: 'agent:c:sub',
          failed: false,
          events: [{
            eventId: '2', cursor: 'a.2', type: 'agent.created', kind: 'agent', state: 'completed',
            agentId: 'agent:c:sub', summary: 'Criou o agente Analista', label: 'Analista',
          }],
        },
      },
    ]

    const { container } = render(<TurnTimeline items={items} />)

    expect(screen.getByText('Vou criar o subagente.')).toBeInTheDocument()
    expect(screen.getByText(/Criou o agente/)).toBeInTheDocument()
    const order = container.querySelector('.turn-timeline')!.children
    expect(order[0].className).toBe('turn-timeline__text')
    expect(order[1].className).toBe('turn-timeline__activity')
  })
})
