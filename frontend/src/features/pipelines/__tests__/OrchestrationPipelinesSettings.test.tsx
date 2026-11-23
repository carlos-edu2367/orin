import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { OrchestrationSection } from '../../settings/OrchestrationSection'
import { mockApi } from './fixtures'

type Raw = {
  path: string
  type: 'boolean' | 'number'
  group: string
  label: string
  description: string
  scope: string
  default: unknown
  value: unknown
  layer: string
  user_value: unknown
  has_user_override: boolean
  minimum?: number
  locked: boolean
}

function item(over: Partial<Raw> & Pick<Raw, 'path' | 'type' | 'group' | 'label'>): Raw {
  return { description: '', scope: 'user_only', default: null, value: null, layer: 'default', user_value: null, has_user_override: false, locked: false, ...over }
}

function items(over: Record<string, Partial<Raw>> = {}): Raw[] {
  const base: Raw[] = [
    item({ path: 'pipelines.enabled', type: 'boolean', group: 'pipelines', label: 'Pipelines', scope: 'project_off_only', default: true, value: true, description: 'Permite executar pipelines.' }),
    item({ path: 'pipelines.agentMayStart', type: 'boolean', group: 'pipelines', label: 'Agente pode iniciar pipeline', default: false, value: false }),
    item({ path: 'orchestration.budget.subagent.maxTokens', type: 'number', group: 'budget', label: 'Teto de tokens por subagente', scope: 'project_lower_only', minimum: 1 }),
    item({ path: 'orchestration.budget.subagent.maxCostUsd', type: 'number', group: 'budget', label: 'Teto de custo por subagente (US$)', scope: 'project_lower_only', minimum: 0.01 }),
    item({ path: 'goal.defaultMaxTokens', type: 'number', group: 'goal', label: 'Teto de tokens do /goal', default: 300000, value: 300000, minimum: 1 }),
  ]
  return base.map((entry) => ({ ...entry, ...(over[entry.path] ?? {}) }))
}

function setup(initial: Raw[]) {
  let current = initial
  const api = mockApi({
    'GET /v1/settings/orchestration': () => ({ project_root: '/repo', trusted: true, items: current }),
    'PUT /v1/settings/orchestration': ({ body }) => {
      const changes = (body as { changes: Record<string, unknown> }).changes
      current = current.map((entry) => (entry.path in changes
        ? { ...entry, value: changes[entry.path] ?? entry.default, user_value: changes[entry.path], layer: changes[entry.path] === null ? 'default' : 'user', has_user_override: changes[entry.path] !== null }
        : entry))
      return { project_root: '/repo', trusted: true, items: current }
    },
  })
  render(
    <MemoryRouter>
      <OrchestrationSection client={api.client} />
    </MemoryRouter>,
  )
  return api
}

describe('OrchestrationPipelinesSettings (block "Pipelines" of Orquestração)', () => {
  it('asks the API for the three groups of the Package C keys', async () => {
    const api = setup(items())
    await screen.findByTestId('orchestration-section-pipelines')
    const read = api.calls.find((call) => call.key === 'GET /v1/settings/orchestration')
    const groups = (read?.query.get('group') ?? '').split(',')
    expect(groups).toEqual(expect.arrayContaining(['pipelines', 'budget', 'goal']))
  })

  it('shows the five keys with the layer of their effective value', async () => {
    setup(items({
      'goal.defaultMaxTokens': { value: 500000, layer: 'user', user_value: 500000, has_user_override: true },
      'orchestration.budget.subagent.maxTokens': { value: 800000, layer: 'project' },
    }))
    const block = await screen.findByTestId('orchestration-pipelines-block')
    for (const path of ['pipelines.enabled', 'pipelines.agentMayStart', 'orchestration.budget.subagent.maxTokens', 'orchestration.budget.subagent.maxCostUsd', 'goal.defaultMaxTokens']) {
      expect(within(block).getByTestId(`setting-row-${path}`)).toBeInTheDocument()
    }
    expect(within(block).getByTestId('layer-goal.defaultMaxTokens')).toHaveTextContent('Usuário')
    expect(within(block).getByTestId('layer-orchestration.budget.subagent.maxTokens')).toHaveTextContent('Projeto')
    expect(within(block).getByTestId('layer-pipelines.enabled')).toHaveTextContent('Padrão')
  })

  it('warns about spending next to "agente pode iniciar pipeline"', async () => {
    setup(items())
    expect(await screen.findByTestId('agent-may-start-warning')).toHaveTextContent(/sem você digitar um comando/)
  })

  it('saves a switch to the user layer through PUT /v1/settings/orchestration', async () => {
    const user = userEvent.setup()
    const api = setup(items())
    const row = await screen.findByTestId('setting-row-pipelines.agentMayStart')
    await user.click(within(row).getByRole('switch'))
    await waitFor(() => expect(api.count('PUT /v1/settings/orchestration')).toBe(1))
    expect(api.calls.find((call) => call.key === 'PUT /v1/settings/orchestration')?.body).toMatchObject({ changes: { 'pipelines.agentMayStart': true } })
    expect(await screen.findByText('Configuração salva na camada do usuário.')).toBeInTheDocument()
  })

  it('a budget field takes a number and, when emptied, clears the ceiling (null), never zero', async () => {
    const user = userEvent.setup()
    const api = setup(items({ 'orchestration.budget.subagent.maxTokens': { value: 400000, layer: 'user', user_value: 400000, has_user_override: true } }))
    const row = await screen.findByTestId('setting-row-orchestration.budget.subagent.maxTokens')
    const input = within(row).getByRole('spinbutton')
    expect(input).toHaveValue(400000)
    await user.clear(input)
    await user.click(within(row).getByRole('button', { name: 'Salvar' }))
    await waitFor(() => expect(api.count('PUT /v1/settings/orchestration')).toBe(1))
    expect(api.calls.find((call) => call.key === 'PUT /v1/settings/orchestration')?.body).toMatchObject({ changes: { 'orchestration.budget.subagent.maxTokens': null } })
  })

  it('accepts a decimal cost ceiling', async () => {
    const user = userEvent.setup()
    const api = setup(items())
    const row = await screen.findByTestId('setting-row-orchestration.budget.subagent.maxCostUsd')
    await user.type(within(row).getByRole('spinbutton'), '2.5')
    await user.click(within(row).getByRole('button', { name: 'Salvar' }))
    await waitFor(() => expect(api.count('PUT /v1/settings/orchestration')).toBe(1))
    expect(api.calls.find((call) => call.key === 'PUT /v1/settings/orchestration')?.body).toMatchObject({ changes: { 'orchestration.budget.subagent.maxCostUsd': 2.5 } })
  })

  it('a locked (managed) value cannot be edited', async () => {
    setup(items({ 'goal.defaultMaxTokens': { layer: 'managed', locked: true } }))
    const row = await screen.findByTestId('setting-row-goal.defaultMaxTokens')
    expect(within(row).getByRole('spinbutton')).toBeDisabled()
  })
})
