import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import type { ProviderQuota } from '../../../api/quota'
import { ContextIndicator } from '../ContextIndicator'
import { QuotaPopover } from '../QuotaPopover'
import { useSubscriptionQuota } from '../useSubscriptionQuota'
import type { ContextUsage } from '../activityTypes'

const SAMPLE_SUBSCRIPTIONS: ProviderQuota[] = [
  {
    provider: 'anthropic-cli',
    windows: [
      {
        kind: 'five_hour',
        used_percent: 45,
        resets_at: '2026-11-21T16:30:00Z',
        status: 'allowed',
        scope_model: null,
      },
      {
        kind: 'weekly_scoped',
        used_percent: 77,
        resets_at: '2026-11-25T00:00:00Z',
        status: 'allowed_warning',
        scope_model: 'Fable',
      },
    ],
    confidence: 'measured',
    observed_at: '2026-11-21T12:54:00Z',
    age_seconds: 360,
    source: 'claude_usage_cache',
    orin_usage: {
      five_hour: { tokens: 42500, calls: 7 },
      seven_day: { tokens: 310000, calls: 48 },
    },
  },
  {
    provider: 'openai-codex',
    windows: [
      {
        kind: 'five_hour',
        used_percent: 82,
        resets_at: '2026-11-21T15:00:00Z',
        status: 'allowed_warning',
        scope_model: null,
      },
      {
        kind: 'seven_day',
        used_percent: 31,
        resets_at: '2026-11-28T00:00:00Z',
        status: 'allowed',
        scope_model: null,
      },
    ],
    confidence: 'measured',
    observed_at: '2026-11-21T12:58:00Z',
    age_seconds: 120,
    source: 'codex_rollout',
    orin_usage: {
      five_hour: { tokens: 18000, calls: 3 },
      seven_day: { tokens: 95000, calls: 14 },
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
      five_hour: { tokens: 5000, calls: 2 },
      seven_day: { tokens: 22000, calls: 9 },
    },
  },
]

