import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { OrchestrationSection } from '../OrchestrationSection'

type RawSettingItem = {
  path: string
  type: 'boolean' | 'number' | 'string' | 'choice' | 'string_list' | 'map' | 'object'
  group: string
  label: string
  description: string
  scope: 'user_only' | 'project_off_only' | 'project_lower_only' | 'any'
  default: unknown
  value: unknown
  layer: 'default' | 'user' | 'project' | 'local' | 'cli' | 'managed'
  user_value: unknown
  has_user_override: boolean
  minimum?: number | null
  maximum?: number | null
  choices?: string[] | null
  item_type?: string | null
  value_minimum?: number | null
  value_maximum?: number | null
  locked: boolean
}

const DEFAULT_TIERS = {
  'claude-opus-5-5': { family: 'anthropic', tier: 4 },
  'gpt-6.1-sol': { family: 'openai', tier: 4 },
}

function buildInitialItems(overrides?: Partial<Record<string, Partial<RawSettingItem>>>): RawSettingItem[] {
  const base: RawSettingItem[] = [
    {
      path: 'orchestration.verifier.enabled',
      type: 'boolean',
      group: 'verifier',
      label: 'Verificador independente',
      description: 'Executa um agente revisor independente após turnos com alterações acima do limiar configurado.',
      scope: 'project_off_only',
      default: false,
      value: false,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      locked: false,
    },
    {
      path: 'orchestration.selfVerify.enabled',
      type: 'boolean',
      group: 'verifier',
      label: 'Auto-verificação antes de responder',
      description: 'Pede ao próprio agente uma rodada extra de conferência antes de encerrar turnos que alteraram arquivos. Antes da 0.13 esta rodada era automática.',
      scope: 'project_off_only',
      default: false,
      value: false,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      locked: false,
    },
    {
      path: 'orchestration.verifier.includeSubagents',
      type: 'boolean',
      group: 'verifier',
      label: 'Verificar também subagentes',
      description: 'Quando ligado, subagentes de execução que ultrapassarem o limiar também passam pelo verificador independente.',
      scope: 'user_only',
      default: false,
      value: false,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      locked: false,
    },
    {
      path: 'orchestration.verifier.modelPolicy',
      type: 'choice',
      group: 'verifier',
      label: 'Política de modelo do verificador',
      description: 'Define como escolher o modelo do verificador.',
      scope: 'user_only',
      default: 'cross_family',
      value: 'cross_family',
      layer: 'default',
      user_value: null,
      has_user_override: false,
      choices: ['cross_family', 'same_model', 'explicit'],
      locked: false,
    },
    {
      path: 'orchestration.verifier.explicit',
      type: 'object',
      group: 'verifier',
      label: 'Modelo fixo do verificador',
      description: 'Objeto {provider, model, effort?} usado quando a política estiver como explicit.',
      scope: 'user_only',
      default: null,
      value: null,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      item_type: 'verifier_explicit',
      locked: false,
    },
    {
      path: 'orchestration.verifier.effort',
      type: 'choice',
      group: 'verifier',
      label: 'Esforço do verificador',
      description: 'Nível de esforço de raciocínio aplicado ao verificador independente.',
      scope: 'user_only',
      default: 'high',
      value: 'high',
      layer: 'default',
      user_value: null,
      has_user_override: false,
      choices: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
      locked: false,
    },
    {
      path: 'orchestration.verifier.maxRounds',
      type: 'number',
      group: 'verifier',
      label: 'Rodadas de correção',
      description: 'Número máximo de rodadas de correção e nova verificação por turno (1 a 5).',
      scope: 'project_lower_only',
      default: 2,
      value: 2,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      minimum: 1,
      maximum: 5,
      locked: false,
    },
    {
      path: 'orchestration.verifier.threshold.files',
      type: 'number',
      group: 'verifier',
      label: 'Limiar de arquivos alterados',
      description: 'Quantidade mínima de arquivos alterados para acionar o verificador.',
      scope: 'any',
      default: 3,
      value: 3,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      minimum: 1,
      maximum: 1000,
      locked: false,
    },
    {
      path: 'orchestration.verifier.threshold.lines',
      type: 'number',
      group: 'verifier',
      label: 'Limiar de linhas alteradas',
      description: 'Quantidade mínima de linhas alteradas para acionar o verificador.',
      scope: 'any',
      default: 150,
      value: 150,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      minimum: 1,
      maximum: 100000,
      locked: false,
    },
    {
      path: 'orchestration.verifier.threshold.sensitivePaths',
      type: 'string_list',
      group: 'verifier',
      label: 'Caminhos sensíveis',
      description: 'Padrões glob que sempre acionam o verificador.',
      scope: 'any',
      default: ['**/migrations/**'],
      value: ['**/migrations/**'],
      layer: 'default',
      user_value: null,
      has_user_override: false,
      locked: false,
    },
    {
      path: 'orchestration.verifier.threshold.ignore',
      type: 'string_list',
      group: 'verifier',
      label: 'Arquivos ignorados na contagem',
      description: 'Padrões glob excluídos da contagem do limiar.',
      scope: 'any',
      default: ['**/*.lock'],
      value: ['**/*.lock'],
      layer: 'default',
      user_value: null,
      has_user_override: false,
      locked: false,
    },
    {
      path: 'orchestration.modelTiers',
      type: 'map',
      group: 'verifier',
      label: 'Famílias e níveis de modelos',
      description: 'Tabela editável de famílias e níveis por modelo.',
      scope: 'user_only',
      default: DEFAULT_TIERS,
      value: DEFAULT_TIERS,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      item_type: 'model_tier',
      value_minimum: 0,
      value_maximum: 10,
      locked: false,
    },
    {
      path: 'orchestration.agentMessages.parentTool',
      type: 'boolean',
      group: 'messages',
      label: 'Pai pode mandar mensagem a subagente',
      description: 'Permite orientar subagentes em andamento.',
      scope: 'project_off_only',
      default: true,
      value: true,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      locked: false,
    },
    {
      path: 'friend.sessionDelivery',
      type: 'choice',
      group: 'friend',
      label: 'Friend: entrega em sessões em andamento',
      description: 'Define entrega imediata ou fila.',
      scope: 'user_only',
      default: 'now',
      value: 'now',
      layer: 'default',
      user_value: null,
      has_user_override: false,
      choices: ['now', 'queue'],
      locked: false,
    },
  ]

  if (!overrides) return base
  return base.map((item) => (overrides[item.path] ? { ...item, ...overrides[item.path] } : item))
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function createMockClient(initial: RawSettingItem[]) {
  let items = initial
  const putPayloads: Array<Record<string, unknown>> = []
  const requestedUrls: string[] = []

  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    requestedUrls.push(url)
    if (url.includes('/v1/orchestration/verifier/metrics')) {
      return jsonResponse({ pairs: [], since: null })
    }
    if (url.includes('/v1/settings/orchestration')) {
      if (init?.method === 'PUT') {
        const body = JSON.parse(String(init.body ?? '{}')) as { changes: Record<string, unknown> }
        putPayloads.push(body.changes)
        items = items.map((item) => {
          if (!(item.path in body.changes)) return item
          const next = body.changes[item.path]
          if (next === null) {
            return {
              ...item,
              value: item.default,
              user_value: null,
              has_user_override: false,
              layer: 'default',
            }
          }
          return {
            ...item,
            value: next,
            user_value: next,
            has_user_override: true,
            layer: 'user',
          }
        })
      }
      return jsonResponse({ project_root: null, trusted: false, items })
    }
    throw new Error(`Unexpected URL: ${url}`)
  })

  return {
    client: new ApiClient({ fetchImpl: fetchMock }),
    putPayloads,
    requestedUrls,
  }
}

