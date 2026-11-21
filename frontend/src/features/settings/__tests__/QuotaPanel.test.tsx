import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import type { OrchestrationSettingItem } from '../../../api/orchestrationSettings'
import { QuotaPanel } from '../QuotaPanel'

function makeQuotaSettingItems(): OrchestrationSettingItem[] {
  const base = {
    group: 'quota',
    scope: 'user_only' as const,
    userValue: null,
    hasUserOverride: false,
    minimum: null,
    maximum: null,
    choices: null,
    itemType: null,
    valueMinimum: null,
    valueMaximum: null,
    locked: false,
  }
  return [
    {
      ...base,
      path: 'quota.display.enabled',
      type: 'boolean',
      label: 'Mostrar cota no compositor',
      description: 'Exibe o indicador de uso da assinatura ao lado do seletor de modelo.',
      defaultValue: true,
      value: true,
      layer: 'default',
    },
    {
      ...base,
      path: 'quota.policy.enabled',
      type: 'boolean',
      label: 'Ativar política por cota',
      description: 'Avisa antes de tarefas custosas e limita trabalho em fundo.',
      defaultValue: false,
      value: false,
      layer: 'default',
    },
    {
      ...base,
      path: 'quota.policy.warnPercent',
      type: 'number',
      label: 'Percentual de aviso de cota',
      description: 'Percentual de 1 a 100.',
      defaultValue: 80,
      value: 75,
      layer: 'user',
      userValue: 75,
      hasUserOverride: true,
      minimum: 1,
      maximum: 100,
    },
    {
      ...base,
      path: 'quota.policy.blockBackgroundPercent',
      type: 'number',
      label: 'Percentual para bloquear trabalho em fundo',
      description: 'Percentual de 1 a 100.',
      defaultValue: 90,
      value: 90,
      layer: 'default',
      minimum: 1,
      maximum: 100,
    },
    {
      ...base,
      path: 'quota.fallback.enabled',
      type: 'boolean',
      label: 'Trocar de assinatura ao esgotar',
      description: 'Inicia o próximo turno no próximo modelo da cadeia.',
      defaultValue: false,
      value: false,
      layer: 'default',
    },
    {
      ...base,
      path: 'quota.fallback.chain',
      type: 'string_list',
      label: 'Cadeia de fallback por cota',
      description: 'Lista ordenada de provider/modelo.',
      defaultValue: [],
      value: ['openai-codex/gpt-5'],
      layer: 'user',
      userValue: ['openai-codex/gpt-5'],
      hasUserOverride: true,
      itemType: 'string',
    },
  ]
}

describe('QuotaPanel', () => {
  it('loads all three subscriptions from GET /v1/quota and renders settings with origin layers and reset actions', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockResolvedValue(undefined)
    const fetchImpl = vi.fn<typeof fetch>((input) => {
      const url = String(input)
      if (url.includes('/v1/quota')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              subscriptions: [
                {
                  provider: 'anthropic-cli',
                  windows: [
                    {
                      kind: 'five_hour',
                      used_percent: 52,
                      resets_at: '2026-11-21T18:00:00Z',
                      status: 'allowed',
                      scope_model: null,
                    },
                  ],
                  confidence: 'measured',
                  observed_at: '2026-11-21T12:55:00Z',
                  age_seconds: 300,
                  source: 'rate_limit_event',
                  orin_usage: {
                    five_hour: { tokens: 14000, calls: 3 },
                    seven_day: { tokens: 90000, calls: 19 },
                  },
                },
                {
                  provider: 'openai-codex',
                  windows: [
                    {
                      kind: 'five_hour',
                      used_percent: 88,
                      resets_at: '2026-11-21T17:00:00Z',
                      status: 'allowed_warning',
                      scope_model: null,
                    },
                  ],
                  confidence: 'measured',
                  observed_at: '2026-11-21T12:57:00Z',
                  age_seconds: 180,
                  source: 'codex_rollout',
                  orin_usage: {
                    five_hour: { tokens: 25000, calls: 5 },
                    seven_day: { tokens: 120000, calls: 22 },
                  },
                },
                {
                  provider: 'google-antigravity',
                  windows: [],
                  confidence: 'unknown',
                  observed_at: null,
                  age_seconds: null,
                  source: null,
                  orin_usage: {
                    five_hour: { tokens: 0, calls: 0 },
                    seven_day: { tokens: 4200, calls: 1 },
                  },
                },
              ],
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          ),
        )
      }
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }))
    })

    const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl, maxAttempts: 1 })
    render(
      <QuotaPanel
        client={client}
        items={makeQuotaSettingItems()}
        onSave={onSave}
        nowMs={Date.parse('2026-11-21T13:00:00Z')}
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('quota-card-anthropic-cli')).toHaveTextContent('52%')
    })

    expect(screen.getByTestId('quota-card-openai-codex')).toHaveTextContent('88%')
    expect(screen.getByTestId('quota-card-google-antigravity')).toHaveTextContent(
      'O Antigravity não informa a cota',
    )

    // Verify settings controls and origin layer badges
    const displayRow = screen.getByTestId('setting-row-quota.display.enabled')
    expect(within(displayRow).getByText('Padrão')).toBeInTheDocument()
    await user.click(within(displayRow).getByRole('switch', { name: 'Mostrar cota no compositor' }))
    expect(onSave).toHaveBeenCalledWith({ 'quota.display.enabled': false })

    const warnRow = screen.getByTestId('setting-row-quota.policy.warnPercent')
    expect(within(warnRow).getByText('Usuário')).toBeInTheDocument()
    await user.click(within(warnRow).getByRole('button', { name: 'Restaurar padrão' }))
    expect(onSave).toHaveBeenCalledWith({ 'quota.policy.warnPercent': null })

    const chainRow = screen.getByTestId('setting-row-quota.fallback.chain')
    expect(within(chainRow).getByText('openai-codex/gpt-5')).toBeInTheDocument()
  })
})
