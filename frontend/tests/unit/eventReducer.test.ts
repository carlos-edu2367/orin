import { describe, expect, it } from 'vitest'
import { ApiError, parseApiError, shouldResync } from '../../src/api/errors'
import type { ClientEvent, ExecutionView } from '../../src/api/types'
import { projectExecution } from '../../src/features/executions/executionProjection'
import {
  applyClientEvent,
  applyEventBatch,
  ProjectionInconsistencyError,
  resetDeliverySequences,
  type StreamState,
} from '../../src/features/realtime/eventReducer'

const snapshot = (overrides: Partial<ExecutionView> = {}): ExecutionView => ({
  execution_id: 'exec-1',
  agent_id: 'agent-orbit',
  state: 'RUNNING',
  state_version: 2,
  parent_execution_id: null,
  created_at: '2026-08-07T10:00:00.000Z',
  updated_at: '2026-08-07T10:00:02.000Z',
  finished_at: null,
  result: null,
  failure: null,
  ...overrides,
})

const event = (overrides: Partial<ClientEvent> = {}): ClientEvent => ({
  event_id: 'evt-3',
  event_type: 'ExecutionWaitingForUser',
  execution_id: 'exec-1',
  sequence: 3,
  occurred_at: '2026-08-07T10:00:03.000Z',
  payload: {
    from_state: 'RUNNING',
    to_state: 'WAITING_USER',
    state_version: 3,
    reason_code: 'input_required',
  },
  ...overrides,
})

const initialState = (...views: ExecutionView[]): StreamState => ({
  cursor: 'opaque-cursor-before',
  seenEventIds: [],
  executions: Object.fromEntries(views.map((view) => [view.execution_id, projectExecution(view)])),
})

describe('applyClientEvent', () => {
  it('deduplicates the same event_id and preserves the existing projection on replay', () => {
    const first = applyClientEvent(initialState(snapshot()), event())
    const replayed = applyClientEvent(first, event())

    expect(replayed).toBe(first)
    expect(replayed.seenEventIds).toEqual(['evt-3'])
    expect(replayed.executions['exec-1'].state).toBe('WAITING_USER')
    expect(replayed.executions['exec-1'].state_version).toBe(3)
  })

  it('records a late event without allowing an inferior sequence to regress the projection', () => {
    const state = initialState(snapshot({ state: 'WAITING_USER', state_version: 3 }))
    const late = event({
      event_id: 'evt-late',
      event_type: 'ExecutionStarted',
      sequence: 2,
      payload: { from_state: 'STARTING', to_state: 'RUNNING', state_version: 2 },
    })

    const next = applyClientEvent(state, late)

    expect(next.executions['exec-1'].state).toBe('WAITING_USER')
    expect(next.executions['exec-1'].state_version).toBe(3)
    expect(next.seenEventIds).toContain('evt-late')
  })

  it('accepts a transition with the next state_version', () => {
    const next = applyClientEvent(initialState(snapshot()), event())

    expect(next.executions['exec-1']).toMatchObject({
      state: 'WAITING_USER',
      state_version: 3,
      visual_status: 'Aguardando você',
      last_event_sequence: 3,
    })
  })

  it('keeps another execution isolated from the projected transition', () => {
    const other = snapshot({ execution_id: 'exec-2', state: 'PAUSED', state_version: 8 })

    const next = applyClientEvent(initialState(snapshot(), other), event())

    expect(next.executions['exec-2']).toEqual(projectExecution(other))
  })

  it('updates the opaque cursor only after the full batch applies successfully', () => {
    const state = initialState(snapshot())
    const next = applyEventBatch(state, [event()], 'opaque-cursor-after')

    expect(state.cursor).toBe('opaque-cursor-before')
    expect(next.cursor).toBe('opaque-cursor-after')
    expect(next.executions['exec-1'].state).toBe('WAITING_USER')
  })

  it('applies a transition whose delivery sequence is lower than the known state_version', () => {
    // The public stream numbers delivery globally, so a fresh binding may carry a
    // sequence far below the state_version already known from the snapshot.
    const state = initialState(snapshot({ state: 'RUNNING', state_version: 7 }))
    const delivered = event({
      event_id: 'evt-delivery-1',
      sequence: 1,
      payload: { from_state: 'RUNNING', to_state: 'WAITING_USER', state_version: 8 },
    })

    const next = applyClientEvent(state, delivered)

    expect(next.executions['exec-1'].state).toBe('WAITING_USER')
    expect(next.executions['exec-1'].state_version).toBe(8)
  })

  it('applies consecutive transitions when the delivery sequence skips another execution', () => {
    const other = snapshot({ execution_id: 'exec-2', state: 'RUNNING', state_version: 2 })
    const first = applyClientEvent(initialState(snapshot(), other), event({ event_id: 'evt-a', sequence: 1 }))
    const interleaved = applyClientEvent(first, event({
      event_id: 'evt-b',
      execution_id: 'exec-2',
      event_type: 'ExecutionCancelled',
      sequence: 2,
      payload: { from_state: 'RUNNING', to_state: 'CANCELLED', state_version: 3 },
    }))

    const next = applyClientEvent(interleaved, event({
      event_id: 'evt-c',
      event_type: 'ExecutionCancelled',
      sequence: 3,
      payload: { from_state: 'WAITING_USER', to_state: 'CANCELLED', state_version: 4 },
    }))

    expect(next.executions['exec-1'].state).toBe('CANCELLED')
    expect(next.executions['exec-1'].state_version).toBe(4)
    expect(next.executions['exec-2'].state).toBe('CANCELLED')
  })

  it('treats a transition redelivered under a new event_id as harmless', () => {
    const applied = applyClientEvent(initialState(snapshot()), event())
    const redelivered = applyClientEvent(applied, event({ event_id: 'evt-3-redelivered', sequence: 4 }))

    expect(redelivered.executions['exec-1']).toEqual({
      ...applied.executions['exec-1'],
      last_event_sequence: 4,
    })
    expect(redelivered.seenEventIds).toEqual(['evt-3', 'evt-3-redelivered'])
  })

  it('clears per-binding delivery sequences without touching projections or seen ids', () => {
    const applied = applyClientEvent(initialState(snapshot()), event())

    const rebound = resetDeliverySequences(applied)

    expect(rebound.executions['exec-1'].last_event_sequence).toBe(0)
    expect(rebound.executions['exec-1'].state).toBe('WAITING_USER')
    expect(rebound.executions['exec-1'].state_version).toBe(3)
    expect(rebound.seenEventIds).toEqual(['evt-3'])
  })

  it('rejects a sequence gap without changing the cursor or projection', () => {
    const state = initialState(snapshot())
    const gap = event({
      event_id: 'evt-5',
      sequence: 5,
      payload: { from_state: 'RUNNING', to_state: 'COMPLETED', state_version: 5 },
    })

    expect(() => applyEventBatch(state, [gap], 'opaque-cursor-after')).toThrow(ProjectionInconsistencyError)
    expect(state.cursor).toBe('opaque-cursor-before')
    expect(state.executions['exec-1'].state).toBe('RUNNING')
  })

  it('rejects a next-sequence lifecycle event whose state_version is not newer', () => {
    const state = initialState(snapshot())
    const inconsistent = event({
      event_id: 'evt-inconsistent',
      sequence: 3,
      payload: { from_state: 'RUNNING', to_state: 'WAITING_USER', state_version: 2 },
    })

    expect(() => applyEventBatch(state, [inconsistent], 'opaque-cursor-after')).toThrow(ProjectionInconsistencyError)
    expect(state.cursor).toBe('opaque-cursor-before')
    expect(state.seenEventIds).toEqual([])
  })
})