describe('QuotaPopover', () => {
  const nowMs = Date.parse('2026-11-21T13:00:00Z')

  it('opens on ring click showing all three subscriptions side by side with windows, sources, age, and Orin usage', async () => {
    const user = userEvent.setup()
    render(
      <QuotaPopover
        provider="anthropic-cli"
        subscriptions={SAMPLE_SUBSCRIPTIONS}
        displayEnabled={true}
        nowMs={nowMs}
      />,
    )

    expect(screen.queryByTestId('quota-popover-panel')).not.toBeInTheDocument()

    await user.click(screen.getByTestId('quota-ring-trigger'))

    const dialog = screen.getByRole('dialog', { name: /cota das assinaturas/i })
    expect(dialog).toBeInTheDocument()

    const claudeCard = within(dialog).getByTestId('quota-card-anthropic-cli')
    expect(claudeCard).toHaveTextContent('Claude CLI')
    expect(claudeCard).toHaveTextContent('45%')
    expect(claudeCard).toHaveTextContent('Semanal · Fable')
    expect(claudeCard).toHaveTextContent('77%')
    expect(claudeCard).toHaveTextContent('Cache local do Claude')
    expect(claudeCard).toHaveTextContent('visto há 6 min')
    expect(claudeCard).toHaveTextContent(/42\.500/)
    expect(claudeCard).toHaveTextContent(/7 chamadas/)

    const codexCard = within(dialog).getByTestId('quota-card-openai-codex')
    expect(codexCard).toHaveTextContent('Codex CLI')
    expect(codexCard).toHaveTextContent('82%')
    expect(codexCard).toHaveTextContent('31%')
    expect(codexCard).toHaveTextContent('visto há 2 min')
    expect(codexCard).toHaveTextContent(/18\.000/)

    const agyCard = within(dialog).getByTestId('quota-card-google-antigravity')
    expect(agyCard).toHaveTextContent('Antigravity')
    expect(agyCard).toHaveTextContent('Sem leitura do Antigravity ainda')
    expect(agyCard).not.toHaveTextContent('não informa')
    expect(agyCard).toHaveTextContent(/5\.000/)
    expect(agyCard).toHaveTextContent(/22\.000/)
  })

  it('closes on Escape key and on outside click', async () => {
    const user = userEvent.setup()
    render(
      <div>
        <button type="button">Fora</button>
        <QuotaPopover
          provider="openai-codex"
          subscriptions={SAMPLE_SUBSCRIPTIONS}
          displayEnabled={true}
          nowMs={nowMs}
        />
      </div>,
    )

    await user.click(screen.getByTestId('quota-ring-trigger'))
    expect(screen.getByRole('dialog', { name: /cota das assinaturas/i })).toBeInTheDocument()

    await user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: /cota das assinaturas/i })).not.toBeInTheDocument()
    })

    await user.click(screen.getByTestId('quota-ring-trigger'))
    expect(screen.getByRole('dialog', { name: /cota das assinaturas/i })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Fora' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: /cota das assinaturas/i })).not.toBeInTheDocument()
    })
  })

  it('hides completely when displayEnabled is false and calls onRefresh when requested', async () => {
    const user = userEvent.setup()
    const onRefresh = vi.fn()
    const { rerender, container } = render(
      <QuotaPopover
        provider="anthropic-cli"
        subscriptions={SAMPLE_SUBSCRIPTIONS}
        displayEnabled={false}
        onRefresh={onRefresh}
        nowMs={nowMs}
      />,
    )
    expect(container.firstChild).toBeNull()

    rerender(
      <QuotaPopover
        provider="anthropic-cli"
        subscriptions={SAMPLE_SUBSCRIPTIONS}
        displayEnabled={true}
        onRefresh={onRefresh}
        nowMs={nowMs}
      />,
    )
    await user.click(screen.getByTestId('quota-ring-trigger'))
    // Opening the panel already asks for fresh data (server-side CLI probe).
    expect(onRefresh).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: /atualizar/i }))
    expect(onRefresh).toHaveBeenCalledTimes(2)
  })

  it('does not refresh while closed and refreshes once per opening', async () => {
    const user = userEvent.setup()
    const onRefresh = vi.fn()
    render(
      <QuotaPopover
        provider="anthropic-cli"
        subscriptions={SAMPLE_SUBSCRIPTIONS}
        onRefresh={onRefresh}
        nowMs={nowMs}
      />,
    )
    expect(onRefresh).not.toHaveBeenCalled()
    await user.click(screen.getByTestId('quota-ring-trigger'))
    await user.click(screen.getByRole('button', { name: /fechar painel/i }))
    await user.click(screen.getByTestId('quota-ring-trigger'))
    expect(onRefresh).toHaveBeenCalledTimes(2)
  })

  it('renders a compact vertical list on its own surface, not on the shared picker panel', async () => {
    const user = userEvent.setup()
    render(
      <QuotaPopover provider="anthropic-cli" subscriptions={SAMPLE_SUBSCRIPTIONS} nowMs={nowMs} />,
    )
    await user.click(screen.getByTestId('quota-ring-trigger'))

    const panel = screen.getByTestId('quota-popover-panel')
    expect(panel).toHaveClass('quota-popover__panel')
    expect(panel).not.toHaveClass('picker-panel')
    expect(screen.getByTestId('quota-subscriptions-grid')).toHaveClass('quota-cards--popover')

    const cards = within(panel).getAllByTestId(/^quota-card-/)
    expect(cards.map((card) => card.getAttribute('data-testid'))).toEqual([
      'quota-card-anthropic-cli',
      'quota-card-openai-codex',
      'quota-card-google-antigravity',
    ])
    expect(within(cards[0]).getByText('Atual')).toBeInTheDocument()
    expect(within(cards[1]).queryByText('Atual')).not.toBeInTheDocument()

    // One row per window with label, percent, bar and release time; usage by Orin is collapsed.
    const windows = within(cards[0]).getAllByRole('listitem')
    expect(windows).toHaveLength(2)
    expect(windows[0]).toHaveTextContent('5 h')
    expect(windows[0]).toHaveTextContent('45%')
    expect(windows[0]).toHaveTextContent(/libera /)
    expect(cards[0].querySelector('details.quota-card__orin-usage')).not.toHaveAttribute('open')
  })

  it('shows the measured Antigravity groups as scoped windows and the new source labels', async () => {
    const user = userEvent.setup()
    const agy: ProviderQuota = {
      ...SAMPLE_SUBSCRIPTIONS[2],
      confidence: 'measured',
      source: 'agy_usage_command',
      observed_at: '2026-11-21T12:59:00Z',
      age_seconds: 60,
      windows: [
        { kind: 'seven_day', used_percent: 9, resets_at: '2026-11-25T18:03:09Z', status: 'allowed', scope_model: 'Claude e GPT', scope_models: ['Claude Opus 5.5', 'GPT-OSS 120B'] },
        { kind: 'five_hour', used_percent: 0, resets_at: '2026-11-21T17:21:19Z', status: 'allowed', scope_model: 'Claude e GPT' },
        { kind: 'seven_day', used_percent: 1, resets_at: '2026-11-30T12:20:26Z', status: 'allowed', scope_model: 'Gemini' },
        { kind: 'five_hour', used_percent: 17, resets_at: '2026-11-21T17:20:26Z', status: 'allowed', scope_model: 'Gemini' },
      ],
    }
    const codex: ProviderQuota = { ...SAMPLE_SUBSCRIPTIONS[1], source: 'codex_app_server' }
    const claude: ProviderQuota = { ...SAMPLE_SUBSCRIPTIONS[0], source: 'claude_usage_command' }
    render(
      <QuotaPopover provider="google-antigravity" subscriptions={[claude, codex, agy]} nowMs={nowMs} />,
    )
    await user.click(screen.getByTestId('quota-ring-trigger'))

    const agyCard = screen.getByTestId('quota-card-google-antigravity')
    const labels = within(agyCard)
      .getAllByRole('listitem')
      .map((item) => item.querySelector('.quota-card__window-label')?.textContent)
    expect(labels).toEqual(['5 h · Claude e GPT', 'Semanal · Claude e GPT', '5 h · Gemini', 'Semanal · Gemini'])
    expect(agyCard).toHaveTextContent('Consulta ao Antigravity (/usage)')
    expect(agyCard).toHaveTextContent('Medido')
    expect(within(agyCard).getByText('Semanal · Claude e GPT')).toHaveAttribute(
      'title',
      expect.stringContaining('Modelos: Claude Opus 5.5, GPT-OSS 120B'),
    )
    expect(screen.getByTestId('quota-card-anthropic-cli')).toHaveTextContent('Consulta ao Claude (/usage)')
    expect(screen.getByTestId('quota-card-openai-codex')).toHaveTextContent('Consulta ao Codex (app-server)')
  })

  it('shows a renewed window as renewed instead of the old percent', async () => {
    const user = userEvent.setup()
    const renewed: ProviderQuota = {
      ...SAMPLE_SUBSCRIPTIONS[0],
      confidence: 'partial',
      windows: [
        { kind: 'seven_day', used_percent: null, resets_at: null, status: 'renewed', scope_model: null },
      ],
    }
    render(<QuotaPopover provider="anthropic-cli" subscriptions={[renewed]} nowMs={nowMs} />)
    await user.click(screen.getByTestId('quota-ring-trigger'))

    const card = screen.getByTestId('quota-card-anthropic-cli')
    expect(card).toHaveTextContent('Semanal')
    expect(card).toHaveTextContent('Renovada')
    expect(card).toHaveTextContent('aguardando leitura')
    expect(card).not.toHaveTextContent('77%')
  })

  it('advances the age on its own every 30 seconds while mounted', () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(Date.parse('2026-11-21T13:00:00Z'))
      const quota: ProviderQuota = { ...SAMPLE_SUBSCRIPTIONS[0], observed_at: '2026-11-21T12:56:00Z', age_seconds: 240 }
      render(<QuotaPopover provider="anthropic-cli" subscriptions={[quota]} />)
      const trigger = screen.getByTestId('quota-ring-trigger')
      expect(trigger.getAttribute('title')).toContain('visto há 4 min')

      act(() => {
        vi.advanceTimersByTime(30_000)
      })
      expect(trigger.getAttribute('title')).toContain('visto há 4 min')
      act(() => {
        vi.advanceTimersByTime(30_000)
      })
      expect(trigger.getAttribute('title')).toContain('visto há 5 min')
    } finally {
      vi.useRealTimers()
    }
  })

  it('refreshes displayed quota automatically when SSE quota.updated arrives', async () => {
    let currentPercent = 45
    let pushSse: ((chunk: string) => void) | null = null
    const encoder = new TextEncoder()

    const fetchImpl = vi.fn<typeof fetch>((input) => {
      const url = String(input)
      if (url.includes('/v1/settings/orchestration')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              project_root: null,
              trusted: false,
              items: [
                {
                  path: 'quota.display.enabled',
                  type: 'boolean',
                  group: 'quota',
                  label: 'Mostrar cota no compositor',
                  description: 'Exibe cota.',
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
              ],
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          ),
        )
      }
      if (url.includes('/v1/quota/stream')) {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            pushSse = (text: string) => {
              controller.enqueue(encoder.encode(text))
            }
          },
        })
        return Promise.resolve(
          new Response(stream, {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
          }),
        )
      }
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
                      used_percent: currentPercent,
                      resets_at: '2026-11-21T16:30:00Z',
                      status: currentPercent > 90 ? 'allowed_warning' : 'allowed',
                      scope_model: null,
                    },
                  ],
                  confidence: 'measured',
                  observed_at: '2026-11-21T12:55:00Z',
                  age_seconds: 300,
                  source: 'rate_limit_event',
                  orin_usage: {
                    five_hour: { tokens: 1000, calls: 1 },
                    seven_day: { tokens: 2000, calls: 2 },
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

    function Harness() {
      const q = useSubscriptionQuota({ client, provider: 'anthropic' })
      return (
        <QuotaPopover
          provider={q.provider}
          subscriptions={q.subscriptions}
          displayEnabled={q.displayEnabled}
          nowMs={nowMs}
        />
      )
    }

    render(<Harness />)

    await waitFor(() => {
      expect(screen.getByTestId('quota-ring-trigger')).toHaveTextContent('45%')
    })

    currentPercent = 92
    expect(pushSse).not.toBeNull()
    pushSse!('event: quota.updated\ndata: {"event":"quota.updated","provider":"anthropic-cli","user_id":"u1"}\n\n')

    await waitFor(() => {
      expect(screen.getByTestId('quota-ring-trigger')).toHaveTextContent('92%')
      expect(screen.getByTestId('quota-ring-trigger')).toHaveAttribute('data-tone', 'danger')
    })
  })
})

