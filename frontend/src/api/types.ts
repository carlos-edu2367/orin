export const executionStates = [
  'QUEUED',
  'STARTING',
  'RUNNING',
  'WAITING_TOOL',
  'WAITING_USER',
  'PAUSED',
  'COMPLETED',
  'FAILED',
  'CANCELLED',
] as const

export type ExecutionState = (typeof executionStates)[number]

export type ExecutionView = {
  execution_id: string
  agent_id: string
  state: ExecutionState
  state_version: number
  parent_execution_id: string | null
  created_at: string
  updated_at: string
  finished_at: string | null
  result: { display_text?: string; result_ref: string } | null
  failure: { code: string } | null
}

export type ClientEvent = {
  event_id: string
  event_type: string
  execution_id: string
  sequence: number
  occurred_at: string
  payload: Record<string, unknown>
}

export type ExecutionCommandReceipt = {
  outcome: string
  execution_id: string
  state_version: number
}

export type CreateExecutionInput = {
  agent_id: string
  task_ref: string
  workspace_id: string | null
  limits: Record<string, number | null>
  expected_agent_version: number | null
}

export type ExecutionControlAction = 'PAUSE' | 'RESUME' | 'CANCEL'

export type ControlExecutionInput = {
  action: ExecutionControlAction
  expected_state_version: number | null
  reason?: string
}

export type ExecutionInput = {
  input_ref: string
  expected_state_version: number
}

export type OpenStreamResponse = {
  stream_id: string
  cursor: string
  stream_binding_digest: string
  revocation_epoch: number
}

export type ReadStreamResponse = {
  events: ClientEvent[]
  cursor: string
}
