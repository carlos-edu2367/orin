import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { OrinActivityCenter } from '../../src/components/OrinActivityCenter'
import { WorkspaceNavigation } from '../../src/features/projects/WorkspaceNavigation'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function makeConversationDetail(
  conversationId: string,
  state: string,
  activities: Array<Record<string, unknown>> = [],
) {
  return {
    conversation_id: conversationId,
    title: `Sessão ${conversationId}`,
    state,
    mode: 'code',
    permission_mode: 'ask_permissions',
    provider: 'google-antigravity',
    model_id: 'gemini-4-carbon-high',
    messages: [],
    turns: [
      {
        turn_id: `turn_${conversationId}`,
        state,
        created_at: '2026-11-15T15:00:00Z',
        started_at: '2026-11-15T15:00:01Z',
        finished_at: null,
        scheduled_by_schedule_id: null,
        code_mode: 'code',
      },
    ],
    activities,
    activity_cursor: String(activities.length),
    workspace: {},
    context_usage: null,
  }
}

function makeToolPermissionActivity(requestId: string, turnId: string) {
  return {
    id: 1,
    event_id: `evt_perm_${requestId}`,
    cursor: '1',
    turn_id: turnId,
    agent_id: 'agent:main',
    event_type: 'tool.permission_required',
    summary: 'Permissão para executar comando',
    payload: {
      tool_name: 'run_command',
      command: 'pytest -q',
      permission_request: {
        request_id: requestId,
        turn_id: turnId,
        tool_name: 'run_command',
        command: 'pytest -q',
        status: 'pending',
        state: 'pending',
      },
    },
  }
}

function makeWorkspaceTrustActivity(path: string) {
  return {
    id: 2,
    event_id: 'evt_trust_1',
    cursor: '2',
    turn_id: 'turn_1',
    agent_id: 'agent:main',
    event_type: 'workspace.trust_required',
    summary: `Confiar na pasta ${path}`,
    payload: {
      workspace_trust: {
        path,
        items: ['package.json'],
      },
    },
  }
}

function makePlanApprovalActivity() {
  return {
    id: 3,
    event_id: 'evt_plan_1',
    cursor: '3',
    turn_id: 'turn_1',
    agent_id: 'agent:main',
    event_type: 'code_mode.plan_ready',
    summary: 'Plano pronto para aprovação',
    payload: {
      plan_approval: true,
      plan_path: 'docs/plan.md',
      plan_content: '# Plano',
    },
  }
}

