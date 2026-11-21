import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import type { OrchestrationSettingItem } from '../../../api/orchestrationSettings'
import { OrchestrationSettingsForm } from '../OrchestrationSettingsForm'
import { QuotaAndEffortSection } from '../QuotaAndEffortSection'

function sampleItems(): OrchestrationSettingItem[] {
  return [
    {
      path: 'quota.display.enabled',
      type: 'boolean',
      group: 'quota',
      label: 'Mostrar cota no compositor',
      description: 'Exibe o indicador de uso da assinatura.',
      scope: 'user_only',
      defaultValue: true,
      value: true,
      layer: 'default',
      userValue: null,
      hasUserOverride: false,
      minimum: null,
      maximum: null,
      choices: null,
      itemType: null,
      valueMinimum: null,
      valueMaximum: null,
      locked: false,
    },
    {
      path: 'quota.policy.warnPercent',
      type: 'number',
      group: 'quota',
      label: 'Percentual de aviso de cota',
      description: 'Percentual de 1 a 100.',
      scope: 'user_only',
      defaultValue: 80,
      value: 75,
      layer: 'user',
      userValue: 75,
      hasUserOverride: true,
      minimum: 1,
      maximum: 100,
      choices: null,
      itemType: null,
      valueMinimum: null,
      valueMaximum: null,
      locked: false,
    },
    {
      path: 'effort.subagentPolicy',
      type: 'choice',
      group: 'effort',
      label: 'Esforço dos subagentes',
      description: 'Política de esforço para subagentes.',
      scope: 'user_only',
      defaultValue: 'model_default',
      value: 'model_default',
      layer: 'default',
      userValue: null,
      hasUserOverride: false,
      minimum: null,
      maximum: null,
      choices: ['model_default', 'inherit', 'by_type'],
      itemType: null,
      valueMinimum: null,
      valueMaximum: null,
      locked: false,
    },
    {
      path: 'quota.fallback.chain',
      type: 'string_list',
      group: 'quota',
      label: 'Cadeia de fallback por cota',
      description: 'Modelos usados em ordem.',
      scope: 'user_only',
      defaultValue: [],
      value: ['openai-codex/gpt-5'],
      layer: 'user',
      userValue: ['openai-codex/gpt-5'],
      hasUserOverride: true,
      minimum: null,
      maximum: null,
      choices: null,
      itemType: 'string',
      valueMinimum: null,
      valueMaximum: null,
      locked: false,
    },
    {
      path: 'context.windowOverrides',
      type: 'map',
      group: 'context',
      label: 'Janela de contexto por modelo',
      description: 'Override manual em tokens.',
      scope: 'project_lower_only',
      defaultValue: {},
      value: { 'anthropic-cli/claude-opus-5-5': 200000 },
      layer: 'project',
      userValue: null,
      hasUserOverride: false,
      minimum: null,
      maximum: null,
      choices: null,
      itemType: 'number',
      valueMinimum: 50000,
      valueMaximum: 1000000,
      locked: false,
    },
    {
      path: 'quota.policy.blockBackgroundPercent',
      type: 'number',
      group: 'quota',
      label: 'Percentual para bloquear trabalho em fundo',
      description: 'Gerenciado pela organização.',
      scope: 'user_only',
      defaultValue: 90,
      value: 60,
      layer: 'managed',
      userValue: 85,
      hasUserOverride: true,
      minimum: 1,
      maximum: 100,
      choices: null,
      itemType: null,
      valueMinimum: null,
      valueMaximum: null,
      locked: true,
    },
  ]
}

