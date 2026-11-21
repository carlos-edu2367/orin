import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ProviderQuota } from '../../api/quota'
import { QuotaRing } from '../QuotaRing'

function makeQuota(overrides: Partial<ProviderQuota> = {}): ProviderQuota {
  return {
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
        used_percent: 34,
        resets_at: '2026-11-25T00:00:00Z',
        status: 'allowed',
        scope_model: 'Fable',
      },
    ],
    confidence: 'measured',
    observed_at: '2026-11-21T12:55:00Z',
    age_seconds: 300,
    source: 'rate_limit_event',
    orin_usage: {
      five_hour: { tokens: 12000, calls: 4 },
      seven_day: { tokens: 84000, calls: 21 },
    },
    ...overrides,
  }
}

describe('QuotaRing', () => {
  const nowMs = Date.parse('2026-11-21T13:00:00Z')

  it('renders green tone below 70% with highest known percent, windows, reset time, and age in tooltip', () => {
    const quota = makeQuota()
    render(<QuotaRing quota={quota} provider="anthropic-cli" nowMs={nowMs} />)

    const trigger = screen.getByTestId('quota-ring-trigger')
    expect(trigger).toHaveAttribute('data-tone', 'ok')
    expect(trigger).toHaveAttribute('data-confidence', 'measured')
    expect(trigger).toHaveTextContent('45%')

    const title = trigger.getAttribute('title') ?? ''
    expect(title).toContain('5 h')
    expect(title).toContain('45%')
    expect(title).toContain('Fable')
    expect(title).toContain('34%')
    expect(title).toContain('visto há 5 min')
    expect(title).toMatch(/libera/i)
  })

  it('renders amber tone between 70% and 90% for partial/estimated confidence', () => {
    const quota = makeQuota({
      provider: 'openai-codex',
      confidence: 'partial',
      observed_at: '2026-11-21T12:59:45Z',
      age_seconds: 15,
      source: 'codex_rollout',
      windows: [
        {
          kind: 'five_hour',
          used_percent: 78,
          resets_at: '2026-11-21T17:00:00Z',
          status: 'allowed_warning',
          scope_model: null,
        },
        {
          kind: 'seven_day',
          used_percent: 62,
          resets_at: '2026-11-28T00:00:00Z',
          status: 'allowed',
          scope_model: null,
        },
      ],
    })
    render(<QuotaRing quota={quota} provider="openai-codex" nowMs={nowMs} />)

    const trigger = screen.getByTestId('quota-ring-trigger')
    expect(trigger).toHaveAttribute('data-tone', 'warn')
    expect(trigger).toHaveAttribute('data-confidence', 'partial')
    expect(trigger).toHaveTextContent('78%')
    expect(trigger.getAttribute('title') ?? '').toContain('agora')
    expect(trigger.getAttribute('title') ?? '').toContain('Semanal')
  })

  it('renders red tone above 90% and also when status is rejected without percent', () => {
    const highQuota = makeQuota({
      windows: [
        { kind: 'five_hour', used_percent: 94, resets_at: '2026-11-21T16:00:00Z', status: 'allowed_warning', scope_model: null },
      ],
    })
    const { rerender } = render(<QuotaRing quota={highQuota} provider="anthropic-cli" nowMs={nowMs} />)
    expect(screen.getByTestId('quota-ring-trigger')).toHaveAttribute('data-tone', 'danger')
    expect(screen.getByTestId('quota-ring-trigger')).toHaveTextContent('94%')

    const rejectedQuota = makeQuota({
      provider: 'google-antigravity',
      confidence: 'partial',
      windows: [
        { kind: 'unknown', used_percent: null, resets_at: '2026-11-21T15:00:00Z', status: 'rejected', scope_model: null },
      ],
    })
    rerender(<QuotaRing quota={rejectedQuota} provider="google-antigravity" nowMs={nowMs} />)
    const trigger = screen.getByTestId('quota-ring-trigger')
    expect(trigger).toHaveAttribute('data-tone', 'danger')
    expect(trigger.textContent).not.toMatch(/%/)
  })

  it('renders "?" without inventing percent for unknown Antigravity quota and includes clear hint', () => {
    const quota = makeQuota({
      provider: 'google-antigravity',
      confidence: 'unknown',
      windows: [],
      observed_at: null,
      age_seconds: null,
      source: null,
    })
    render(<QuotaRing quota={quota} provider="google-antigravity" nowMs={nowMs} />)

    const trigger = screen.getByTestId('quota-ring-trigger')
    expect(trigger).toHaveAttribute('data-tone', 'unknown')
    expect(trigger).toHaveAttribute('data-confidence', 'unknown')
    expect(trigger).toHaveTextContent('?')
    expect(trigger.textContent).not.toMatch(/\d+%/)
    expect(trigger.getAttribute('title') ?? '').toContain('O Antigravity não informa a cota')
  })

  it('renders nothing when quota.display.enabled is false', () => {
    const { container } = render(
      <QuotaRing quota={makeQuota()} provider="anthropic-cli" enabled={false} nowMs={nowMs} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('invokes onClick handler when activated', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    render(<QuotaRing quota={makeQuota()} provider="anthropic-cli" onClick={onClick} nowMs={nowMs} />)

    await user.click(screen.getByTestId('quota-ring-trigger'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})
