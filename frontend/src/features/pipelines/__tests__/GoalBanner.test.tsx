import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { GoalBanner } from '../GoalBanner'

function goal(over: Record<string, unknown> = {}) {
  return {
    run_id: 'run-goal',
    state: 'running',
    stop_reason: null,
    objective: 'Migrar o módulo de pagamentos para o novo SDK',
    max_tokens: 300000,
    used_tokens: 112000,
    window_used_tokens: 112000,
    remaining_tokens: 188000,
    turns: 3,
    window_turns: 3,
    max_turns: 50,
    last_evaluation: { turn_index: 2, done: false, reason: 'Os testes de reembolso ainda não passam', model: 'claude-haiku-5-5', fallback: false },
    evaluator_model: 'claude-haiku-5-5',
    resumable: false,
    started_at: '2026-11-23T10:00:00Z',
    finished_at: null,
    ...over,
  }
}

function harness(initial: Record<string, unknown> | null, after: Record<string, unknown> | null = initial) {
  let current = initial
  const calls: string[] = []
  const bodies: unknown[] = []
  const fetchImpl = vi.fn(async (url: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => {
    const method = init?.method ?? 'GET'
    const path = String(url).replace('http://localhost', '')
    calls.push(`${method} ${path}`)
    if (init?.body) bodies.push(JSON.parse(String(init.body)))
    if (method !== 'GET') current = after
    return new Response(JSON.stringify({ goal: current }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl: fetchImpl as unknown as typeof fetch })
  return { client, calls, bodies, fetchImpl }
}

describe('GoalBanner', () => {
  it('shows the objective, the tokens used and left, the evaluator model and the last reason', async () => {
    const { client } = harness(goal())
    render(<GoalBanner client={client} conversationId="conv-1" refreshKey="a" />)
    expect(await screen.findByTestId('goal-banner')).toHaveAttribute('data-state', 'running')
    expect(screen.getByText(/Migrar o módulo de pagamentos/)).toBeInTheDocument()
    expect(screen.getByTestId('goal-tokens')).toHaveTextContent('112.000')
    expect(screen.getByTestId('goal-tokens')).toHaveTextContent('300.000')
    expect(screen.getByTestId('goal-tokens')).toHaveTextContent('188.000')
    expect(screen.getByTestId('goal-evaluator')).toHaveTextContent('claude-haiku-5-5')
    expect(screen.getByTestId('goal-evaluator')).not.toHaveTextContent(/esforço baixo/)
    expect(screen.getByTestId('goal-reason')).toHaveTextContent('Os testes de reembolso ainda não passam')
    expect(screen.getByRole('button', { name: 'Limpar' })).toBeEnabled()
  })

  it('says when the evaluator is the turn model at low effort (fallback), because that costs more', async () => {
    const { client } = harness(goal({ last_evaluation: { turn_index: 1, done: false, reason: 'ok', model: 'claude-opus-5-5', fallback: true }, evaluator_model: 'claude-opus-5-5' }))
    render(<GoalBanner client={client} conversationId="conv-1" refreshKey="a" />)
    expect(await screen.findByTestId('goal-evaluator')).toHaveTextContent(/modelo do turno em esforço baixo/)
  })

  it('clears the objective and disappears', async () => {
    const user = userEvent.setup()
    const { client, calls, bodies } = harness(goal(), goal({ state: 'cancelled', stop_reason: 'cleared' }))
    render(<GoalBanner client={client} conversationId="conv-1" refreshKey="a" />)
    await user.click(await screen.findByRole('button', { name: 'Limpar' }))
    await waitFor(() => expect(screen.queryByTestId('goal-banner')).not.toBeInTheDocument())
    expect(calls).toContain('POST /v1/conversations/conv-1/goal/clear')
    expect(bodies[0]).toEqual({ cancel_turn: false })
  })

  it('"Limpar e parar o turno" also cancels the turn in progress', async () => {
    const user = userEvent.setup()
    const { client, bodies } = harness(goal(), null)
    render(<GoalBanner client={client} conversationId="conv-1" refreshKey="a" />)
    await user.click(await screen.findByRole('button', { name: 'Limpar e parar o turno' }))
    await waitFor(() => expect(screen.queryByTestId('goal-banner')).not.toBeInTheDocument())
    expect(bodies[0]).toEqual({ cancel_turn: true })
  })

  it('offers "Retomar" with a new cap when the objective stopped on its token ceiling', async () => {
    const user = userEvent.setup()
    const stopped = goal({ state: 'budget_exceeded', stop_reason: 'budget', resumable: true, remaining_tokens: 0, window_used_tokens: 300000 })
    const { client, calls, bodies } = harness(stopped, goal())
    render(<GoalBanner client={client} conversationId="conv-1" refreshKey="a" />)
    expect(await screen.findByTestId('goal-banner')).toHaveAttribute('data-state', 'budget_exceeded')
    expect(screen.getByText(/o orçamento acabou/)).toBeInTheDocument()
    const cap = screen.getByLabelText('Novo teto de tokens')
    // One change event, not key by key: under a loaded full suite the per-key typing raced the cap prefill.
    fireEvent.change(cap, { target: { value: '600000' } })
    expect(cap).toHaveValue(600000)
    await user.click(screen.getByRole('button', { name: 'Retomar' }))
    await waitFor(() => expect(calls).toContain('POST /v1/conversations/conv-1/goal/resume'), { timeout: 5000 })
    expect(bodies[0]).toEqual({ max_tokens: 600000 })
    await waitFor(() => expect(screen.getByTestId('goal-banner')).toHaveAttribute('data-state', 'running'), { timeout: 5000 })
  })

  it('does not offer "Retomar" for an objective that cannot be resumed', async () => {
    const { client } = harness(goal({ state: 'failed', stop_reason: 'max_turns', resumable: false }))
    render(<GoalBanner client={client} conversationId="conv-1" refreshKey="a" />)
    await screen.findByTestId('goal-banner')
    expect(screen.queryByRole('button', { name: 'Retomar' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Limpar' })).toBeInTheDocument()
  })

  it('when the evaluator says it is done, shows its reason and "Continuar mesmo assim" starts the objective again', async () => {
    const user = userEvent.setup()
    const done = goal({
      state: 'succeeded', stop_reason: 'done', resumable: false,
      last_evaluation: { turn_index: 4, done: true, reason: 'Todos os testes passam', model: 'claude-haiku-5-5', fallback: false },
    })
    const { client, calls, bodies } = harness(done, goal())
    render(<GoalBanner client={client} conversationId="conv-1" refreshKey="a" />)
    expect(await screen.findByTestId('goal-banner')).toHaveAttribute('data-state', 'succeeded')
    expect(screen.getByTestId('goal-reason')).toHaveTextContent('Todos os testes passam')
    await user.click(screen.getByRole('button', { name: 'Continuar mesmo assim' }))
    await waitFor(() => expect(calls).toContain('POST /v1/conversations/conv-1/goal'))
    expect(bodies[0]).toEqual({ objective: 'Migrar o módulo de pagamentos para o novo SDK', max_tokens: 300000 })
  })

  it('disappears when the person dismisses a finished objective', async () => {
    const user = userEvent.setup()
    const { client } = harness(goal({ state: 'succeeded', stop_reason: 'done' }))
    render(<GoalBanner client={client} conversationId="conv-1" refreshKey="a" />)
    await user.click(await screen.findByRole('button', { name: 'Dispensar' }))
    expect(screen.queryByTestId('goal-banner')).not.toBeInTheDocument()
  })

  it('renders nothing when there is no objective, and reads again when the refresh key changes', async () => {
    const { client, fetchImpl } = harness(null)
    const { rerender } = render(<GoalBanner client={client} conversationId="conv-1" refreshKey="a" />)
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId('goal-banner')).not.toBeInTheDocument()
    rerender(<GoalBanner client={client} conversationId="conv-1" refreshKey="b" />)
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2))
  })
})
