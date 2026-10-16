import type { ActivityEvent, ActivityToolCall, ConversationActivityEvent, TodoItem } from './activityTypes'

export type { ActivityEvent, ActivityToolCall }
export type ActivityConnectionState = 'snapshot' | 'connecting' | 'live' | 'degraded' | 'resyncing'
export type TurnLifecycleStatus = 'idle' | 'running' | 'completed' | 'failed' | 'cancelled'

export type ActivityStoreState = {
  events: ConversationActivityEvent[]
  cursor: string
  connection: ActivityConnectionState
  resyncRequired: boolean
  todos: TodoItem[]
  activePhase: string | null
  status: TurnLifecycleStatus
}

export type TurnActivityState = ActivityStoreState

export type ActivityAction =
  | { type: 'snapshot'; events: ConversationActivityEvent[]; cursor: string }
  | { type: 'prepend_older'; events: ConversationActivityEvent[] }
  | { type: 'event'; event: ConversationActivityEvent }
  | { type: 'cursor'; cursor: string }
  | { type: 'connection'; connection: ActivityConnectionState }
  | { type: 'resync' }

export const EVENT_LIMIT = 1500

const TERMINAL_COMPLETED_TYPES = new Set([
  'turn.completed',
  'TURN_COMPLETED',
  'code_mode.completed_with_caveats',
])

const TERMINAL_FAILED_TYPES = new Set([
  'turn.failed',
  'TURN_FAILED',
  'code_mode.blocked',
])

const TERMINAL_CANCELLED_TYPES = new Set([
  'turn.cancelled',
  'TURN_CANCELLED',
  'execution.canceled',
  'EXECUTION_CANCELED',
])

const ACTIVE_ACTIVITY_STATES = new Set([
  'queued',
  'working',
  'waiting_tool',
  'waiting_agent',
  'waiting_user',
  'retrying',
])

export function terminalOutcomeFor(event: ActivityEvent): 'completed' | 'failed' | 'cancelled' | null {
  if (event.parentAgentId) return null
  if (TERMINAL_CANCELLED_TYPES.has(event.type)) return 'cancelled'
  if (TERMINAL_FAILED_TYPES.has(event.type)) {
    return event.errorCode === 'TURN_CANCELLED' || event.status === 'cancelled' || event.state === 'cancelled'
      ? 'cancelled'
      : 'failed'
  }
  if (TERMINAL_COMPLETED_TYPES.has(event.type)) {
    if (event.errorCode === 'TURN_CANCELLED' || event.status === 'cancelled' || event.state === 'cancelled') {
      return 'cancelled'
    }
    if (event.status === 'failed' || event.state === 'failed' || Boolean(event.errorCode)) {
      return 'failed'
    }
    return 'completed'
  }
  return null
}

function resolveTurnOutcome(
  event: ConversationActivityEvent,
  priorEvents: ConversationActivityEvent[],
): 'completed' | 'failed' | 'cancelled' | null {
  const raw = terminalOutcomeFor(event)
  if (!raw) return null
  if (raw === 'completed' && event.turnId) {
    for (const item of priorEvents) {
      if (item.turnId !== event.turnId) continue
      const priorOutcome = terminalOutcomeFor(item)
      if (priorOutcome === 'cancelled') return 'cancelled'
      if (priorOutcome === 'failed') return 'failed'
    }
  }
  return raw
}

function settleTurnEvents(
  events: ConversationActivityEvent[],
  turnId: string | undefined,
  outcome: 'completed' | 'failed' | 'cancelled',
): ConversationActivityEvent[] {
  const settledState =
    outcome === 'cancelled' ? 'cancelled' : outcome === 'failed' ? 'failed' : 'completed'
  const settledStatus =
    outcome === 'cancelled' ? 'cancelled' : outcome === 'failed' ? 'failed' : 'succeeded'
  let mutated = false
  const next = events.map((item) => {
    const sameTurn = !turnId || !item.turnId || item.turnId === turnId
    if (!sameTurn) return item
    const isStuckState = ACTIVE_ACTIVITY_STATES.has(item.state)
    const isStuckStatus = item.status === 'running' || item.status === 'started' || item.status === 'in_progress'
    if (!isStuckState && !isStuckStatus) return item
    mutated = true
    return {
      ...item,
      state: isStuckState ? settledState : item.state,
      ...(isStuckStatus ? { status: settledStatus } : {}),
    }
  })
  return mutated ? next : events
}

export function settleTodos(
  todos: TodoItem[],
  outcome: 'completed' | 'failed' | 'cancelled' = 'completed',
): TodoItem[] {
  if (todos.length === 0) return todos
  if (outcome === 'completed') {
    if (!todos.some((item) => item.status !== 'completed')) return todos
    return todos.map((item) =>
      item.status === 'completed' ? item : { ...item, status: 'completed' },
    )
  }
  if (!todos.some((item) => item.status === 'in_progress')) return todos
  return todos.map((item) =>
    item.status === 'in_progress' ? { ...item, status: 'pending' } : item,
  )
}

