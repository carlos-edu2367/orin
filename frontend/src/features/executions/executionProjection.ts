import type { ExecutionView } from '../../api/types'
import { toVisualStatus, type VisualStatus } from './executionStatus'

export type ExecutionProjection = ExecutionView & {
  /** Derived only from the persisted ExecutionState; never sent to the backend. */
  visual_status: VisualStatus
  /** Highest delivery sequence applied inside the current stream binding. */
  last_event_sequence: number
}

/**
 * Boundary between the HTTP DTO and the projection the UI renders. A snapshot
 * carries no stream position, so delivery numbering starts unknown at zero.
 */
export function projectExecution(view: ExecutionView): ExecutionProjection {
  return {
    ...view,
    result: view.result ? { ...view.result } : null,
    failure: view.failure ? { ...view.failure } : null,
    visual_status: toVisualStatus(view.state),
    last_event_sequence: 0,
  }
}
