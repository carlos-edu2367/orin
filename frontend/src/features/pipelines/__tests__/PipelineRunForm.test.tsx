import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { parsePipelineDetail } from '../../../api/pipelines'
import { PipelineRunForm } from '../PipelineRunForm'
import { apiError, mockApi, rawDetail, rawEstimate, reply } from './fixtures'

const STARTED = { run_id: 'run-9', turn_id: 'turn-9', pipeline_id: 'implementar-verificar', state: 'running', inputs: {}, degraded: [], budget: {}, quota: {} }

function setup(options: {
  detail?: Record<string, unknown>
  estimate?: Record<string, unknown> | (() => unknown)
  presets?: Record<string, string>
  extra?: Record<string, () => unknown>
}) {
  const detail = parsePipelineDetail(rawDetail(options.detail))
  const api = mockApi({
    'POST /v1/pipelines/implementar-verificar/estimate': () => (typeof options.estimate === 'function' ? options.estimate() : rawEstimate(options.estimate)),
    'POST /v1/pipeline-runs': () => reply(202, STARTED),
    'POST /v1/pipelines/implementar-verificar/approve': () => ({ pipeline_id: 'implementar-verificar', approved: true }),
    ...Object.fromEntries(Object.entries(options.extra ?? {}).map(([key, value]) => [key, value])),
  })
  const onStarted = vi.fn()
  const view = render(<PipelineRunForm client={api.client} pipeline={detail} conversationId="conv-1" presets={options.presets} onStarted={onStarted} />)
  return { ...api, onStarted, ...view }
}