describe('shouldResync', () => {
  it.each([
    [400, 'VALIDATION', 'invalid_cursor'],
    [409, 'CONFLICT', 'retention_expired'],
    [403, 'AUTHORIZATION', 'access_denied'],
    [409, 'CONFLICT', 'stream_revoked'],
    [409, 'CONFLICT', 'sequence_gap'],
    [503, 'INDETERMINATE', 'indeterminate'],
  ])('requests reconciliation for status %i, category %s and code %s', (status, category, code) => {
    expect(shouldResync(new ApiError({ status, category, code }))).toBe(true)
  })

  it('does not request reconciliation for a normal field validation error', () => {
    expect(shouldResync(new ApiError({ status: 422, category: 'VALIDATION', code: 'invalid_request' }))).toBe(false)
  })
})

describe('parseApiError', () => {
  it('retains only the authorized public envelope and discards raw sensitive data', () => {
    const parsed = parseApiError(403, {
      error: {
        code: 'access_denied',
        category: 'AUTHORIZATION',
        message_key: 'access_denied',
        correlation_id: 'corr_public',
        retryable: false,
        retry_after: null,
        token: 'pat-secret',
        cursor: 'signed-cursor-secret',
        result_ref: 'opaque-result-secret',
        headers: { authorization: 'Bearer secret' },
        payload: { prompt: 'private prompt' },
      },
    })

    expect(parsed.toPublicDetails()).toEqual({
      status: 403,
      code: 'access_denied',
      category: 'AUTHORIZATION',
      messageKey: 'access_denied',
      correlationId: 'corr_public',
      retryable: false,
      retryAfter: null,
    })
    expect(JSON.stringify(parsed.toPublicDetails())).not.toMatch(/pat-secret|signed-cursor|opaque-result|Bearer|private prompt/)
  })
})
