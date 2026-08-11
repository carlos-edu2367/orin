export type ActivityCategory = 'lifecycle' | 'tool' | 'delegation' | 'resource'

export type Activity = {
  id: string
  executionId: string
  category: ActivityCategory
  state: string
  eventIds: string[]
}

/**
 * The shape BACKEND_DISCOVERY.md documents for a future Tool Runtime -> SSE bridge.
 * No adapter publishes this today; it exists so the normalizer and UI are written
 * against the authorized field names instead of inventing their own.
 */
export type ToolActivityView = {
  invocation_id: string
  execution_id: string
  tool_kind: string
  state: string
  occurred_at: string
  progress_kind?: string
  error_code?: string
  summary?: string
}

// The only event family the public stream authorizes today (BACKEND_DISCOVERY.md).
export const lifecycleEventTypes = new Set<string>([
  'ExecutionQueued',
  'ExecutionStarted',
  'ExecutionWaitingForTool',
  'ExecutionWaitingForUser',
  'ExecutionPaused',
  'ExecutionResumed',
  'ExecutionFinished',
  'ExecutionFailed',
  'ExecutionCancelled',
])

// Not authorized on the public stream yet; kept only so the category exists in the
// type once a delegation projection is composed (Fase 4).
export const delegationEventTypes = new Set<string>([
  'AgentMessageCreated',
  'AgentMessageExpired',
  'DelegationCreated',
  'StructuredHandoffCreated',
  'DelegationCompleted',
  'DelegationFailed',
  'DelegationCancelled',
  'DelegationResultReturned',
  'AgentWaitRegistered',
  'AgentWaitCancelled',
  'AgentWaitSatisfied',
])
