import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import type { ConversationActivityEvent } from '../../src/features/conversations/activityTypes'
import { OverviewPanel } from '../../src/features/overview/OverviewPanel'

vi.mock('../../src/features/overview/OrbitalScene', () => ({
  OrbitalScene: () => <div data-testid="orbital-mock">3D</div>,
}))

function makeOverviewResponse(title = 'Test') {
  return new Response(
    JSON.stringify({
      conversation_id: 'conv_1',
      title,
      state: 'running',
      provider: 'google-antigravity',
      model_id: 'gemini-4-carbon-high',
      agents: [],
      tools: [],
      messages: [],
      errors: [],
      turns: [],
      activity_count: 0,
      duration_seconds: null,
      token_usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, usage_reported: false },
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  )
}

function makeEvents(
  count: number,
  lastType: ConversationActivityEvent['type'] = 'agent.thought_delta',
): ConversationActivityEvent[] {
  return Array.from({ length: count }, (_, i) => ({
    eventId: `evt_${i + 1}`,
    cursor: String(i + 1),
    type: i === count - 1 ? lastType : 'agent.thought_delta',
    kind: 'agent',
    state: 'working',
    agentId: 'agent:main',
    summary: 'streaming',
    occurredAt: '2026-11-15T15:00:00Z',
  }))
}

describe('OverviewPanel polling discipline', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('does not re-fetch /overview on every streaming thought_delta event, only on interval or throttled structural events', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => makeOverviewResponse())
    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })

    const { rerender } = render(
      <OverviewPanel client={client} conversationId="conv_1" liveEvents={makeEvents(1)} onClose={() => {}} />,
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(1)

    // Simulate 10 rapid streaming thought_delta events within 500ms: MUST NOT trigger 10 /overview fetches!
    for (let i = 2; i <= 11; i++) {
      rerender(
        <OverviewPanel client={client} conversationId="conv_1" liveEvents={makeEvents(i)} onClose={() => {}} />,
      )
    }
    await act(async () => {
      vi.advanceTimersByTime(500)
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(1)

    // Real structural event (`agent.created`) after >= 1500ms triggers exactly 1 refresh
    await act(async () => {
      vi.advanceTimersByTime(1600)
    })
    rerender(
      <OverviewPanel
        client={client}
        conversationId="conv_1"
        liveEvents={makeEvents(12, 'agent.created')}
        onClose={() => {}}
      />,
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('coalesces a rapid burst of structural events within 1500ms into a single trailing refresh and cleans up on unmount', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => makeOverviewResponse())
    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })

    const { rerender, unmount } = render(
      <OverviewPanel client={client} conversationId="conv_1" liveEvents={makeEvents(1)} onClose={() => {}} />,
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(1)

    // 200ms after initial load, emit 4 structural events in quick succession (including one followed by thought_delta)
    await act(async () => {
      vi.advanceTimersByTime(200)
    })
    rerender(
      <OverviewPanel
        client={client}
        conversationId="conv_1"
        liveEvents={makeEvents(2, 'agent.created')}
        onClose={() => {}}
      />,
    )
    rerender(
      <OverviewPanel
        client={client}
        conversationId="conv_1"
        liveEvents={makeEvents(3, 'delegation.created')}
        onClose={() => {}}
      />,
    )
    rerender(
      <OverviewPanel
        client={client}
        conversationId="conv_1"
        liveEvents={[
          ...makeEvents(3, 'delegation.completed'),
          {
            eventId: 'evt_4_delta',
            cursor: '4',
            type: 'agent.thought_delta',
            kind: 'agent',
            state: 'working',
            agentId: 'agent:main',
            summary: 'thinking',
          },
        ]}
        onClose={() => {}}
      />,
    )

    // Still within the 1500ms throttle window (only 200ms elapsed): must NOT have fetched yet
    expect(fetchImpl).toHaveBeenCalledTimes(1)

    // Advance remaining 1300ms to reach the 1500ms throttle boundary -> exactly 1 trailing fetch fires
    await act(async () => {
      vi.advanceTimersByTime(1300)
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(2)

    // Emit another structural event 100ms later, then unmount before the trailing timer fires
    await act(async () => {
      vi.advanceTimersByTime(100)
    })
    rerender(
      <OverviewPanel
        client={client}
        conversationId="conv_1"
        liveEvents={makeEvents(5, 'turn.completed')}
        onClose={() => {}}
      />,
    )
    unmount()

    await act(async () => {
      vi.advanceTimersByTime(5000)
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('guards overlapping /overview requests with inFlightRef when a fetch takes longer than 4000ms', async () => {
    let resolveFirst: ((res: Response) => void) | null = null
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          if (!resolveFirst) {
            resolveFirst = resolve
          } else {
            resolve(makeOverviewResponse())
          }
        }),
    )
    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })

    render(
      <OverviewPanel client={client} conversationId="conv_1" liveEvents={makeEvents(1)} onClose={() => {}} />,
    )
    expect(fetchImpl).toHaveBeenCalledTimes(1)

    // 4000ms interval ticks while first request is still in-flight -> must be skipped!
    await act(async () => {
      vi.advanceTimersByTime(4000)
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(1)

    // Resolve first request; next 4000ms tick should now proceed
    await act(async () => {
      resolveFirst?.(makeOverviewResponse())
      await Promise.resolve()
    })
    await act(async () => {
      vi.advanceTimersByTime(4000)
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('queues a trailing refresh when a structural event arrives while a fetch is in flight and resets cleanly on conversationId change', async () => {
    const resolvers: Array<(res: Response) => void> = []
    const requestedUrls: string[] = []
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      requestedUrls.push(url)
      return new Promise<Response>((resolve) => {
        resolvers.push(resolve)
      })
    })
    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })

    const { rerender } = render(
      <OverviewPanel client={client} conversationId="conv_1" liveEvents={makeEvents(1)} onClose={() => {}} />,
    )
    expect(fetchImpl).toHaveBeenCalledTimes(1)

    // Structural event arrives 300ms into the in-flight initial load
    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    rerender(
      <OverviewPanel
        client={client}
        conversationId="conv_1"
        liveEvents={makeEvents(2, 'delegation.failed')}
        onClose={() => {}}
      />,
    )
    // Must not start overlapping fetch while first is in flight
    expect(fetchImpl).toHaveBeenCalledTimes(1)

    // First fetch resolves at t = 600ms; trailing structural refresh must fire at t = 1500ms (900ms later)
    await act(async () => {
      vi.advanceTimersByTime(300)
      resolvers.shift()?.(makeOverviewResponse('Conv 1'))
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(900)
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(2)

    // Switch conversationId while conv_1's second request is still in-flight: conv_2 must load immediately
    rerender(
      <OverviewPanel client={client} conversationId="conv_2" liveEvents={[]} onClose={() => {}} />,
    )
    expect(fetchImpl).toHaveBeenCalledTimes(3)
    expect(requestedUrls[2]).toContain('/v1/conversations/conv_2/overview')
  })

  it('cancels pending throttle timer when 4000ms interval ticks and resets overview/error state when conversationId changes', async () => {
    let shouldFailConv2 = false
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      if (url.includes('/conv_2/') && shouldFailConv2) {
        return new Response(JSON.stringify({ detail: 'Failed' }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return makeOverviewResponse(url.includes('/conv_2/') ? 'Conv 2 Title' : 'Conv 1 Title')
    })
    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })

    const { rerender } = render(
      <OverviewPanel client={client} conversationId="conv_1" liveEvents={makeEvents(1)} onClose={() => {}} />,
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('heading', { name: 'Conv 1 Title' })).toBeInTheDocument()

    // At t = 3800ms, emit structural event #1 -> fires immediately (call #2)
    await act(async () => {
      vi.advanceTimersByTime(3800)
    })
    rerender(
      <OverviewPanel
        client={client}
        conversationId="conv_1"
        liveEvents={makeEvents(2, 'agent.created')}
        onClose={() => {}}
      />,
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(2)

    // At t = 3900ms, emit structural event #2 -> schedules trailing throttle timer for t = 5300ms
    await act(async () => {
      vi.advanceTimersByTime(100)
    })
    rerender(
      <OverviewPanel
        client={client}
        conversationId="conv_1"
        liveEvents={makeEvents(3, 'delegation.created')}
        onClose={() => {}}
      />,
    )
    expect(fetchImpl).toHaveBeenCalledTimes(2)

    // At t = 4000ms, the 4s interval fires (call #3) and MUST cancel the stale t = 5300ms throttle timer!
    await act(async () => {
      vi.advanceTimersByTime(100)
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(3)

    // Advance past t = 5300ms (up to t = 5500ms, before the next 8000ms interval): NO extra fetch may fire!
    await act(async () => {
      vi.advanceTimersByTime(1500)
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(3)

    // Switching to conv_2 when conv_2 fails MUST clear conv_1's stale overview and render the error alert
    shouldFailConv2 = true
    rerender(
      <OverviewPanel client={client} conversationId="conv_2" liveEvents={[]} onClose={() => {}} />,
    )
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.queryByRole('heading', { name: 'Conv 1 Title' })).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Não foi possível carregar a visão geral.')
  })

  it('resets lastStructuralIdRef when liveEvents empties so a recurring structural event ID triggers a refresh', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => makeOverviewResponse())
    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })

    const structuralEvents: ConversationActivityEvent[] = [
      {
        eventId: 'evt_struct_repeat',
        cursor: '1',
        type: 'agent.created',
        kind: 'agent',
        state: 'working',
        agentId: 'agent:main',
        summary: 'created',
        occurredAt: '2026-11-15T15:00:00Z',
      },
    ]

    const { rerender } = render(
      <OverviewPanel client={client} conversationId="conv_1" liveEvents={structuralEvents} onClose={() => {}} />,
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(1)

    // Briefly empty liveEvents
    rerender(<OverviewPanel client={client} conversationId="conv_1" liveEvents={[]} onClose={() => {}} />)

    // Advance past throttle window and re-emit the same structural event ID
    await act(async () => {
      vi.advanceTimersByTime(1600)
    })
    rerender(
      <OverviewPanel client={client} conversationId="conv_1" liveEvents={structuralEvents} onClose={() => {}} />,
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })
})