describe('OrchestrationSection — Verificador independente (WS-A7)', () => {
  it('defaults verifier and selfVerify to off, displays cost warning and 0.13 migration note, and preserves messages/friend blocks', async () => {
    const { client, requestedUrls } = createMockClient(buildInitialItems())

    render(
      <MemoryRouter initialEntries={['/settings/orchestration']}>
        <OrchestrationSection client={client} />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Orquestração' })).toBeInTheDocument()
    expect(requestedUrls.some((url) => url.includes('group=messages%2Cfriend%2Cverifier'))).toBe(true)

    const verifierCore = screen.getByTestId('orchestration-section-verifier-core')
    const verifierSwitch = within(verifierCore).getByRole('switch', { name: /Verificador independente/i })
    const selfVerifySwitch = within(verifierCore).getByRole('switch', { name: /Auto-verificação antes de responder/i })

    expect(verifierSwitch).not.toBeChecked()
    expect(selfVerifySwitch).not.toBeChecked()
    expect(screen.getByTestId('verifier-cost-warning')).toHaveTextContent(/outra assinatura/i)
    expect(verifierCore).toHaveTextContent('Antes da 0.13 esta rodada era automática')
    expect(screen.getByTestId('layer-orchestration.verifier.enabled')).toHaveTextContent('Padrão')
    expect(screen.getByTestId('layer-orchestration.selfVerify.enabled')).toHaveTextContent('Padrão')

    // Preserves existing messages, friend, and adds verifier metrics block
    expect(screen.getByTestId('orchestration-section-messages')).toBeInTheDocument()
    expect(screen.getByTestId('orchestration-section-friend')).toBeInTheDocument()
    expect(screen.getByTestId('orchestration-section-verifier-metrics')).toBeInTheDocument()
    expect(await screen.findByTestId('verifier-metrics-empty')).toBeInTheDocument()
  })

  it('saves every verifier setting key and updates layer badges from Padrão to Usuário', async () => {
    const user = userEvent.setup()
    const { client, putPayloads } = createMockClient(buildInitialItems())

    render(
      <MemoryRouter initialEntries={['/settings/orchestration']}>
        <OrchestrationSection client={client} />
      </MemoryRouter>,
    )

    expect(await screen.findByTestId('orchestration-section-verifier')).toBeInTheDocument()

    // Core toggles
    await user.click(screen.getByRole('switch', { name: /Verificador independente/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.verifier.enabled': true })
    })
    expect(screen.getByTestId('layer-orchestration.verifier.enabled')).toHaveTextContent('Usuário')

    await user.click(screen.getByRole('switch', { name: /Auto-verificação antes de responder/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.selfVerify.enabled': true })
    })
    expect(screen.getByTestId('layer-orchestration.selfVerify.enabled')).toHaveTextContent('Usuário')

    await user.click(screen.getByRole('switch', { name: /Verificar também subagentes/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.verifier.includeSubagents': true })
    })

    // Policy, explicit model, effort, maxRounds
    const policySelect = screen.getByRole('combobox', { name: /Política de modelo do verificador/i })
    await user.selectOptions(policySelect, 'explicit')
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.verifier.modelPolicy': 'explicit' })
    })
    expect(screen.getByTestId('layer-orchestration.verifier.modelPolicy')).toHaveTextContent('Usuário')

    const explicitRow = document.querySelector('[data-setting-path="orchestration.verifier.explicit"]') as HTMLElement
    await user.type(within(explicitRow).getByPlaceholderText(/openai-codex/i), 'openai-codex')
    await user.type(within(explicitRow).getByPlaceholderText(/gpt-6\.1-sol/i), 'gpt-6.1-sol')
    await user.selectOptions(within(explicitRow).getByRole('combobox'), 'xhigh')
    await user.click(within(explicitRow).getByRole('button', { name: /Salvar modelo fixo/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({
        'orchestration.verifier.explicit': {
          provider: 'openai-codex',
          model: 'gpt-6.1-sol',
          effort: 'xhigh',
        },
      })
    })
    expect(screen.getByTestId('layer-orchestration.verifier.explicit')).toHaveTextContent('Usuário')

    await user.selectOptions(screen.getByRole('combobox', { name: /Esforço do verificador/i }), 'max')
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.verifier.effort': 'max' })
    })

    const roundsRow = document.querySelector('[data-setting-path="orchestration.verifier.maxRounds"]') as HTMLElement
    const roundsInput = within(roundsRow).getByRole('spinbutton')
    await user.clear(roundsInput)
    await user.type(roundsInput, '4')
    await user.click(within(roundsRow).getByRole('button', { name: /^Salvar$/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.verifier.maxRounds': 4 })
    })

    // Thresholds: files, lines, sensitivePaths, ignore
    const filesRow = document.querySelector('[data-setting-path="orchestration.verifier.threshold.files"]') as HTMLElement
    const filesInput = within(filesRow).getByRole('spinbutton')
    await user.clear(filesInput)
    await user.type(filesInput, '5')
    await user.click(within(filesRow).getByRole('button', { name: /^Salvar$/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.verifier.threshold.files': 5 })
    })

    const linesRow = document.querySelector('[data-setting-path="orchestration.verifier.threshold.lines"]') as HTMLElement
    const linesInput = within(linesRow).getByRole('spinbutton')
    await user.clear(linesInput)
    await user.type(linesInput, '250')
    await user.click(within(linesRow).getByRole('button', { name: /^Salvar$/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.verifier.threshold.lines': 250 })
    })

    const sensitiveRow = document.querySelector('[data-setting-path="orchestration.verifier.threshold.sensitivePaths"]') as HTMLElement
    await user.type(within(sensitiveRow).getByPlaceholderText(/migrations/i), '**/billing/**')
    await user.click(within(sensitiveRow).getByRole('button', { name: /Adicionar/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({
        'orchestration.verifier.threshold.sensitivePaths': ['**/migrations/**', '**/billing/**'],
      })
    })

    const ignoreRow = document.querySelector('[data-setting-path="orchestration.verifier.threshold.ignore"]') as HTMLElement
    await user.click(within(ignoreRow).getByRole('button', { name: /Remover padrão \*\*\/\*\.lock/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({
        'orchestration.verifier.threshold.ignore': [],
      })
    })
  })

  it('edits, adds, removes, and resets orchestration.modelTiers to default', async () => {
    const user = userEvent.setup()
    const { client, putPayloads } = createMockClient(buildInitialItems())

    render(
      <MemoryRouter initialEntries={['/settings/orchestration']}>
        <OrchestrationSection client={client} />
      </MemoryRouter>,
    )

    const tiersSection = await screen.findByTestId('orchestration-section-model-tiers')

    // Edit existing model tier
    const opusTierInput = within(tiersSection).getByLabelText('Nível de claude-opus-5-5')
    await user.clear(opusTierInput)
    await user.type(opusTierInput, '5')
    await user.click(within(tiersSection).getByRole('button', { name: 'Salvar claude-opus-5-5' }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({
        'orchestration.modelTiers': {
          'claude-opus-5-5': { family: 'anthropic', tier: 5 },
          'gpt-6.1-sol': { family: 'openai', tier: 4 },
        },
      })
    })
    expect(screen.getByTestId('layer-orchestration.modelTiers')).toHaveTextContent('Usuário')

    // Add new model entry
    await user.type(within(tiersSection).getByLabelText('Novo modelo'), 'gemini-4-neon')
    await user.type(within(tiersSection).getByLabelText('Família do novo modelo'), 'google')
    const newTierInput = within(tiersSection).getByLabelText('Nível do novo modelo')
    await user.clear(newTierInput)
    await user.type(newTierInput, '4')
    await user.click(within(tiersSection).getByRole('button', { name: /Adicionar modelo/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({
        'orchestration.modelTiers': {
          'claude-opus-5-5': { family: 'anthropic', tier: 5 },
          'gpt-6.1-sol': { family: 'openai', tier: 4 },
          'gemini-4-neon': { family: 'google', tier: 4 },
        },
      })
    })

    // Remove model entry
    await user.click(within(tiersSection).getByRole('button', { name: 'Remover gpt-6.1-sol' }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({
        'orchestration.modelTiers': {
          'claude-opus-5-5': { family: 'anthropic', tier: 5 },
          'gemini-4-neon': { family: 'google', tier: 4 },
        },
      })
    })

    // Reset back to default via null override
    await user.click(within(tiersSection).getByRole('button', { name: /Restaurar padrão/i }))
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.modelTiers': null })
    })
    expect(screen.getByTestId('layer-orchestration.modelTiers')).toHaveTextContent('Padrão')
    expect(within(tiersSection).getByLabelText('Nível de claude-opus-5-5')).toHaveValue(4)
  })

  it('locks controls managed by enterprise/managed layer', async () => {
    const { client } = createMockClient(
      buildInitialItems({
        'orchestration.verifier.enabled': {
          value: true,
          layer: 'managed',
          locked: true,
        },
      }),
    )

    render(
      <MemoryRouter initialEntries={['/settings/orchestration']}>
        <OrchestrationSection client={client} />
      </MemoryRouter>,
    )

    const verifierSwitch = await screen.findByRole('switch', { name: /Verificador independente/i })
    expect(verifierSwitch).toBeDisabled()
    expect(screen.getByTestId('layer-orchestration.verifier.enabled')).toHaveTextContent('Gerenciado')
  })
})
