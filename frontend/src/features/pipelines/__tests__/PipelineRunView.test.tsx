import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { PipelineRunView } from '../PipelineRunView'
import { apiError, mockApi, rawDetail, rawEstimate, rawRun, rawStep, reply } from './fixtures'

function renderView(api: ReturnType<typeof mockApi>) {
  return render(
    <MemoryRouter>
      <PipelineRunView client={api.client} runId="run-1" />
    </MemoryRouter>,
  )
}

function routes(run: () => unknown, extra: Record<string, () => unknown> = {}) {
  return {
    'GET /v1/pipeline-runs/run-1': run,
    'GET /v1/pipelines/implementar-verificar': () => rawDetail(),
    'POST /v1/pipelines/implementar-verificar/estimate': () => rawEstimate(),
    ...extra,
  }
}

describe('PipelineRunView', () => {
  it('lists the steps in the order of the definition with state, tokens and cost', async () => {
    const api = mockApi(routes(() => rawRun()))
    renderView(api)
    const plano = await screen.findByTestId('run-step-plano')
    expect(plano).toHaveAttribute('data-state', 'succeeded')
    expect(plano).toHaveTextContent('Plano')
    expect(plano).toHaveTextContent('1.200 tokens')
    expect(plano).toHaveTextContent('US$ 0,50')
    const order = screen.getAllByTestId(/^run-step-(?!detail)/).map((node) => node.getAttribute('data-testid'))
    expect(order).toEqual(['run-step-plano', 'run-step-implementar', 'run-step-verificar', 'run-step-corrigir'])
    expect(screen.getByTestId('run-state')).toHaveTextContent('Em execução')
  })

  it('expands the items of a map step with the agent of each, and an unknown cost reads "sem preço"', async () => {
    const user = userEvent.setup()
    const api = mockApi(routes(() => rawRun()))
    renderView(api)
    const step = await screen.findByTestId('run-step-implementar')
    expect(step).toHaveTextContent('1 de 2 itens')
    // one item has no price: the step total is partial, never a clean number and never zero
    expect(step).toHaveTextContent('US$ 0,01 (parcial)')
    expect(step).not.toHaveTextContent('US$ 0,00')
    await user.click(within(step).getByText(/Itens e tentativas/))
    expect(within(step).getByText('sem preço')).toBeInTheDocument()
    expect(within(step).getByText('agent-2')).toBeInTheDocument()
    expect(within(step).getByText('agent-3')).toBeInTheDocument()
    expect(within(step).getByText('item 000')).toBeInTheDocument()
  })

  it('shows a loop as "volta 1 de 2" and the attempts', async () => {
    const run = rawRun({
      steps: [
        rawStep({ step_id: 'verificar', state: 'gate_failed', loop_count: 0, agent_id: null, input_tokens: 0, output_tokens: 0, cost_usd: null, has_output: false }),
        rawStep({ step_id: 'corrigir', loop_count: 0 }),
        rawStep({ step_id: 'verificar', state: 'succeeded', loop_count: 1, attempt: 2, agent_id: null, input_tokens: 0, output_tokens: 0, cost_usd: null, has_output: false }),
      ],
    })
    const api = mockApi(routes(() => run))
    renderView(api)
    expect(await screen.findByTestId('run-step-detail-verificar')).toHaveTextContent('volta 1 de 2 · 2 tentativas')
    expect(screen.getByTestId('run-step-verificar')).toHaveAttribute('data-state', 'succeeded')
  })

  it('a running run offers Pausar and Cancelar, and posts them', async () => {
    const user = userEvent.setup()
    const api = mockApi(routes(() => rawRun(), {
      'POST /v1/pipeline-runs/run-1/pause': () => reply(202, { run_id: 'run-1', pause_requested: true, where: 'worker' }),
      'POST /v1/pipeline-runs/run-1/cancel': () => reply(202, { run_id: 'run-1', cancel_requested: true, where: 'worker' }),
    }))
    renderView(api)
    expect(screen.queryByRole('button', { name: 'Retomar' })).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Pausar' }))
    await waitFor(() => expect(api.count('POST /v1/pipeline-runs/run-1/pause')).toBe(1))
    await user.click(screen.getByRole('button', { name: 'Cancelar' }))
    await waitFor(() => expect(api.count('POST /v1/pipeline-runs/run-1/cancel')).toBe(1))
    expect(screen.getByTestId('run-busy-note')).toHaveTextContent(/entra na fila/)
  })

  it('an interrupted run offers Retomar (and no Pausar), with the option to run the gates again', async () => {
    const user = userEvent.setup()
    const api = mockApi(routes(() => rawRun({ state: 'interrupted', stop_reason: 'paused', resumable: true }), {
      'POST /v1/pipeline-runs/run-1/resume': () => reply(202, { run_id: 'run-1', turn_id: 't2', resumed: true, definition: 'same' }),
    }))
    renderView(api)
    expect(await screen.findByText(/pausada pela pessoa/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pausar' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: /Rodar os gates de novo/ }))
    await user.click(screen.getByRole('button', { name: 'Retomar' }))
    await waitFor(() => expect(api.count('POST /v1/pipeline-runs/run-1/resume')).toBe(1))
    expect(api.calls.find((call) => call.key.endsWith('/resume'))?.body).toEqual({ rerun_gates: true, budget_override: false })
  })

  it('when the definition changed, asks how to continue and resumes with the chosen option', async () => {
    const user = userEvent.setup()
    let attempts = 0
    const api = mockApi(routes(() => rawRun({ state: 'failed', resumable: true }), {
      'POST /v1/pipeline-runs/run-1/resume': () => {
        attempts += 1
        return attempts === 1
          ? apiError(409, 'PIPELINE_DEFINITION_CHANGED', 'mudou', {
            options: [{ id: 'original', label: 'Retomar com a definição original (a do início da execução)' }, { id: 'new_run', label: 'Iniciar uma nova execução com a definição atual' }],
            stored_hash: 'a'.repeat(64), current_hash: 'b'.repeat(64),
          })
          : reply(202, { run_id: 'run-1', turn_id: 't2', resumed: true, definition: 'original' })
      },
    }))
    renderView(api)
    await user.click(await screen.findByRole('button', { name: 'Retomar' }))
    const dialog = await screen.findByTestId('definition-changed')
    expect(dialog).toHaveTextContent('A definição do pipeline mudou')
    expect(within(dialog).getByRole('radio', { name: /definição original/ })).toBeChecked()
    expect(within(dialog).getByRole('radio', { name: /nova execução/ })).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Continuar' }))
    await waitFor(() => expect(api.count('POST /v1/pipeline-runs/run-1/resume')).toBe(2))
    expect(api.calls.filter((call) => call.key.endsWith('/resume'))[1].body).toMatchObject({ definition: 'original' })
    await waitFor(() => expect(screen.queryByTestId('definition-changed')).not.toBeInTheDocument())
  })

  it('a run that ran out of budget asks for confirmation and resumes with budget_override', async () => {
    const user = userEvent.setup()
    const api = mockApi(routes(() => rawRun({ state: 'budget_exceeded', stop_reason: 'budget', resumable: true, needs_budget_override: true, used_tokens: 2000000 }), {
      'POST /v1/pipeline-runs/run-1/resume': () => reply(202, { run_id: 'run-1', turn_id: 't2', resumed: true, definition: 'same' }),
    }))
    renderView(api)
    expect(screen.queryByRole('button', { name: 'Retomar' })).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Continuar com novo orçamento' }))
    const panel = screen.getByTestId('budget-override')
    expect(panel).toHaveTextContent('zera o contador de gasto')
    const confirm = within(panel).getByRole('button', { name: 'Continuar com novo teto' })
    expect(confirm).toBeDisabled()
    await user.click(within(panel).getByRole('checkbox', { name: /gasta mais assinatura/ }))
    await user.click(confirm)
    await waitFor(() => expect(api.count('POST /v1/pipeline-runs/run-1/resume')).toBe(1))
    expect(api.calls.find((call) => call.key.endsWith('/resume'))?.body).toMatchObject({ budget_override: true })
  })

  it('opens the output of a step by reference, as text', async () => {
    const user = userEvent.setup()
    const api = mockApi(routes(() => rawRun(), {
      'GET /v1/pipeline-runs/run-1/steps/plano/output': () => ({ step_id: 'plano', item_key: '', attempt: 1, state: 'succeeded', bytes: 30, output: { tarefas: ['<b>x</b>'] }, truncated: false }),
    }))
    renderView(api)
    const step = await screen.findByTestId('run-step-plano')
    await user.click(within(step).getByRole('button', { name: 'Ver saída' }))
    const panel = await screen.findByTestId('step-output')
    await waitFor(() => expect(panel).toHaveTextContent('"tarefas"'))
    // model text is shown as text, never as markup
    expect(panel.querySelector('b')).toBeNull()
    expect(panel).toHaveTextContent('<b>x</b>')
  })

  it('shows usage by subscription and by agent, and the ceiling when it is known', async () => {
    const api = mockApi(routes(() => rawRun()))
    renderView(api)
    const totals = await screen.findByTestId('run-totals')
    await waitFor(() => expect(totals).toHaveTextContent('de 2.000.000'))
    expect(totals).toHaveTextContent('5.000')
    expect(screen.getByRole('region', { name: 'Uso por agente e por assinatura' })).toHaveTextContent('claude')
    expect(screen.getByRole('region', { name: 'Uso por agente e por assinatura' })).toHaveTextContent('agent-2')
  })

  it('says so when the run does not exist', async () => {
    const api = mockApi({ 'GET /v1/pipeline-runs/run-1': () => apiError(404, 'PIPELINE_RUN_NOT_FOUND', 'x') as never })
    renderView(api)
    expect(await screen.findByRole('alert')).toHaveTextContent(/não existe/)
  })
})
