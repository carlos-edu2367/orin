import type { ClientEvent, ExecutionState } from '../../api/types'
import { executionStates } from '../../api/types'
import { toVisualStatus } from '../executions/executionStatus'
import type { ExecutionProjection } from '../executions/executionProjection'

export type StreamState = {
  cursor: string | null
  seenEventIds: string[]
  executions: Record<string, ExecutionProjection>
}

const stateSet = new Set<string>(executionStates)
const terminalStates = new Set<ExecutionState>(['COMPLETED', 'FAILED', 'CANCELLED'])
const eventTargets: Record<string, ReadonlySet<ExecutionState>> = {
  ExecutionQueued: new Set(['QUEUED', 'STARTING']),
  ExecutionStarted: new Set(['RUNNING']),
  ExecutionWaitingForTool: new Set(['WAITING_TOOL']),
  ExecutionWaitingForUser: new Set(['WAITING_USER']),
  ExecutionPaused: new Set(['PAUSED']),
  ExecutionResumed: new Set(['QUEUED']),
  ExecutionFinished: new Set(['COMPLETED']),
  ExecutionFailed: new Set(['FAILED']),
  ExecutionCancelled: new Set(['CANCELLED']),
}

export class ProjectionInconsistencyError extends Error {
  constructor() {
    super('Execution projection requires a fresh snapshot')
    this.name = 'ProjectionInconsistencyError'
  }
}

export function applyClientEvent(state: StreamState, event: ClientEvent): StreamState {
  if (state.seenEventIds.includes(event.event_id)) return state
  const current = state.executions[event.execution_id]
  if (!current) throw new ProjectionInconsistencyError()

  const seenEventIds = [...state.seenEventIds, event.event_id]
  // `sequence` orders delivery inside the current binding only. The public stream
  // numbers delivery globally, so it must never be compared against state_version.
  if (event.sequence <= current.last_event_sequence) return { ...state, seenEventIds }
  const acknowledged: StreamState = {
    ...state,
    seenEventIds,
    executions: {
      ...state.executions,
      [event.execution_id]: { ...current, last_event_sequence: event.sequence },
    },
  }

  const targets = eventTargets[event.event_type]
  if (!targets) return acknowledged

  const targetState = event.payload.to_state
  const targetVersion = event.payload.state_version
  if (typeof targetState !== 'string'
    || !stateSet.has(targetState)
    || !targets.has(targetState as ExecutionState)
    || !Number.isInteger(targetVersion)
    || (targetVersion as number) < 1) {
    throw new ProjectionInconsistencyError()
  }

  const nextState = targetState as ExecutionState
  // A late redelivery is harmless; it may never regress a known state_version.
  if ((targetVersion as number) < current.state_version) return acknowledged
  if ((targetVersion as number) === current.state_version) {
    if (nextState !== current.state) throw new ProjectionInconsistencyError()
    return acknowledged
  }
  // state_version is per execution and contiguous, so a jump means a missed event.
  if (targetVersion !== current.state_version + 1) throw new ProjectionInconsistencyError()

  const next: ExecutionProjection = {
    ...current,
    state: nextState,
    state_version: targetVersion as number,
    updated_at: event.occurred_at,
    finished_at: terminalStates.has(nextState) ? event.occurred_at : null,
    visual_status: toVisualStatus(nextState),
    last_event_sequence: event.sequence,
  }
  return {
    ...state,
    seenEventIds,
    executions: { ...state.executions, [event.execution_id]: next },
  }
}

/** A new binding restarts delivery numbering; projections and seen ids survive it. */
export function resetDeliverySequences(state: StreamState): StreamState {
  const executions = Object.fromEntries(
    Object.entries(state.executions).map(([id, projection]) => [id, { ...projection, last_event_sequence: 0 }]),
  )
  return { ...state, executions }
}

export function applyEventBatch(state: StreamState, events: ClientEvent[], nextCursor: string): StreamState {
  const next = events.reduce(applyClientEvent, state)
  return next.cursor === nextCursor ? next : { ...next, cursor: nextCursor }
}
