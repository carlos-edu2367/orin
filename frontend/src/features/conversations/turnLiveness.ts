import type { ConversationActivityEvent } from './activityTypes'

export type TerminalConversationState = 'completed' | 'failed' | 'cancelled' | 'waiting_user'

export function terminalConversationState(event: ConversationActivityEvent): TerminalConversationState | null {
  if (
    event.type === 'turn.completed'
    || event.type === 'TURN_COMPLETED'
    || event.type === 'code_mode.completed'
    || event.type === 'code_mode.completed_with_caveats'
  ) {
    return 'completed'
  }
  if (
    event.type === 'turn.cancelled'
    || event.type === 'TURN_CANCELLED'
    || event.type === 'execution.canceled'
    || event.type === 'EXECUTION_CANCELED'
  ) {
    return 'cancelled'
  }
  if (event.type === 'turn.failed' || event.type === 'TURN_FAILED') {
    return event.state === 'cancelled' || event.status === 'cancelled' || event.errorCode === 'TURN_CANCELLED'
      ? 'cancelled'
      : 'failed'
  }
  if (
    (event.type === 'turn.waiting_user' && event.payload?.in_turn !== true)
    || event.type === 'code_mode.blocked'
    || event.type === 'code_mode.plan_ready'
    || event.type === 'tool.permission_required'
    || Boolean(event.planApproval)
    || event.tool === 'exit_plan_mode'
    || event.toolName === 'exit_plan_mode'
  ) {
    return 'waiting_user'
  }
  return null
}

const normalizedType = (type: string) => type.toLowerCase().replace(/_/g, '.')

// Root events that only a turn still working emits: the model kept calling tools, writing or waiting inside the
// turn for an ask_user answer. Bookkeeping that can trail a stopped turn (context, speech, artifacts) is not here.
const CONTINUATION_TYPES = new Set([
  'tool.requested',
  'tool.started',
  'tool.progressed',
  'tool.finished',
  'tool.completed',
  'tool.failed',
  'assistant.delta',
  'agent.thought.delta',
  'agent.message.sent',
].map(normalizedType))

function isContinuation(event: ConversationActivityEvent): boolean {
  if (event.type === 'turn.waiting_user' || event.type === 'TURN_WAITING_USER') return event.payload?.in_turn === true
  return CONTINUATION_TYPES.has(normalizedType(event.type))
}

/**
 * Turns a root (non-subagent) terminal event already ended; `anyTurn` when such an event names no turn.
 *
 * completed / failed / cancelled always end the turn. A "waiting" event (exit_plan_mode, a plan approval, a
 * permission request, a classic waiting_user) ends it only when the turn stopped there: a native turn in full
 * autonomy can call exit_plan_mode and keep going (then ask_user and wait inside the turn), and treating it as
 * ended sent the answer as a new turn queued behind the one still waiting for it.
 */
export function rootTerminalTurns(events: readonly ConversationActivityEvent[]): { ids: Set<string>; anyTurn: boolean } {
  const ended = new Set<string>()
  const waiting = new Set<string>()
  let anyTurn = false
  for (const event of events) {
    if (event.parentAgentId) continue
    const state = terminalConversationState(event)
    if (!event.turnId) {
      if (state !== null) anyTurn = true
      continue
    }
    if (state === 'completed' || state === 'failed' || state === 'cancelled') {
      ended.add(event.turnId)
    } else if (state === 'waiting_user') {
      waiting.add(event.turnId)
    } else if (isContinuation(event)) {
      waiting.delete(event.turnId)
    }
  }
  return { ids: new Set([...ended, ...waiting]), anyTurn }
}