describe('OrchestrationSettingsForm', () => {
  it('renders controls by type, shows origin layers, saves edits, resets overrides and disables managed fields', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockResolvedValue(undefined)

    render(<OrchestrationSettingsForm items={sampleItems()} onSave={onSave} />)

    // Layer badges
    expect(screen.getAllByText('Padrão').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('Usuário').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('Projeto')).toBeInTheDocument()
    expect(screen.getByText('Gerenciado')).toBeInTheDocument()

    // Boolean toggle
    const toggle = screen.getByRole('switch', { name: 'Mostrar cota no compositor' })
    expect(toggle).toBeChecked()
    await user.click(toggle)
    expect(onSave).toHaveBeenCalledWith({ 'quota.display.enabled': false })

    // Choice select
    const select = screen.getByLabelText('Esforço dos subagentes')
    await user.selectOptions(select, 'inherit')
    expect(onSave).toHaveBeenCalledWith({ 'effort.subagentPolicy': 'inherit' })

    // Number input + save + reset default
    const warnField = screen.getByTestId('setting-row-quota.policy.warnPercent')
    const warnInput = within(warnField).getByLabelText('Percentual de aviso de cota')
    await user.clear(warnInput)
    await user.type(warnInput, '85')
    await user.click(within(warnField).getByRole('button', { name: 'Salvar' }))
    expect(onSave).toHaveBeenCalledWith({ 'quota.policy.warnPercent': 85 })

    await user.click(within(warnField).getByRole('button', { name: 'Restaurar padrão' }))
    expect(onSave).toHaveBeenCalledWith({ 'quota.policy.warnPercent': null })

    // String list add
    const chainField = screen.getByTestId('setting-row-quota.fallback.chain')
    const addInput = within(chainField).getByLabelText('Adicionar item em Cadeia de fallback por cota')
    await user.type(addInput, 'anthropic-cli/claude-sonnet-5-5')
    await user.click(within(chainField).getByRole('button', { name: 'Adicionar' }))
    expect(onSave).toHaveBeenCalledWith({
      'quota.fallback.chain': ['openai-codex/gpt-5', 'anthropic-cli/claude-sonnet-5-5'],
    })

    // Map add entry
    const mapField = screen.getByTestId('setting-row-context.windowOverrides')
    const keyInput = within(mapField).getByLabelText('Nova chave em Janela de contexto por modelo')
    const valInput = within(mapField).getByLabelText('Novo valor em Janela de contexto por modelo')
    await user.type(keyInput, 'openai-codex/gpt-5')
    await user.type(valInput, '180000')
    await user.click(within(mapField).getByRole('button', { name: 'Adicionar' }))
    expect(onSave).toHaveBeenCalledWith({
      'context.windowOverrides': {
        'anthropic-cli/claude-opus-5-5': 200000,
        'openai-codex/gpt-5': 180000,
      },
    })

    // Managed field is disabled and cannot be reset
    const managedField = screen.getByTestId('setting-row-quota.policy.blockBackgroundPercent')
    const managedInput = within(managedField).getByLabelText('Percentual para bloquear trabalho em fundo')
    expect(managedInput).toBeDisabled()
    expect(within(managedField).queryByRole('button', { name: 'Restaurar padrão' })).not.toBeInTheDocument()
  })

  it('loads and saves through PUT /v1/settings/orchestration in QuotaAndEffortSection', async () => {
    const user = userEvent.setup()
    let subagentPolicy = 'model_default'
    let subagentLayer = 'default'
    const requests: Array<{ url: string; method: string; body: string | null }> = []

    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? init.body : null
      requests.push({ url, method, body })

      if (method === 'PUT' && body) {
        const parsed = JSON.parse(body) as { changes?: Record<string, unknown> }
        if (typeof parsed.changes?.['effort.subagentPolicy'] === 'string') {
          subagentPolicy = parsed.changes['effort.subagentPolicy']
          subagentLayer = 'user'
        }
      }

      return Promise.resolve(
        new Response(
          JSON.stringify({
            project_root: null,
            trusted: false,
            items: [
              {
                path: 'effort.subagentPolicy',
                type: 'choice',
                group: 'effort',
                label: 'Esforço dos subagentes',
                description: 'Define a política de esforço.',
                scope: 'user_only',
                default: 'model_default',
                value: subagentPolicy,
                layer: subagentLayer,
                user_value: subagentLayer === 'user' ? subagentPolicy : null,
                has_user_override: subagentLayer === 'user',
                minimum: null,
                maximum: null,
                choices: ['model_default', 'inherit', 'by_type'],
                item_type: null,
                value_minimum: null,
                value_maximum: null,
                locked: false,
              },
              {
                path: 'quota.display.enabled',
                type: 'boolean',
                group: 'quota',
                label: 'Mostrar cota no compositor',
                description: 'Exibe cota no compositor.',
                scope: 'user_only',
                default: true,
                value: true,
                layer: 'default',
                user_value: null,
                has_user_override: false,
                minimum: null,
                maximum: null,
                choices: null,
                item_type: null,
                value_minimum: null,
                value_maximum: null,
                locked: false,
              },
              {
                path: 'context.windowOverrides',
                type: 'map',
                group: 'context',
                label: 'Janela de contexto por modelo',
                description: 'Override manual.',
                scope: 'project_lower_only',
                default: {},
                value: {},
                layer: 'default',
                user_value: null,
                has_user_override: false,
                minimum: null,
                maximum: null,
                choices: null,
                item_type: 'number',
                value_minimum: 50000,
                value_maximum: 1000000,
                locked: false,
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    })

    const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl })
    render(
      <MemoryRouter initialEntries={['/settings/quota-effort']}>
        <QuotaAndEffortSection client={client} />
      </MemoryRouter>,
    )

    const select = await screen.findByLabelText('Esforço dos subagentes')
    await user.selectOptions(select, 'by_type')

    await waitFor(() => {
      const putReq = requests.find((req) => req.method === 'PUT' && req.url.includes('/v1/settings/orchestration'))
      expect(putReq).toBeDefined()
      expect(JSON.parse(putReq?.body ?? '{}')).toMatchObject({
        changes: { 'effort.subagentPolicy': 'by_type' },
      })
    })
    expect(screen.getByText('Usuário')).toBeInTheDocument()
  })
})
