import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { QUOTA_PROBE_FOLLOW_UP_MS, parseQuotaReport } from '../../../api/quota'
import { useSubscriptionQuota } from '../useSubscriptionQuota'

function makeClient(requests: string[]) {
  const fetchImpl = vi.fn<typeof fetch>((input) => {
    const url = String(input)
    requests.push(url)
    if (url.includes('/v1/settings/orchestration')) {
      return Promise.resolve(new Response(JSON.stringify({ project_root: null, trusted: false, items: [] }), { status: 200 }))
    }
    if (url.includes('/v1/quota/stream')) {
      return Promise.resolve(new Response('', { status: 200, headers: { 'Content-Type': 'application/json' } }))
    }
    return Promise.resolve(
      new Response(JSON.stringify({ subscriptions: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  })
  return new ApiClient({ baseUrl: 'http://localhost', fetchImpl, maxAttempts: 1 })
}

function Harness({ client }: { client: ApiClient }) {
  const q = useSubscriptionQuota({ client, provider: 'anthropic-cli', subscribeStream: false })
  return (
    <div>
      <button type="button" onClick={q.refresh}>
        refresh
      </button>
      <span data-testid="loading">{String(q.loading)}</span>
    </div>
  )
}

afterEach(() => {
  vi.useRealTimers()
})

describe('useSubscriptionQuota refresh', () => {
  it('asks the server to probe (refresh=true) and reads once more after the follow-up delay', async () => {
    const requests: string[] = []
    const client = makeClient(requests)
    render(<Harness client={client} />)
    await waitFor(() => expect(requests.some((url) => url.includes('/v1/quota'))).toBe(true))

    vi.useFakeTimers()
    const quotaRequests = () => requests.filter((url) => url.includes('/v1/quota') && !url.includes('/stream'))
    const before = quotaRequests().length

    await act(async () => {
      screen.getByText('refresh').click()
      await Promise.resolve()
    })
    const afterClick = quotaRequests()
    expect(afterClick.length).toBe(before + 1)
    expect(afterClick[afterClick.length - 1]).toContain('refresh=true')
    expect(screen.getByTestId('loading')).toHaveTextContent('true')

    await act(async () => {
      vi.advanceTimersByTime(QUOTA_PROBE_FOLLOW_UP_MS + 10)
      await Promise.resolve()
    })
    const afterFollowUp = quotaRequests()
    expect(afterFollowUp.length).toBe(before + 2)
    expect(afterFollowUp[afterFollowUp.length - 1]).not.toContain('refresh=true')
    expect(screen.getByTestId('loading')).toHaveTextContent('false')
  })
})

describe('parseQuotaReport', () => {
  it('keeps scope_models and the renewed status, and tolerates reports without them', () => {
    const report = parseQuotaReport({
      subscriptions: [
        {
          provider: 'google-antigravity',
          confidence: 'measured',
          windows: [
            {
              kind: 'five_hour',
              used_percent: 17.4,
              resets_at: '2026-11-23T17:20:26+00:00',
              status: 'allowed',
              scope_model: 'Gemini',
              scope_models: ['Gemini 4 Argon', '', 3, 'Gemini 3.8 Flash'],
            },
            { kind: 'seven_day', used_percent: null, resets_at: null, status: 'renewed', scope_model: null },
            { kind: 'seven_day', used_percent: 4, resets_at: null, status: 'allowed', scope_model: null },
          ],
        },
      ],
    })
    const windows = report.subscriptions[0].windows
    expect(windows[0].scope_models).toEqual(['Gemini 4 Argon', 'Gemini 3.8 Flash'])
    expect(windows[1].status).toBe('renewed')
    expect(windows[1].scope_models).toEqual([])
    expect(windows[2].scope_models).toEqual([])
  })
})
