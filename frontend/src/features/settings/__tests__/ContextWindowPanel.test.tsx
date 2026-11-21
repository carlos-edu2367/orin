import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import type { OrchestrationSettingItem } from '../../../api/orchestrationSettings'
import { ContextWindowPanel } from '../ContextWindowPanel'

function makeContextSettingItems(overrides: Record<string, number> = {}): OrchestrationSettingItem[] {
  return [
    {
      path: 'context.windowOverrides',
      type: 'map',
      group: 'context',
      label: 'Janela de contexto por modelo',
      description: 'Sobrescrita manual do limite de tokens (50.000 a 1.000.000).',
      scope: 'project_lower_only',
      defaultValue: {},
      value: overrides,
      layer: Object.keys(overrides).length > 0 ? 'user' : 'default',
      userValue: Object.keys(overrides).length > 0 ? overrides : null,
      hasUserOverride: Object.keys(overrides).length > 0,
      minimum: null,
      maximum: null,
      choices: null,
      itemType: 'number',
      valueMinimum: 50000,
      valueMaximum: 1000000,
      locked: false,
    },
    {
      path: 'claudeCli.longContextWithCredits',
      type: 'boolean',
      group: 'context',
      label: 'Usar janela de 1M com créditos de uso',
      description: 'Habilita o limite de 1 milhão de tokens no Claude CLI quando a conta usa créditos extras.',
      scope: 'user_only',
      defaultValue: false,
      value: false,
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
  ]
}

describe('ContextWindowPanel', () => {
  it('displays catalog, learned (with date and Esquecer -> DELETE), effective window + detail, and credits warning', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockResolvedValue(undefined)
    let learnedActive = true
    const requests: Array<{ url: string; method: string }> = []

    const fetchImpl = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      requests.push({ url, method })

      if (url.includes('/v1/context/windows/anthropic-cli/claude-fable-5-2') && method === 'DELETE') {
        learnedActive = false
        return Promise.resolve(
          new Response(
            JSON.stringify({
              forgotten: true,
              provider: 'anthropic-cli',
              model_id: 'claude-fable-5-2',
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          ),
        )
      }

      if (url.includes('/v1/context/windows')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              windows: [
                {
                  provider: 'anthropic-cli',
                  model_id: 'claude-fable-5-2',
                  display_name: 'Claude Fable 5.2',
                  catalog_tokens: 1000000,
                  learned: learnedActive
                    ? {
                        tokens: 200000,
                        source: 'error_raw',
                        observed_at: '2026-11-20T14:30:00Z',
                      }
                    : null,
                  override_tokens: null,
                  effective_tokens: 200000,
                  effective_source: learnedActive ? 'learned' : 'account_credits',
                  effective_detail: learnedActive
                    ? '200 mil, aprendido após erro do modelo'
                    : '200 mil, limite da conta sem créditos',
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
      <ContextWindowPanel
        client={client}
        items={makeContextSettingItems()}
        onSave={onSave}
      />,
    )

    // Explicit warning on claudeCli.longContextWithCredits
    const creditsRow = screen.getByTestId('context-setting-claudeCli.longContextWithCredits')
    expect(creditsRow).toHaveTextContent(/Gasta créditos pagos/i)
    await user.click(within(creditsRow).getByRole('switch'))
    expect(onSave).toHaveBeenCalledWith({ 'claudeCli.longContextWithCredits': true })

    // Model row displays catalog, learned with date, and effective window
    const row = await screen.findByTestId('context-window-row-anthropic-cli/claude-fable-5-2')
    expect(row).toHaveTextContent('Claude Fable 5.2')
    expect(row).toHaveTextContent('1.000.000')
    expect(row).toHaveTextContent('200.000')
    expect(row).toHaveTextContent('200 mil, aprendido após erro do modelo')

    const forgetBtn = within(row).getByRole('button', { name: /Esquecer/i })
    await user.click(forgetBtn)

    await waitFor(() => {
      expect(
        requests.some(
          (req) =>
            req.method === 'DELETE' &&
            req.url.includes('/v1/context/windows/anthropic-cli/claude-fable-5-2'),
        ),
      ).toBe(true)
    })

    await waitFor(() => {
      expect(screen.getByTestId('context-window-row-anthropic-cli/claude-fable-5-2')).toHaveTextContent(
        '200 mil, limite da conta sem créditos',
      )
    })
  })

  it('rejects manual override values outside 50.000..1.000.000 with a clear pt-BR message and saves valid overrides', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockResolvedValue(undefined)
    const fetchImpl = vi.fn<typeof fetch>(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            windows: [
              {
                provider: 'openai-codex',
                model_id: 'gpt-5-codex',
                display_name: 'GPT-5 Codex',
                catalog_tokens: 400000,
                learned: null,
                override_tokens: null,
                effective_tokens: 400000,
                effective_source: 'cli_catalog',
                effective_detail: '400 mil, catálogo do CLI',
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl, maxAttempts: 1 })
    render(
      <ContextWindowPanel
        client={client}
        items={makeContextSettingItems()}
        onSave={onSave}
      />,
    )

    const row = await screen.findByTestId('context-window-row-openai-codex/gpt-5-codex')
    const overrideInput = within(row).getByLabelText(/Override manual/i)
    const saveBtn = within(row).getByRole('button', { name: 'Salvar' })

    // Below minimum
    await user.clear(overrideInput)
    await user.type(overrideInput, '30000')
    await user.click(saveBtn)

    expect(screen.getByRole('alert')).toHaveTextContent(/50\.000.*1\.000\.000/)
    expect(onSave).not.toHaveBeenCalled()

    // Above maximum
    await user.clear(overrideInput)
    await user.type(overrideInput, '1500000')
    await user.click(saveBtn)

    expect(screen.getByRole('alert')).toHaveTextContent(/50\.000.*1\.000\.000/)
    expect(onSave).not.toHaveBeenCalled()

    // Valid override
    await user.clear(overrideInput)
    await user.type(overrideInput, '180000')
    await user.click(saveBtn)

    expect(onSave).toHaveBeenCalledWith({
      'context.windowOverrides': {
        'openai-codex/gpt-5-codex': 180000,
      },
    })
  })
})
