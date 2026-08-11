import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ClientEvent } from '../../src/api/types'
import { ExecutionPage } from '../../src/features/executions/ExecutionPage'
import { type ExecutionFixture } from '../../src/features/executions/fixtures'

const fixture = (state: ExecutionFixture['state']): ExecutionFixture => ({
  execution_id: `exec-${state.toLowerCase()}`,
  agent_id: 'agent-orbit',
  state,
  state_version: 4,
  parent_execution_id: null,
  created_at: '2026-08-07T00:00:00.000Z',
  updated_at: '2026-08-07T00:00:01.000Z',
  finished_at: state === 'COMPLETED' || state === 'FAILED' || state === 'CANCELLED' ? '2026-08-07T00:00:03.000Z' : null,
  result: state === 'COMPLETED' ? { display_text: 'A tarefa terminou com segurança.', result_ref: 'opaque-result' } : null,
  failure: state === 'FAILED' ? { code: 'EXECUTION_FAILED' } : null,
})

describe('ExecutionPage', () => {
  it('derives the human working label from RUNNING', () => {
    render(<ExecutionPage execution={fixture('RUNNING')} />)

    expect(screen.getByText('Trabalhando')).toBeInTheDocument()
  })

  it('derives the waiting label from WAITING_USER', () => {
    render(<ExecutionPage execution={fixture('WAITING_USER')} />)

    expect(screen.getByText('Aguardando você')).toBeInTheDocument()
  })

  it('offers an opaque input reference only while WAITING_USER', async () => {
    const { userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    const onInput = vi.fn()
    const { rerender } = render(<ExecutionPage execution={fixture('WAITING_USER')} onInput={onInput} />)

    await user.click(screen.getByRole('button', { name: 'Fornecer referência de entrada' }))
    await user.type(screen.getByLabelText('Referência de entrada'), 'input:waiting-user')
    await user.click(screen.getByRole('button', { name: 'Enviar entrada' }))

    expect(onInput).toHaveBeenCalledWith('input:waiting-user')

    rerender(<ExecutionPage execution={fixture('RUNNING')} onInput={onInput} />)
    expect(screen.queryByRole('button', { name: 'Fornecer referência de entrada' })).not.toBeInTheDocument()
  })

  it.each(['COMPLETED', 'FAILED', 'CANCELLED'] as const)('disables execution controls in %s', (state) => {
    render(<ExecutionPage execution={fixture(state)} />)

    expect(screen.getByRole('button', { name: 'Pausar execução' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancelar execução' })).toBeDisabled()
  })

  it('exposes inspector disclosure semantics', async () => {
    const { userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    render(<ExecutionPage execution={fixture('RUNNING')} />)

    const trigger = screen.getByRole('button', { name: 'Abrir inspector' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Detalhes técnicos')).not.toBeInTheDocument()

    await user.click(trigger)

    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Detalhes técnicos')).toBeInTheDocument()
  })

  it('renders no activity group when no events are supplied', () => {
    render(<ExecutionPage execution={fixture('RUNNING')} />)

    expect(screen.queryByRole('button', { name: /Ferramenta/ })).not.toBeInTheDocument()
  })

  it('renders a compact, expandable tool activity group once a Tool invocation is projected', () => {
    const events: ClientEvent[] = [
      { event_id: 'evt-1', event_type: 'ToolStarted', execution_id: 'exec-running', sequence: 1, occurred_at: '2026-08-07T00:00:01.000Z', payload: { invocation_id: 'inv-1', tool_kind: 'code_exec', state: 'running' } },
      { event_id: 'evt-2', event_type: 'ToolProgressed', execution_id: 'exec-running', sequence: 2, occurred_at: '2026-08-07T00:00:02.000Z', payload: { invocation_id: 'inv-1', progress_kind: 'chunk' } },
      { event_id: 'evt-3', event_type: 'ToolFinished', execution_id: 'exec-running', sequence: 3, occurred_at: '2026-08-07T00:00:03.000Z', payload: { invocation_id: 'inv-1', state: 'succeeded' } },
    ]
    render(<ExecutionPage execution={{ ...fixture('RUNNING'), execution_id: 'exec-running' }} events={events} />)

    expect(screen.getByRole('button', { name: /Ferramenta/ })).toBeInTheDocument()
    expect(screen.getByText('3 ações observadas')).toBeInTheDocument()
  })

  it('does not surface a lifecycle-only projection as an activity group, since level 1 is reserved for Tool', () => {
    const events: ClientEvent[] = [
      { event_id: 'evt-1', event_type: 'ExecutionStarted', execution_id: 'exec-running', sequence: 1, occurred_at: '2026-08-07T00:00:01.000Z', payload: { to_state: 'RUNNING', state_version: 2 } },
    ]
    render(<ExecutionPage execution={{ ...fixture('RUNNING'), execution_id: 'exec-running' }} events={events} />)

    expect(screen.queryByRole('button', { name: /Ferramenta/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/ações observadas/)).not.toBeInTheDocument()
  })

  it('shows the terminal state without inventing text when the result carries only an opaque result_ref', () => {
    render(<ExecutionPage execution={{ ...fixture('COMPLETED'), result: { result_ref: 'opaque-result-only' } }} />)

    expect(screen.getByText('Concluído')).toBeInTheDocument()
    expect(screen.queryByText('RESULTADO AUTORIZADO')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/opaque-result-only/)
  })
})
