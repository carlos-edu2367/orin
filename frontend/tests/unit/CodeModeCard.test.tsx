import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { ActivityStream } from '../../src/features/conversations/ActivityStream'
import { kindFor, stateFor, type ConversationActivityEvent } from '../../src/features/conversations/activityTypes'

function event(index: number, type: string, stage: string, summary: string): ConversationActivityEvent {
  return {
    eventId: `code-${index}`,
    cursor: `a.${index}`,
    type,
    kind: kindFor(type),
    state: stateFor(type),
    agentId: 'agent:main',
    turnId: 'turn-code',
    codeStage: stage,
    summary,
  }
}

it('renders one persistent Code mode card as later lifecycle events arrive', () => {
  const initial = [event(1, 'code_mode.activated', 'planning', 'Modo Code ativado')]
  const view = render(<ActivityStream events={initial} />)
  const card = screen.getByLabelText('Progresso do Modo Code')

  view.rerender(<ActivityStream events={[
    ...initial,
    event(2, 'code_mode.plan_ready', 'planning', 'Plano pronto'),
    event(3, 'code_mode.validation_started', 'validating', 'Executando validação'),
  ]} />)

  expect(screen.getAllByLabelText('Progresso do Modo Code')).toHaveLength(1)
  expect(screen.getByLabelText('Progresso do Modo Code')).toBe(card)
  expect(card).toHaveTextContent('Executando validação')
})
