import { waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { ExecutionRealtimeStore } from '../../src/features/realtime/realtimeStore'

describe('ExecutionRealtimeStore bootstrap', () => {
  it('publishes the authorized snapshot before opening and reading its stream', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input)
      if (path === '/v1/executions/exec-store') {
        return json({
          execution_id: 'exec-store',
          agent_id: 'agent-orbit',
          state: 'RUNNING',
          state_version: 2,
          parent_execution_id: null,
          created_at: '2026-08-07T10:00:00.000Z',
          updated_at: '2026-08-07T10:00:02.000Z',
          finished_at: null,
          result: null,
          failure: null,
        })
      }
      if (path === '/v1/events/streams') {
        return json({ stream_id: 'stream-store', cursor: 'cursor-2', stream_binding_digest: 'digest', revocation_epoch: 1 }, 201)
      }
      if (path === '/v1/events/streams/stream-store/read') {
        expect(JSON.parse(String(init?.body))).toEqual({ cursor: 'cursor-2', maximum_events: 100 })
        return json({ events: [], cursor: 'cursor-2' })
      }
      throw new Error('unexpected public API path')
    })
    const store = new ExecutionRealtimeStore({
      client: new ApiClient({ fetchImpl, maxAttempts: 1 }),
      executionId: 'exec-store',
      pollDelayMs: 20,
    })

    store.start()
    await waitFor(() => expect(store.getSnapshot().executions['exec-store']?.state).toBe('RUNNING'))
    await waitFor(() => expect(store.getSnapshot().connection).toBe('live'))
    store.stop()

    expect(fetchImpl.mock.calls.map(([path]) => String(path))).toEqual(expect.arrayContaining([
      '/v1/executions/exec-store',
      '/v1/events/streams',
    ]))
  })
})

describe('ExecutionRealtimeStore rebinding', () => {
  it('applies events from a new binding even when its delivery sequence restarts lower', async () => {
    let opens = 0
    let reads = 0
    const fetchImpl = vi.fn<typeof fetch>(async (input) => {
      const path = String(input)
      if (path === '/v1/executions/exec-rebind') return json(view('RUNNING', 2))
      if (path === '/v1/events/streams') {
        opens += 1
        return json({ stream_id: `stream-${opens}`, cursor: `cursor-${opens}`, stream_binding_digest: 'digest', revocation_epoch: 1 }, 201)
      }
      reads += 1
      if (reads === 1) {
        return json({ events: [transition('evt-far', 10, 'WAITING_USER', 3)], cursor: 'cursor-far' })
      }
      if (reads === 2) throw new TypeError('network unavailable')
      // The second binding restarts delivery numbering at 1 for a newer transition.
      return json({ events: [transition('evt-near', 1, 'CANCELLED', 4)], cursor: 'cursor-near' })
    })
    const store = new ExecutionRealtimeStore({
      client: new ApiClient({ fetchImpl, maxAttempts: 1 }),
      executionId: 'exec-rebind',
      pollDelayMs: 20,
      recoveryDelayMs: 20,
    })

    store.start()
    await waitFor(() => expect(store.getSnapshot().executions['exec-rebind']?.state).toBe('CANCELLED'))
    store.stop()

    expect(store.getSnapshot().executions['exec-rebind']?.state_version).toBe(4)
    expect(opens).toBeGreaterThan(1)
  })
})

function view(state: string, stateVersion: number) {
  return {
    execution_id: 'exec-rebind',
    agent_id: 'agent-orbit',
    state,
    state_version: stateVersion,
    parent_execution_id: null,
    created_at: '2026-08-07T10:00:00.000Z',
    updated_at: '2026-08-07T10:00:02.000Z',
    finished_at: null,
    result: null,
    failure: null,
  }
}

function transition(eventId: string, sequence: number, toState: string, stateVersion: number) {
  return {
    event_id: eventId,
    event_type: toState === 'WAITING_USER' ? 'ExecutionWaitingForUser' : 'ExecutionCancelled',
    execution_id: 'exec-rebind',
    sequence,
    occurred_at: '2026-08-07T10:00:05.000Z',
    payload: { from_state: 'RUNNING', to_state: toState, state_version: stateVersion },
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