describe('OrinActivityCenter & WorkspaceNavigation HTTP/1.1 socket discipline', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('caps concurrent detail fetches at <= 2 and passes AbortSignal to fetchConversationWithRawActivities', async () => {
    let activeDetailFetches = 0
    let maxObservedDetailConcurrency = 0
    const pendingResolvers: Array<() => void> = []
    const observedSignals: Array<AbortSignal | undefined> = []

    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      if (url.endsWith('/v1/conversations')) {
        return jsonResponse({
          items: [
            { conversation_id: 'c1', title: 'C1', state: 'running', updated_at: '2026-11-15T15:00:00Z' },
            { conversation_id: 'c2', title: 'C2', state: 'running', updated_at: '2026-11-15T15:00:00Z' },
            { conversation_id: 'c3', title: 'C3', state: 'waiting_user', updated_at: '2026-11-15T15:00:00Z' },
            { conversation_id: 'c4', title: 'C4', state: 'streaming', updated_at: '2026-11-15T15:00:00Z' },
            { conversation_id: 'c5', title: 'C5', state: 'waiting_user', updated_at: '2026-11-15T15:00:00Z' },
          ],
        })
      }
      if (url.endsWith('/v1/projects/sidebar')) {
        return jsonResponse({ items: [] })
      }
      const match = url.match(/\/v1\/conversations\/(c\d+)$/)
      if (match) {
        const cid = match[1]!
        observedSignals.push(init?.signal ?? undefined)
        activeDetailFetches += 1
        maxObservedDetailConcurrency = Math.max(maxObservedDetailConcurrency, activeDetailFetches)
        await new Promise<void>((resolve) => {
          pendingResolvers.push(() => {
            activeDetailFetches -= 1
            resolve()
          })
        })
        return jsonResponse(makeConversationDetail(cid, cid === 'c3' || cid === 'c5' ? 'waiting_user' : 'running'))
      }
      return jsonResponse({})
    })

    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })
    render(
      <MemoryRouter>
        <OrinActivityCenter client={client} />
      </MemoryRouter>,
    )

    // Flush list fetches so detail worker pool starts
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    // With 5 candidates, only 2 detail requests may be in flight initially
    expect(activeDetailFetches).toBe(2)
    expect(maxObservedDetailConcurrency).toBe(2)

    // Drain all 5 queued detail fetches one by one
    while (pendingResolvers.length > 0) {
      const next = pendingResolvers.shift()!
      await act(async () => {
        next()
        await Promise.resolve()
        await Promise.resolve()
      })
      expect(maxObservedDetailConcurrency).toBeLessThanOrEqual(2)
    }

    expect(observedSignals).toHaveLength(5)
    for (const sig of observedSignals) {
      expect(sig).toBeInstanceOf(AbortSignal)
    }
  })

  it('caches only settled waiting_user sessions and never caches running sessions so in-turn approvals are detected immediately', async () => {
    let runningHasApproval = false
    const detailFetchCounts: Record<string, number> = { conv_wait: 0, conv_run: 0 }

    const fetchImpl = vi.fn<typeof fetch>(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      if (url.endsWith('/v1/conversations')) {
        // Notice BOTH conversations keep the exact same updated_at across polls!
        return jsonResponse({
          items: [
            {
              conversation_id: 'conv_wait',
              title: 'Waiting Session',
              state: 'waiting_user',
              updated_at: '2026-11-15T15:00:00Z',
            },
            {
              conversation_id: 'conv_run',
              title: 'Running Session',
              state: 'running',
              updated_at: '2026-11-15T15:00:00Z',
            },
          ],
        })
      }
      if (url.endsWith('/v1/projects/sidebar')) {
        return jsonResponse({ items: [] })
      }
      if (url.endsWith('/v1/conversations/conv_wait')) {
        detailFetchCounts.conv_wait += 1
        return jsonResponse(makeConversationDetail('conv_wait', 'waiting_user', []))
      }
      if (url.endsWith('/v1/conversations/conv_run')) {
        detailFetchCounts.conv_run += 1
        return jsonResponse(
          makeConversationDetail(
            'conv_run',
            'running',
            runningHasApproval ? [makeToolPermissionActivity('req_in_turn_1', 'turn_conv_run')] : [],
          ),
        )
      }
      return jsonResponse({})
    })

    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })
    render(
      <MemoryRouter>
        <OrinActivityCenter client={client} />
      </MemoryRouter>,
    )

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(detailFetchCounts.conv_wait).toBe(1)
    expect(detailFetchCounts.conv_run).toBe(1)
    expect(screen.queryByTestId('orin-activity-center-badge')).not.toBeInTheDocument()

    // Now an in-turn tool permission is emitted on conv_run WITHOUT changing conv_run.updated_at or state!
    runningHasApproval = true
    await act(async () => {
      window.dispatchEvent(new CustomEvent('orin:activity-center-refresh'))
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    // conv_wait MUST hit detailCacheRef (still 1 fetch), while conv_run MUST NOT be cached (2 fetches)
    expect(detailFetchCounts.conv_wait).toBe(1)
    expect(detailFetchCounts.conv_run).toBe(2)
    expect(screen.getByTestId('orin-activity-center-badge')).toHaveTextContent('1')
  })

  it('evicts detailCacheRef when resolving toolPermission, planApproval, or workspaceTrust on a waiting_user session', async () => {
    let stage: 'tool' | 'plan' | 'trust' | 'done' = 'tool'
    let waitDetailCalls = 0

    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/v1/conversations')) {
        return jsonResponse({
          items: [
            {
              conversation_id: 'conv_wait_act',
              title: 'Waiting Action Session',
              state: 'waiting_user',
              updated_at: '2026-11-15T15:00:00Z',
            },
          ],
        })
      }
      if (url.endsWith('/v1/projects/sidebar')) {
        return jsonResponse({ items: [] })
      }
      if (url.endsWith('/v1/conversations/conv_wait_act') && method === 'GET') {
        waitDetailCalls += 1
        const activities =
          stage === 'tool'
            ? [makeToolPermissionActivity('req_wait_1', 'turn_conv_wait_act')]
            : stage === 'plan'
              ? [makePlanApprovalActivity()]
              : stage === 'trust'
                ? [makeWorkspaceTrustActivity('C:/proj')]
                : []
        return jsonResponse(makeConversationDetail('conv_wait_act', 'waiting_user', activities))
      }
      if (url.includes('/tool-permission') && method === 'POST') {
        stage = 'plan'
        return jsonResponse({ ok: true })
      }
      if (url.endsWith('/v1/conversations/conv_wait_act/permission-mode') && method === 'PATCH') {
        return jsonResponse({ permission_mode: 'accept_edits' })
      }
      if (url.endsWith('/v1/conversations/conv_wait_act/messages') && method === 'POST') {
        stage = 'trust'
        return jsonResponse(
          {
            conversation_id: 'conv_wait_act',
            title: 'Waiting Action Session',
            turn_id: 'turn_2',
            message_id: 'msg_2',
            state: 'running',
            mode: 'code',
            permission_mode: 'accept_edits',
          },
          201,
        )
      }
      if (url.endsWith('/v1/conversations/conv_wait_act/workspace-trust') && method === 'POST') {
        stage = 'done'
        return jsonResponse({ trusted: true, path: 'C:/proj' })
      }
      return jsonResponse({})
    })

    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })
    render(
      <MemoryRouter>
        <OrinActivityCenter client={client} />
      </MemoryRouter>,
    )

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(waitDetailCalls).toBe(1)

    // Second refresh before resolving hits cache (still 1 detail call)
    await act(async () => {
      window.dispatchEvent(new CustomEvent('orin:activity-center-refresh'))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(waitDetailCalls).toBe(1)

    // Open popover and resolve toolPermission ("Permitir")
    fireEvent.click(screen.getByTestId('orin-activity-center-trigger'))
    fireEvent.click(await screen.findByTestId('activity-center-allow-once-req_wait_1'))
    await waitFor(() => {
      // Cache was evicted on toolPermission resolution -> detail call #2 loaded planApproval
      expect(waitDetailCalls).toBe(2)
    })

    // Resolve planApproval ("Aprovar plano")
    fireEvent.click(await screen.findByRole('button', { name: 'Aprovar plano' }))
    await waitFor(() => {
      // Cache was evicted on planApproval resolution -> detail call #3 loaded workspaceTrust
      expect(waitDetailCalls).toBe(3)
    })

    // Resolve workspaceTrust ("Confiar na pasta")
    fireEvent.click(await screen.findByRole('button', { name: 'Confiar na pasta' }))
    await waitFor(() => {
      // Cache was evicted on workspaceTrust resolution -> detail call #4 loaded empty approvals
      expect(waitDetailCalls).toBe(4)
    })
  })

  it('aborts in-flight detail requests when a newer refresh starts or on unmount', async () => {
    const detailSignals: AbortSignal[] = []

    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      if (url.endsWith('/v1/conversations')) {
        return jsonResponse({
          items: [
            { conversation_id: 'c_slow', title: 'Slow', state: 'running', updated_at: '2026-11-15T15:00:00Z' },
          ],
        })
      }
      if (url.endsWith('/v1/projects/sidebar')) {
        return jsonResponse({ items: [] })
      }
      if (url.endsWith('/v1/conversations/c_slow')) {
        const sig = init?.signal
        if (sig) detailSignals.push(sig)
        return await new Promise<Response>((_resolve, reject) => {
          sig?.addEventListener('abort', () => {
            reject(new DOMException('The request was aborted', 'AbortError'))
          })
        })
      }
      return jsonResponse({})
    })

    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })
    const { unmount } = render(
      <MemoryRouter>
        <OrinActivityCenter client={client} />
      </MemoryRouter>,
    )

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(detailSignals).toHaveLength(1)
    expect(detailSignals[0]!.aborted).toBe(false)

    // Triggering a newer refresh must abort the first in-flight detail request
    await act(async () => {
      window.dispatchEvent(new CustomEvent('orin:activity-center-refresh'))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(detailSignals[0]!.aborted).toBe(true)
    expect(detailSignals).toHaveLength(2)
    expect(detailSignals[1]!.aborted).toBe(false)

    // Unmounting must abort the second in-flight detail request
    unmount()
    expect(detailSignals[1]!.aborted).toBe(true)
  })

  it('WorkspaceNavigation guards reload() with inFlightRef + pendingReloadRef and polls every 3s for all 5 active states', async () => {
    vi.useFakeTimers()
    let activeReloads = 0
    let maxConcurrentReloads = 0
    let reloadCount = 0
    let pendingListResolvers: Array<() => void> = []

    const fetchImpl = vi.fn<typeof fetch>(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      if (url.endsWith('/v1/conversations')) {
        reloadCount += 1
        activeReloads += 1
        maxConcurrentReloads = Math.max(maxConcurrentReloads, activeReloads)
        await new Promise<void>((resolve) => {
          pendingListResolvers.push(() => {
            activeReloads -= 1
            resolve()
          })
        })
        return jsonResponse({
          items: [
            { conversation_id: 'c_cancelling', title: 'Cancelling chat', state: 'cancelling', updated_at: null },
          ],
        })
      }
      if (url.endsWith('/v1/projects/sidebar')) {
        return jsonResponse({ items: [] })
      }
      return jsonResponse({})
    })

    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })
    render(
      <MemoryRouter>
        <WorkspaceNavigation client={client} />
      </MemoryRouter>,
    )

    // Flush initial queueMicrotask reload
    await act(async () => {
      await Promise.resolve()
    })
    expect(reloadCount).toBe(1)
    expect(activeReloads).toBe(1)

    // Fire 4 update events while the first reload is still in flight: MUST NOT start concurrent reloads!
    await act(async () => {
      window.dispatchEvent(new CustomEvent('orin:conversations-updated'))
      window.dispatchEvent(new CustomEvent('orin:projects-updated'))
      window.dispatchEvent(new CustomEvent('orin:conversations-updated'))
      await Promise.resolve()
    })
    expect(reloadCount).toBe(1)
    expect(maxConcurrentReloads).toBe(1)

    // Resolve first reload -> should trigger exactly ONE trailing reload for the coalesced events
    const firstResolve = pendingListResolvers.shift()!
    await act(async () => {
      firstResolve()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(reloadCount).toBe(2)
    expect(maxConcurrentReloads).toBe(1)

    // Resolve trailing reload -> no more pending reloads until 3s active timer ticks
    const secondResolve = pendingListResolvers.shift()!
    await act(async () => {
      secondResolve()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(reloadCount).toBe(2)

    // Because `cancelling` is preserved in the 5 active states, advancing 3000ms triggers a poll
    await act(async () => {
      vi.advanceTimersByTime(3000)
      await Promise.resolve()
    })
    expect(reloadCount).toBe(3)
    pendingListResolvers.shift()?.()
  })

  it('continues draining the 2-worker detail queue when one conversation detail request fails with HTTP 500', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      if (url.endsWith('/v1/conversations')) {
        return jsonResponse({
          items: [
            { conversation_id: 'c_ok_1', title: 'OK 1', state: 'running', updated_at: '2026-11-15T15:00:00Z' },
            { conversation_id: 'c_fail_2', title: 'Fail 2', state: 'running', updated_at: '2026-11-15T15:00:00Z' },
            { conversation_id: 'c_ok_3', title: 'OK 3', state: 'running', updated_at: '2026-11-15T15:00:00Z' },
            { conversation_id: 'c_ok_4', title: 'OK 4', state: 'running', updated_at: '2026-11-15T15:00:00Z' },
          ],
        })
      }
      if (url.endsWith('/v1/projects/sidebar')) {
        return jsonResponse({ items: [] })
      }
      if (url.endsWith('/v1/conversations/c_fail_2')) {
        return jsonResponse({ detail: 'Internal error' }, 500)
      }
      if (url.endsWith('/v1/conversations/c_ok_4')) {
        return jsonResponse(
          makeConversationDetail('c_ok_4', 'running', [
            makeToolPermissionActivity('req_ok_4', 'turn_c_ok_4'),
          ]),
        )
      }
      const match = url.match(/\/v1\/conversations\/(c_ok_\d+)$/)
      if (match) {
        return jsonResponse(makeConversationDetail(match[1]!, 'running', []))
      }
      return jsonResponse({})
    })

    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })
    render(
      <MemoryRouter>
        <OrinActivityCenter client={client} />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('orin-activity-center-badge')).toHaveTextContent('1')
    })
  })

  it('aborts in-flight list requests on rapid refresh events and preserves detailCacheRef + approvals on transient /v1/conversations failure', async () => {
    const listSignals: AbortSignal[] = []
    let failConvList = false
    let waitDetailCalls = 0
    let holdListRequest = false
    let releaseHeldList: (() => void) | null = null

    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      if (url.endsWith('/v1/conversations')) {
        if (init?.signal) listSignals.push(init.signal)
        if (holdListRequest) {
          await new Promise<void>((resolve, reject) => {
            releaseHeldList = resolve
            init?.signal?.addEventListener('abort', () => {
              reject(new DOMException('The request was aborted', 'AbortError'))
            })
          })
        }
        if (failConvList) {
          return jsonResponse({ detail: 'Gateway restarting' }, 502)
        }
        return jsonResponse({
          items: [
            {
              conversation_id: 'conv_cached_wait',
              title: 'Cached Waiting',
              state: 'waiting_user',
              updated_at: '2026-11-15T15:00:00Z',
            },
          ],
        })
      }
      if (url.endsWith('/v1/projects/sidebar')) {
        if (init?.signal) listSignals.push(init.signal)
        return jsonResponse({ items: [] })
      }
      if (url.endsWith('/v1/conversations/conv_cached_wait')) {
        waitDetailCalls += 1
        return jsonResponse(
          makeConversationDetail('conv_cached_wait', 'waiting_user', [
            makeWorkspaceTrustActivity('C:/proj'),
          ]),
        )
      }
      return jsonResponse({})
    })

    const client = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl, maxAttempts: 1 })
    render(
      <MemoryRouter>
        <OrinActivityCenter client={client} />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('orin-activity-center-badge')).toHaveTextContent('1')
    })
    expect(waitDetailCalls).toBe(1)
    expect(listSignals.length).toBeGreaterThanOrEqual(2)

    // Hold next list request and fire a second refresh event -> first list request's AbortSignal must abort!
    holdListRequest = true
    const signalCountBefore = listSignals.length
    await act(async () => {
      window.dispatchEvent(new CustomEvent('orin:conversations-updated'))
      await Promise.resolve()
    })
    const heldConvSignal = listSignals[signalCountBefore]!
    expect(heldConvSignal).toBeInstanceOf(AbortSignal)
    expect(heldConvSignal.aborted).toBe(false)

    holdListRequest = false
    await act(async () => {
      window.dispatchEvent(new CustomEvent('orin:activity-center-refresh'))
      releaseHeldList?.()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(heldConvSignal.aborted).toBe(true)
    expect(waitDetailCalls).toBe(1)

    // Simulate transient 502 on /v1/conversations: MUST NOT wipe existing approvals or evict detailCacheRef!
    failConvList = true
    await act(async () => {
      window.dispatchEvent(new CustomEvent('orin:activity-center-refresh'))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.getByTestId('orin-activity-center-badge')).toHaveTextContent('1')

    // Recover /v1/conversations: detailCacheRef must STILL be intact (waitDetailCalls remains 1)!
    failConvList = false
    await act(async () => {
      window.dispatchEvent(new CustomEvent('orin:activity-center-refresh'))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(waitDetailCalls).toBe(1)
    expect(screen.getByTestId('orin-activity-center-badge')).toHaveTextContent('1')
  })

  it('preserves project-chat approvals and detailCacheRef on transient /v1/projects/sidebar 5xx failure and clears detailCacheRef on client swap', async () => {
    let failProjectSidebar = false
    let projDetailCallsA = 0
    let projDetailCallsB = 0

    const makeFetch = (onDetailCall: () => void) =>
      vi.fn<typeof fetch>(async (input) => {
        const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
        if (url.endsWith('/v1/conversations')) {
          return jsonResponse({ items: [] })
        }
        if (url.endsWith('/v1/projects/sidebar')) {
          if (failProjectSidebar) {
            return jsonResponse({ detail: 'Sidebar 502' }, 502)
          }
          return jsonResponse({
            items: [
              {
                project_id: 'proj_1',
                name: 'Projeto 1',
                description: null,
                chats: [
                  {
                    conversation_id: 'conv_proj_wait',
                    title: 'Project Waiting Chat',
                    state: 'waiting_user',
                    mode: 'code',
                    updated_at: '2026-11-15T15:00:00Z',
                  },
                ],
              },
            ],
          })
        }
        if (url.endsWith('/v1/conversations/conv_proj_wait')) {
          onDetailCall()
          return jsonResponse(
            makeConversationDetail('conv_proj_wait', 'waiting_user', [
              makeWorkspaceTrustActivity('C:/proj_1'),
            ]),
          )
        }
        return jsonResponse({})
      })

    const clientA = new ApiClient({
      baseUrl: 'http://localhost:8000',
      fetchImpl: makeFetch(() => {
        projDetailCallsA += 1
      }),
      maxAttempts: 1,
    })
    const clientB = new ApiClient({
      baseUrl: 'http://localhost:8000',
      fetchImpl: makeFetch(() => {
        projDetailCallsB += 1
      }),
      maxAttempts: 1,
    })

    const { rerender } = render(
      <MemoryRouter>
        <OrinActivityCenter client={clientA} />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('orin-activity-center-badge')).toHaveTextContent('1')
    })
    expect(projDetailCallsA).toBe(1)

    // Simulate transient 502 on /v1/projects/sidebar while /v1/conversations succeeds
    failProjectSidebar = true
    await act(async () => {
      window.dispatchEvent(new CustomEvent('orin:activity-center-refresh'))
      await Promise.resolve()
      await Promise.resolve()
    })
    // Must preserve project approval badge and cached detail
    expect(screen.getByTestId('orin-activity-center-badge')).toHaveTextContent('1')

    // Recover /v1/projects/sidebar: detailCacheRef must still have conv_proj_wait cached!
    failProjectSidebar = false
    await act(async () => {
      window.dispatchEvent(new CustomEvent('orin:activity-center-refresh'))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(projDetailCallsA).toBe(1)
    expect(screen.getByTestId('orin-activity-center-badge')).toHaveTextContent('1')

    // Swap clientA -> clientB: detailCacheRef must clear so clientB fetches its own detail snapshot
    rerender(
      <MemoryRouter>
        <OrinActivityCenter client={clientB} />
      </MemoryRouter>,
    )
    await waitFor(() => {
      expect(projDetailCallsB).toBe(1)
    })
  })

  it('WorkspaceNavigation ignores stale in-flight clientA reload when swapped to clientB and does not loop on inline onChatsChange', async () => {
    let resolveClientA: (() => void) | null = null
    let clientACalls = 0
    let clientBCalls = 0

    const fetchA = vi.fn<typeof fetch>(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      if (url.endsWith('/v1/conversations')) {
        clientACalls += 1
        await new Promise<void>((resolve) => {
          resolveClientA = resolve
        })
        return jsonResponse({
          items: [{ conversation_id: 'chat_stale_a', title: 'Stale A', state: 'completed', updated_at: null }],
        })
      }
      return jsonResponse({ items: [] })
    })

    const fetchB = vi.fn<typeof fetch>(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
      if (url.endsWith('/v1/conversations')) {
        clientBCalls += 1
        return jsonResponse({
          items: [{ conversation_id: 'chat_fresh_b', title: 'Fresh B', state: 'completed', updated_at: null }],
        })
      }
      return jsonResponse({ items: [] })
    })

    const clientA = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl: fetchA, maxAttempts: 1 })
    const clientB = new ApiClient({ baseUrl: 'http://localhost:8000', fetchImpl: fetchB, maxAttempts: 1 })

    const observedTitles: string[][] = []
    const { rerender } = render(
      <MemoryRouter>
        <WorkspaceNavigation
          client={clientA}
          onChatsChange={(chats) => observedTitles.push(chats.map((c) => c.title))}
        />
      </MemoryRouter>,
    )

    await act(async () => {
      await Promise.resolve()
    })
    expect(clientACalls).toBe(1)

    // Swap to clientB with a brand new inline onChatsChange while clientA's reload is still in flight
    rerender(
      <MemoryRouter>
        <WorkspaceNavigation
          client={clientB}
          onChatsChange={(chats) => observedTitles.push(chats.map((c) => c.title))}
        />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('Fresh B')).toBeInTheDocument()
    })
    expect(clientBCalls).toBe(1)

    // Now resolve clientA's slow request: it MUST NOT overwrite Fresh B or re-query clientA!
    await act(async () => {
      resolveClientA?.()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.getByText('Fresh B')).toBeInTheDocument()
    expect(screen.queryByText('Stale A')).not.toBeInTheDocument()
    expect(clientACalls).toBe(1)
    expect(clientBCalls).toBe(1)
    expect(observedTitles).toEqual([['Fresh B']])
  })
})