describe('ContextIndicator window source details', () => {
  it('shows effective window detail and readable window_source fallback in tooltip and title', () => {
    const usage: ContextUsage = {
      used_tokens: 90000,
      limit_tokens: 200000,
      percentage: 45,
      system_prompt_tokens: 10000,
      history_tokens: 60000,
      input_tokens: 5000,
      tools_tokens: 10000,
      skills_tokens: 3000,
      mcps_tokens: 2000,
      omitted_messages: 0,
      compaction_count: 0,
      compaction_enabled: true,
      window_source: 'account_credits',
      window_detail: '200 mil, limite da conta sem créditos',
    }
    const { rerender } = render(<ContextIndicator usage={usage} />)

    const indicator = screen.getByLabelText(/Contexto:/i)
    expect(indicator.getAttribute('title') ?? '').toContain('200 mil, limite da conta sem créditos')
    expect(screen.getByTestId('context-indicator-window-source')).toHaveTextContent(
      '200 mil, limite da conta sem créditos',
    )

    rerender(
      <ContextIndicator
        usage={{
          ...usage,
          window_detail: undefined,
          window_source: 'learned',
        }}
      />,
    )
    expect(screen.getByTestId('context-indicator-window-source')).toHaveTextContent(
      /aprendido após erro do modelo/i,
    )
  })
})