function derivePhaseAndStatus(
  prevPhase: string | null,
  prevStatus: TurnLifecycleStatus,
  event: ConversationActivityEvent,
  resolvedOutcome: 'completed' | 'failed' | 'cancelled' | null = terminalOutcomeFor(event),
): { activePhase: string | null; status: TurnLifecycleStatus } {
  if (resolvedOutcome) {
    return { activePhase: null, status: resolvedOutcome }
  }
  const nextPhase = event.codeStage ?? event.label ?? event.toolName ?? prevPhase
  return { activePhase: nextPhase ?? null, status: 'running' }
}

export function createActivityState(cursor = '0'): ActivityStoreState {
  return {
    events: [],
    cursor,
    connection: 'snapshot',
    resyncRequired: false,
    todos: [],
    activePhase: null,
    status: 'idle',
  }
}

export function applyActivityEvent(state: ActivityStoreState, event: ConversationActivityEvent): ActivityStoreState {
  // Identity is the real duplicate guard. The cursor comparison only blocks a
  // genuine regression: if a cursor cannot be ordered at all, dropping the event
  // would silently empty the feed, which is far worse than showing it twice —
  // and the event_id check above already prevents that.
  if (state.events.some((item) => item.eventId === event.eventId)) return state
  if (isCursorRegression(state.cursor, event.cursor)) return state

  const outcome = resolveTurnOutcome(event, state.events)
  const baseEvents = outcome ? settleTurnEvents(state.events, event.turnId, outcome) : state.events
  const rawTodos = event.todos && event.todos.length > 0 ? event.todos : state.todos
  const nextTodos = outcome ? settleTodos(rawTodos, outcome) : rawTodos
  const { activePhase, status } = derivePhaseAndStatus(state.activePhase, state.status, event, outcome)

  return {
    ...state,
    events: trimActivityEvents([...baseEvents, event], EVENT_LIMIT),
    cursor: event.cursor,
    resyncRequired: false,
    todos: nextTodos,
    activePhase,
    status,
  }
}

export function activityReducer(state: ActivityStoreState, action: ActivityAction): ActivityStoreState {
  switch (action.type) {
    case 'snapshot': {
      // The durable snapshot is the authority for the whole conversation, so it
      // replaces local state unconditionally. Gating it on cursor ordering once
      // made an unparseable cursor erase every activity row in the chat.
      const deduped = dedupe(action.events)
      let settled: ConversationActivityEvent[] = []
      let todos: TodoItem[] = []
      let activePhase: string | null = null
      let status: TurnLifecycleStatus = 'idle'
      for (const item of deduped) {
        if (item.todos && item.todos.length > 0) {
          todos = item.todos
        }
        const outcome = resolveTurnOutcome(item, settled)
        if (outcome) {
          settled = settleTurnEvents(settled, item.turnId, outcome)
          todos = settleTodos(todos, outcome)
        }
        settled.push(item)
        const derived = derivePhaseAndStatus(activePhase, status, item, outcome)
        activePhase = derived.activePhase
        status = derived.status
      }
      return {
        events: trimActivityEvents(settled, EVENT_LIMIT),
        cursor: action.cursor,
        connection: 'snapshot',
        resyncRequired: false,
        todos,
        activePhase,
        status,
      }
    }
    case 'prepend_older': {
      if (action.events.length === 0) return state
      const combined = dedupe([...action.events, ...state.events])
      let settled: ConversationActivityEvent[] = []
      let todos: TodoItem[] = state.todos
      for (const item of combined) {
        if (todos.length === 0 && item.todos && item.todos.length > 0) {
          todos = item.todos
        }
        const outcome = resolveTurnOutcome(item, settled)
        if (outcome) {
          settled = settleTurnEvents(settled, item.turnId, outcome)
          todos = settleTodos(todos, outcome)
        }
        settled.push(item)
      }
      return {
        ...state,
        events: settled,
        todos,
      }
    }
    case 'event':
      return applyActivityEvent(state, action.event)
    case 'cursor':
      return isCursorRegression(state.cursor, action.cursor) ? state : { ...state, cursor: action.cursor }
    case 'connection':
      return { ...state, connection: action.connection }
    case 'resync':
      return {
        ...state,
        events: [],
        cursor: '0',
        connection: 'resyncing',
        resyncRequired: true,
        activePhase: null,
        status: 'idle',
      }
  }
}

function isStreamingDeltaEvent(event: ConversationActivityEvent): boolean {
  return (
    event.type === 'assistant.delta' ||
    event.type === 'ASSISTANT_DELTA' ||
    event.type === 'agent.thought_delta' ||
    event.type === 'AGENT_THOUGHT_DELTA'
  )
}

