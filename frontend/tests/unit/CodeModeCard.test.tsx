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

it('distinguishes a recoverable provider failure from work that is still running', () => {
  render(<ActivityStream events={[
    event(1, 'code_mode.activated', 'planning', 'Modo Code ativado'),
    { ...event(2, 'code_mode.blocked', 'blocked', 'Modo Code bloqueado'), errorCode: 'PROVIDER_STREAM_FAILED' },
  ]} />)

  const card = screen.getByLabelText('Progresso do Modo Code')
  expect(card).toHaveTextContent('Falha temporária')
  expect(card).toHaveTextContent('Não foi possível alcançar o provedor')
  expect(card).not.toHaveTextContent('Em andamento')
})

it('renders the live todo_write checklist inside CodeModeCard with activeForm for in_progress items', () => {
  render(<ActivityStream events={[
    event(1, 'code_mode.activated', 'planning', 'Modo Code ativado'),
    {
      ...event(2, 'code_mode.stage_changed', 'implementing', 'Lista de tarefas (1/3 concluídas)'),
      todos: [
        { id: '1', content: 'Inspecionar arquitetura', status: 'completed', activeForm: 'Inspecionando arquitetura' },
        { id: '2', content: 'Implementar Fase C', status: 'in_progress', activeForm: 'Implementando Fase C' },
        { id: '3', content: 'Rodar suíte de testes', status: 'pending', activeForm: 'Rodando suíte de testes' },
      ],
    },
  ]} />)

  const card = screen.getByLabelText('Progresso do Modo Code')
  expect(card).toHaveTextContent('Lista de tarefas (1/3 concluídas)')
  expect(card).toHaveTextContent('Inspecionar arquitetura')
  expect(card).toHaveTextContent('Implementando Fase C')
  expect(card).toHaveTextContent('Rodar suíte de testes')
})