describe('PipelineRunForm', () => {
  it('renders one field per input type, with the defaults and the preset from /pipeline chave=valor', async () => {
    setup({
      detail: {
        inputs: [
          { name: 'objetivo', type: 'string', required: true, default: null, max_length: 8000, agent_settable: true, whole_command: false },
          { name: 'rodadas', type: 'integer', required: false, default: 2, max_length: null, agent_settable: true, whole_command: false },
          { name: 'rapido', type: 'boolean', required: false, default: true, max_length: null, agent_settable: true, whole_command: false },
          { name: 'revisores', type: 'models', required: false, default: 'auto', max_length: null, agent_settable: true, whole_command: false },
        ],
      },
      presets: { objetivo: 'criar a tela de login' },
    })
    expect(await screen.findByLabelText(/objetivo/)).toHaveValue('criar a tela de login')
    expect(screen.getByLabelText(/rodadas/)).toHaveValue(2)
    expect(screen.getByLabelText(/rapido/)).toBeChecked()
    expect(screen.getByRole('radio', { name: /Automático/ })).toBeChecked()
  })

  it('shows, before spending: the subscriptions involved, the steps, the budget and the maximum parallelism', async () => {
    setup({})
    const before = await screen.findByTestId('estimate-subscriptions')
    expect(within(before).getByText(/claude · claude-sonnet-5-5/)).toBeInTheDocument()
    expect(screen.getByTestId('estimate-parallel')).toHaveTextContent('3 agentes ao mesmo tempo')
    const budget = screen.getByTestId('estimate-budget')
    expect(budget).toHaveTextContent('2.000.000')
    expect(budget).toHaveTextContent('US$ 15,00')
    expect(budget).toHaveTextContent('120 min')
    expect(screen.getByTestId('estimate-steps')).toHaveTextContent('implementar')
    expect(screen.getByTestId('pipeline-before')).toHaveTextContent(/Não é uma estimativa de tokens/)
    // it never promises a number of tokens
    expect(screen.getByTestId('pipeline-before').textContent).not.toMatch(/\d+\s*tokens\s*estimad/i)
  })

  it('"models: auto" lists the resolved models with their subscription and warns about quota', async () => {
    setup({
      detail: { inputs: [{ name: 'revisores', type: 'models', required: false, default: 'auto', max_length: null, agent_settable: true, whole_command: false }] },
      estimate: {
        inputs: { revisores: [{ provider: 'claude', model: 'claude-opus-5-5' }, { provider: 'codex', model: 'gpt-6-luna' }] },
        subscriptions: [{ provider: 'claude', model: 'claude-opus-5-5', steps: ['revisar'] }, { provider: 'codex', model: 'gpt-6-luna', steps: ['revisar'] }],
        quota: { available: true, warn: ['codex'], blocked: [] },
        degraded: [{ reason: 'models_auto_partial', detail: 'só há 2 famílias disponíveis (pedido: 3)', effect: 'a revisão roda com 2 modelos' }],
      },
    })
    const resolved = await screen.findByTestId('resolved-revisores')
    expect(within(resolved).getByText('claude/claude-opus-5-5')).toBeInTheDocument()
    expect(within(resolved).getByText('codex/gpt-6-luna')).toBeInTheDocument()
    expect(screen.getByText('Cota alta')).toBeInTheDocument()
    expect(screen.getByTestId('estimate-degraded')).toHaveTextContent(/só há 2 famílias/)
  })

  it('warns when the permission mode is ask_permissions, and refuses to run in plan mode', async () => {
    const ask = setup({ estimate: { permission_mode: 'ask_permissions' } })
    expect(await screen.findByTestId('estimate-permission')).toHaveTextContent(/pede aprovação a cada ação/)
    ask.unmount()
    setup({ estimate: { permission_mode: 'plan_mode' } })
    expect(await screen.findByText(/Modo Plano, que recusa pipelines/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/objetivo/), 'algo')
    expect(screen.getByRole('button', { name: 'Rodar' })).toBeDisabled()
  })

  it('tells the person that the conversation stays busy and that messages wait in the queue (R3)', async () => {
    setup({})
    expect(await screen.findByTestId('estimate-conversation-busy')).toHaveTextContent(/fica ocupada/)
  })

  it('blocks "Rodar" until the required input is filled, then starts the run with typed inputs', async () => {
    const user = userEvent.setup()
    const { calls, onStarted } = setup({})
    const run = await screen.findByRole('button', { name: 'Rodar' })
    expect(run).toBeDisabled()
    expect(screen.getByText(/Preencha: objetivo/)).toBeInTheDocument()
    await user.type(screen.getByLabelText(/objetivo/), 'migrar para o novo SDK')
    expect(screen.getByRole('button', { name: 'Rodar' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Rodar' }))
    await waitFor(() => expect(onStarted).toHaveBeenCalledTimes(1))
    expect(onStarted.mock.calls[0][0].runId).toBe('run-9')
    const start = calls.find((call) => call.key === 'POST /v1/pipeline-runs')
    expect(start?.body).toEqual({ pipeline_id: 'implementar-verificar', conversation_id: 'conv-1', inputs: { objetivo: 'migrar para o novo SDK', testes: 'pytest -q' } })
  })

  it('a project pipeline that is not approved shows its gate commands and needs an explicit approval before "Rodar"', async () => {
    const user = userEvent.setup()
    const { calls, onStarted } = setup({
      detail: { source: 'project', approval_required: true, approved: false },
      presets: { objetivo: 'x' },
    })
    const approval = await screen.findByTestId('pipeline-approval')
    expect(within(approval).getByTestId('gate-review')).toHaveTextContent('pytest -q')
    expect(within(approval).getByTestId('gate-review')).toHaveTextContent(/limite de 600 s/)
    const run = screen.getByRole('button', { name: 'Aprovar e rodar' })
    expect(run).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: /Revisei os comandos/ }))
    expect(screen.getByRole('button', { name: 'Aprovar e rodar' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Aprovar e rodar' }))
    await waitFor(() => expect(onStarted).toHaveBeenCalled())
    const keys = calls.map((call) => call.key).filter((key) => !key.includes('estimate'))
    expect(keys).toEqual(['POST /v1/pipelines/implementar-verificar/approve', 'POST /v1/pipeline-runs'])
    expect(calls.find((call) => call.key.endsWith('/approve'))?.body).toEqual({ definition_hash: 'a'.repeat(64), conversation_id: 'conv-1' })
  })

  it('explains a refusal in pt-BR and keeps the form', async () => {
    const user = userEvent.setup()
    const { onStarted } = setup({
      presets: { objetivo: 'x' },
      extra: { 'POST /v1/pipeline-runs': () => apiError(409, 'PIPELINE_PLAN_MODE', 'plan') as never },
    })
    await user.click(await screen.findByRole('button', { name: 'Rodar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Modo Plano/)
    expect(onStarted).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Rodar' })).toBeEnabled()
  })

  it('marks an input that is a whole gate command and says the agent never fills it', async () => {
    setup({})
    expect(await screen.findByText(/comando inteiro de um gate/)).toBeInTheDocument()
  })

  it('asks for a conversation when there is none and does not call the estimate', async () => {
    const api = mockApi({})
    render(<PipelineRunForm client={api.client} pipeline={parsePipelineDetail(rawDetail())} conversationId={null} onStarted={vi.fn()} />)
    expect(screen.getByText(/Escolha uma conversa/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rodar' })).toBeDisabled()
    expect(api.fetchImpl).not.toHaveBeenCalled()
  })
})