function isEphemeralDeltaOrContext(
  event: ConversationActivityEvent,
  latestContextIdByTurn: Map<string, string>,
): boolean {
  if (isStreamingDeltaEvent(event)) {
    return true
  }
  if (event.type === 'context.updated' || event.type === 'CONTEXT_UPDATED') {
    const turnKey = event.turnId ?? '__default__'
    return latestContextIdByTurn.get(turnKey) !== event.eventId
  }
  return false
}

function mergeAdjacentDelta(
  older: ConversationActivityEvent,
  newer: ConversationActivityEvent,
): ConversationActivityEvent | null {
  if (older.turnId !== newer.turnId) return null
  const olderIsThought = older.type === 'agent.thought_delta' || older.type === 'AGENT_THOUGHT_DELTA'
  const newerIsThought = newer.type === 'agent.thought_delta' || newer.type === 'AGENT_THOUGHT_DELTA'
  if (olderIsThought && newerIsThought) {
    const olderText = String(older.payload?.delta ?? older.detail ?? older.delta ?? older.content ?? '')
    const newerText = String(newer.payload?.delta ?? newer.detail ?? newer.delta ?? newer.content ?? '')
    const mergedText = `${olderText}${newerText}`
    return {
      ...newer,
      detail: mergedText,
      ...(newer.payload ? { payload: { ...newer.payload, delta: mergedText } } : { payload: { delta: mergedText } }),
    }
  }
  const olderIsAssistant = older.type === 'assistant.delta' || older.type === 'ASSISTANT_DELTA'
  const newerIsAssistant = newer.type === 'assistant.delta' || newer.type === 'ASSISTANT_DELTA'
  if (olderIsAssistant && newerIsAssistant && older.messageId === newer.messageId) {
    const mergedContent = `${older.content ?? ''}${newer.content ?? ''}`
    return {
      ...newer,
      content: mergedContent,
      ...(older.summary && newer.summary ? { summary: `${older.summary}${newer.summary}` } : {}),
    }
  }
  return null
}

/**
 * Retains structural tool/artifact/lifecycle/prepend_older events unconditionally
 * while compacting and trimming old `assistant.delta` / `agent.thought_delta` /
 * stale `context.updated` events so delta floods never evict structural history
 * or delete consolidated paragraphs before tool calls.
 */
export function trimActivityEvents(
  events: ConversationActivityEvent[],
  limit: number = EVENT_LIMIT,
): ConversationActivityEvent[] {
  if (events.length <= limit) return events

  const working = [...events]
  const latestContextIdByTurn = new Map<string, string>()
  for (const event of working) {
    if (event.type === 'context.updated' || event.type === 'CONTEXT_UPDATED') {
      latestContextIdByTurn.set(event.turnId ?? '__default__', event.eventId)
    }
  }

  const dropNeeded = working.length - limit
  let dropped = 0
  const filtered: ConversationActivityEvent[] = []
  for (let i = 0; i < working.length; i += 1) {
    const event = working[i]
    if (dropped < dropNeeded && isEphemeralDeltaOrContext(event, latestContextIdByTurn)) {
      if (isStreamingDeltaEvent(event)) {
        const next = working[i + 1]
        const merged = next && isStreamingDeltaEvent(next) ? mergeAdjacentDelta(event, next) : null
        if (merged) {
          working[i + 1] = merged
          dropped += 1
          continue
        }
      } else {
        dropped += 1
        continue
      }
    }
    filtered.push(event)
  }

  return filtered
}

/** True only when both cursors are comparable and the next one moves backwards. */
export function isCursorRegression(current: string, next: string): boolean {
  if (current === next) return false
  const currentIndex = cursorIndex(current)
  const nextIndex = cursorIndex(next)
  if (currentIndex === null || nextIndex === null) return false
  return nextIndex < currentIndex
}

export function isCursorMonotonic(current: string, next: string): boolean {
  return !isCursorRegression(current, next)
}

function cursorIndex(value: string): number | null {
  if (/^(0|[1-9]\d*)$/.test(value)) {
    const parsed = Number(value)
    return Number.isSafeInteger(parsed) ? parsed : null
  }
  if (!value.startsWith('a.')) return null
  try {
    const encoded = value.slice(2).replaceAll('-', '+').replaceAll('_', '/')
    const raw = atob(encoded + '='.repeat((4 - encoded.length % 4) % 4))
    const body = raw.slice(0, -33)
    const payload = JSON.parse(body) as { p?: unknown }
    return typeof payload.p === 'number' && Number.isSafeInteger(payload.p) ? payload.p : null
  } catch {
    return null
  }
}

function dedupe(events: ConversationActivityEvent[]): ConversationActivityEvent[] {
  const seen = new Set<string>()
  return events.filter((event) => {
    if (seen.has(event.eventId)) return false
    seen.add(event.eventId)
    return true
  })
}
