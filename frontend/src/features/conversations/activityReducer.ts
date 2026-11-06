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
  | {
      type: 'reconcile_snapshot'
      events: ConversationActivityEvent[]
      cursor: string
      conversationState?: string
    }
  | { type: 'prepend_older'; events: ConversationActivityEvent[] }
  | { type: 'event'; event: ConversationActivityEvent }
  | { type: 'cursor'; cursor: string }
  | { type: 'connection'; connection: ActivityConnectionState }
  | { type: 'resync' }

export const EVENT_LIMIT = 1500

const TERMINAL_COMPLETED_TYPES = new Set([
  'turn.completed',
  'TURN_COMPLETED',
  'code_mode.completed',
  'code_mode.completed_with_caveats',
  'code_mode.unverified',
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
    const hasTransientPrefix =
      item.kind === 'tool' && typeof item.summary === 'string' && /^Executando:\s*/i.test(item.summary)
    if (!isStuckState && !isStuckStatus && !hasTransientPrefix) return item
    mutated = true
    const cleanedSummary =
      item.kind === 'tool' && typeof item.summary === 'string'
        ? item.summary.replace(/^Executando:\s*/i, '')
        : item.summary
    return {
      ...item,
      state: isStuckState ? settledState : item.state,
      ...(isStuckStatus || (isStuckState && (item.kind === 'tool' || item.kind === 'agent'))
        ? { status: settledStatus }
        : {}),
      ...(cleanedSummary !== undefined ? { summary: cleanedSummary } : {}),
    }
  })
  return mutated ? next : events
}

export function settleTodos(
  todos: TodoItem[],
  outcome: 'completed' | 'failed' | 'cancelled' = 'completed',
): TodoItem[] {
  if (todos.length === 0 || outcome === 'completed') return todos
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

export const initialActivityState = createActivityState

export function applyActivityEvent(state: ActivityStoreState, event: ConversationActivityEvent): ActivityStoreState {
  // Identity is the real duplicate guard. The cursor comparison only blocks a
  // genuine regression: if a cursor cannot be ordered at all, dropping the event
  // would silently empty the feed, which is far worse than showing it twice —
  // and the event_id check above already prevents that.
  if (
    state.events.some(
      (item) => item.eventId === event.eventId || item.mergedEventIds?.includes(event.eventId),
    )
  ) {
    return state
  }
  if (isCursorRegression(state.cursor, event.cursor)) return state
  if (isStreamingDeltaEvent(event)) {
    const deltaText = extractDeltaText(event)
    const isTurnSettled =
      state.status === 'completed' ||
      state.status === 'failed' ||
      state.status === 'cancelled' ||
      Boolean(
        event.turnId &&
          state.events.some(
            (item) => item.turnId === event.turnId && terminalOutcomeFor(item) !== null,
          ),
      )
    if (
      isTurnSettled &&
      deltaText.length > 0 &&
      state.events.some(
        (item) =>
          isStreamingDeltaEvent(item) &&
          isSameDeltaScope(item, event) &&
          extractDeltaText(item).includes(deltaText),
      )
    ) {
      return state
    }
  }

  const outcome = resolveTurnOutcome(event, state.events)
  const baseEvents = outcome ? settleTurnEvents(state.events, event.turnId, outcome) : state.events
  const rawTodos = event.todos && event.todos.length > 0 ? event.todos : state.todos
  const nextTodos = outcome ? settleTodos(rawTodos, outcome) : rawTodos
  const { activePhase, status } = derivePhaseAndStatus(state.activePhase, state.status, event, outcome)
  const nextConnection =
    state.connection === 'connecting' || state.connection === 'degraded'
      ? 'live'
      : state.connection

  return {
    ...state,
    events: trimActivityEvents([...baseEvents, event], EVENT_LIMIT),
    cursor: event.cursor ?? state.cursor,
    connection: nextConnection,
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
    case 'reconcile_snapshot': {
      const mergedEvents = reconcileSnapshotEvents(state.events, action.events)
      let settled: ConversationActivityEvent[] = []
      let todos: TodoItem[] = state.todos
      let activePhase: string | null = state.activePhase
      let status: TurnLifecycleStatus = state.status
      for (const item of mergedEvents) {
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
      const convState = action.conversationState
      const terminalConvOutcome: 'completed' | 'failed' | 'cancelled' | null =
        convState === 'completed' || convState === 'completed_with_caveats'
          ? 'completed'
          : convState === 'failed' || convState === 'blocked'
            ? 'failed'
            : convState === 'cancelled' || convState === 'canceled'
              ? 'cancelled'
              : null
      if (terminalConvOutcome) {
        settled = settleTurnEvents(settled, undefined, terminalConvOutcome)
        todos = settleTodos(todos, terminalConvOutcome)
        activePhase = null
        status = terminalConvOutcome
      }
      const nextCursor = isCursorRegression(state.cursor, action.cursor)
        ? state.cursor
        : action.cursor || state.cursor
      return {
        ...state,
        events: trimActivityEvents(settled, EVENT_LIMIT),
        cursor: nextCursor,
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
    case 'cursor': {
      const nextConnection =
        state.connection === 'connecting' || state.connection === 'degraded'
          ? 'live'
          : state.connection
      if (isCursorRegression(state.cursor, action.cursor)) {
        return nextConnection !== state.connection ? { ...state, connection: nextConnection } : state
      }
      return { ...state, cursor: action.cursor, connection: nextConnection }
    }
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

function isThoughtDeltaEvent(event: ConversationActivityEvent): boolean {
  return event.type === 'agent.thought_delta' || event.type === 'AGENT_THOUGHT_DELTA'
}

function isAssistantDeltaEvent(event: ConversationActivityEvent): boolean {
  return event.type === 'assistant.delta' || event.type === 'ASSISTANT_DELTA'
}

function extractDeltaText(event: ConversationActivityEvent): string {
  if (isThoughtDeltaEvent(event)) {
    return String(event.payload?.delta ?? event.payload?.content ?? event.detail ?? event.delta ?? event.content ?? '')
  }
  if (isAssistantDeltaEvent(event)) {
    return String(event.content ?? event.payload?.content ?? '')
  }
  return ''
}

function isNonBreakingDeltaSeparator(event: ConversationActivityEvent): boolean {
  return (
    event.type === 'context.updated' ||
    event.type === 'CONTEXT_UPDATED' ||
    event.type.startsWith('speak.')
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

function combineMergedIds(
  a: ConversationActivityEvent,
  b: ConversationActivityEvent,
): string[] {
  const ids = new Set<string>(a.mergedEventIds ?? [])
  if (b.eventId && b.eventId !== a.eventId) {
    ids.add(b.eventId)
  }
  for (const id of b.mergedEventIds ?? []) {
    if (id !== a.eventId) ids.add(id)
  }
  return Array.from(ids)
}

function mergeAdjacentDelta(
  older: ConversationActivityEvent,
  newer: ConversationActivityEvent,
): ConversationActivityEvent | null {
  if (older.turnId !== newer.turnId) return null
  if ((older.agentId ?? '') !== (newer.agentId ?? '')) return null
  const olderIsThought = isThoughtDeltaEvent(older)
  const newerIsThought = isThoughtDeltaEvent(newer)
  const mergedEventIds = combineMergedIds(older, newer)
  if (olderIsThought && newerIsThought) {
    const olderText = extractDeltaText(older)
    const newerText = extractDeltaText(newer)
    const mergedText = `${olderText}${newerText}`
    const endedAt = newer.endedAt ?? newer.occurredAt ?? older.endedAt ?? older.occurredAt
    return {
      ...newer,
      eventId: older.eventId,
      originEventId: older.originEventId ?? older.eventId,
      ...(mergedEventIds.length > 0 ? { mergedEventIds } : {}),
      ...(older.occurredAt ? { occurredAt: older.occurredAt } : {}),
      ...(endedAt ? { endedAt } : {}),
      detail: mergedText,
      ...(newer.payload
        ? { payload: { ...newer.payload, content: mergedText, delta: mergedText } }
        : { payload: { content: mergedText, delta: mergedText } }),
    }
  }
  const olderIsAssistant = isAssistantDeltaEvent(older)
  const newerIsAssistant = isAssistantDeltaEvent(newer)
  if (
    olderIsAssistant &&
    newerIsAssistant &&
    (!older.messageId || !newer.messageId || older.messageId === newer.messageId)
  ) {
    const mergedContent = `${older.content ?? ''}${newer.content ?? ''}`
    const endedAt = newer.endedAt ?? newer.occurredAt ?? older.endedAt ?? older.occurredAt
    return {
      ...newer,
      eventId: older.eventId,
      originEventId: older.originEventId ?? older.eventId,
      ...(mergedEventIds.length > 0 ? { mergedEventIds } : {}),
      ...(older.messageId || newer.messageId ? { messageId: older.messageId ?? newer.messageId } : {}),
      ...(older.occurredAt ? { occurredAt: older.occurredAt } : {}),
      ...(endedAt ? { endedAt } : {}),
      content: mergedContent,
      ...(older.summary && newer.summary ? { summary: `${older.summary}${newer.summary}` } : {}),
    }
  }
  return null
}

/**
 * Mirrors the backend `_compact_snapshot_activities` pass so contiguous SSE
 * `assistant.delta` and `agent.thought_delta` chunks collapse into the first
 * event of each run before reconciling with a durable snapshot.
 */
export function compactStreamingDeltas(
  events: ConversationActivityEvent[],
): ConversationActivityEvent[] {
  const compacted: ConversationActivityEvent[] = []
  let activeDeltaIndex = -1

  for (const event of events) {
    if (isStreamingDeltaEvent(event)) {
      if (activeDeltaIndex >= 0) {
        const merged = mergeAdjacentDelta(compacted[activeDeltaIndex]!, event)
        if (merged) {
          compacted[activeDeltaIndex] = merged
          continue
        }
      }
      activeDeltaIndex = compacted.length
      compacted.push(event)
      continue
    }
    if (!isNonBreakingDeltaSeparator(event)) {
      activeDeltaIndex = -1
    }
    compacted.push(event)
  }

  return compacted
}

function isSameDeltaScope(a: ConversationActivityEvent, b: ConversationActivityEvent): boolean {
  if (a.turnId !== b.turnId) return false
  if ((a.agentId ?? '') !== (b.agentId ?? '')) return false
  if (isThoughtDeltaEvent(a) && isThoughtDeltaEvent(b)) return true
  if (isAssistantDeltaEvent(a) && isAssistantDeltaEvent(b)) {
    return !a.messageId || !b.messageId || a.messageId === b.messageId
  }
  return false
}

function reconcileDeltaPair(
  existing: ConversationActivityEvent,
  snap: ConversationActivityEvent,
): ConversationActivityEvent {
  const existingText = extractDeltaText(existing)
  const snapText = extractDeltaText(snap)
  const mergedEventIds = combineMergedIds(snap, existing)
  const withMergedIds = mergedEventIds.length > 0 ? { ...snap, mergedEventIds } : snap
  if (existingText.length > snapText.length && existingText.startsWith(snapText)) {
    if (isThoughtDeltaEvent(withMergedIds)) {
      return {
        ...withMergedIds,
        detail: existingText,
        payload: { ...(withMergedIds.payload ?? {}), content: existingText, delta: existingText },
      }
    }
    if (isAssistantDeltaEvent(withMergedIds)) {
      return {
        ...withMergedIds,
        content: existingText,
      }
    }
  }
  return withMergedIds
}

function reconcileSnapshotEvents(
  existingEvents: ConversationActivityEvent[],
  incomingSnapshotEvents: ConversationActivityEvent[],
): ConversationActivityEvent[] {
  const compactedExisting = compactStreamingDeltas(existingEvents)
  const snapshotEvents = compactStreamingDeltas(incomingSnapshotEvents)
  const snapshotById = new Map<string, ConversationActivityEvent>()
  const snapshotIndexById = new Map<string, number>()
  for (let i = 0; i < snapshotEvents.length; i += 1) {
    const item = snapshotEvents[i]!
    snapshotById.set(item.eventId, item)
    snapshotIndexById.set(item.eventId, i)
  }

  const compactedExistingIds = new Set(compactedExisting.map((item) => item.eventId))
  const usedSnapshotIds = new Set<string>()
  const unabsorbedRemainderBySnapId = new Map<string, { snap: ConversationActivityEvent; remainder: string }>()
  const mergedEvents: ConversationActivityEvent[] = []
  let lastMatchedSnapshotIndex = -1

  for (let existingIdx = 0; existingIdx < compactedExisting.length; existingIdx += 1) {
    const existing = compactedExisting[existingIdx]!
    const directSnap = snapshotById.get(existing.eventId)
    if (directSnap) {
      usedSnapshotIds.add(directSnap.eventId)
      const snapIdx = snapshotIndexById.get(directSnap.eventId) ?? -1
      if (snapIdx > lastMatchedSnapshotIndex) {
        lastMatchedSnapshotIndex = snapIdx
      }
      if (isStreamingDeltaEvent(existing) && isStreamingDeltaEvent(directSnap)) {
        const existingText = extractDeltaText(existing)
        const snapText = extractDeltaText(directSnap)
        if (snapText.length > existingText.length && snapText.startsWith(existingText)) {
          unabsorbedRemainderBySnapId.set(directSnap.eventId, {
            snap: directSnap,
            remainder: snapText.slice(existingText.length),
          })
        }
        mergedEvents.push(reconcileDeltaPair(existing, directSnap))
      } else {
        if (!isNonBreakingDeltaSeparator(directSnap)) {
          unabsorbedRemainderBySnapId.clear()
        }
        mergedEvents.push(directSnap)
      }
      continue
    }

    if (isStreamingDeltaEvent(existing)) {
      const existingText = extractDeltaText(existing)
      let absorbedByEarlierSnap = false
      if (existingText.length > 0) {
        for (const [snapId, entry] of unabsorbedRemainderBySnapId.entries()) {
          if (!isSameDeltaScope(existing, entry.snap)) continue
          const idx = entry.remainder.indexOf(existingText)
          if (idx >= 0) {
            const nextRemainder = entry.remainder.slice(idx + existingText.length)
            if (nextRemainder.length > 0) {
              unabsorbedRemainderBySnapId.set(snapId, { snap: entry.snap, remainder: nextRemainder })
            } else {
              unabsorbedRemainderBySnapId.delete(snapId)
            }
            absorbedByEarlierSnap = true
            break
          }
        }
      }
      if (absorbedByEarlierSnap) {
        continue
      }

      // Upper bound in snapshotEvents: cannot cross the next structural event that also exists in compactedExisting
      let maxSnapshotIndex = snapshotEvents.length
      for (let lookahead = existingIdx + 1; lookahead < compactedExisting.length; lookahead += 1) {
        const futureExisting = compactedExisting[lookahead]!
        if (isStreamingDeltaEvent(futureExisting) || isNonBreakingDeltaSeparator(futureExisting)) continue
        const futureSnapIdx = snapshotIndexById.get(futureExisting.eventId)
        if (futureSnapIdx !== undefined) {
          maxSnapshotIndex = futureSnapIdx
          break
        }
      }

      const unmatchedSnap = snapshotEvents.find((snap, snapIdx) => {
        if (snapIdx <= lastMatchedSnapshotIndex || snapIdx >= maxSnapshotIndex) return false
        if (!isStreamingDeltaEvent(snap)) return false
        if (usedSnapshotIds.has(snap.eventId) || compactedExistingIds.has(snap.eventId)) return false
        if (!isSameDeltaScope(existing, snap)) return false
        const snapText = extractDeltaText(snap)
        return (
          existingText.length > 0 &&
          snapText.length > 0 &&
          (snapText.includes(existingText) || existingText.includes(snapText))
        )
      })

      if (unmatchedSnap) {
        usedSnapshotIds.add(unmatchedSnap.eventId)
        const snapIdx = snapshotIndexById.get(unmatchedSnap.eventId) ?? -1
        if (snapIdx > lastMatchedSnapshotIndex) {
          lastMatchedSnapshotIndex = snapIdx
        }
        const snapText = extractDeltaText(unmatchedSnap)
        const idx = snapText.indexOf(existingText)
        if (idx >= 0 && idx + existingText.length < snapText.length) {
          unabsorbedRemainderBySnapId.set(unmatchedSnap.eventId, {
            snap: unmatchedSnap,
            remainder: snapText.slice(idx + existingText.length),
          })
        }
        mergedEvents.push(reconcileDeltaPair(existing, unmatchedSnap))
        continue
      }
    }

    mergedEvents.push(existing)
  }

  const existingIds = new Set(mergedEvents.map((item) => item.eventId))
  for (const item of snapshotEvents) {
    if (!existingIds.has(item.eventId) && !usedSnapshotIds.has(item.eventId)) {
      mergedEvents.push(item)
      existingIds.add(item.eventId)
    }
  }

  return mergedEvents
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
export function isCursorRegression(current: string, next: string | undefined): boolean {
  if (!next || current === next) return false
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
  const rest = value.slice(2)
  if (/^(0|[1-9]\d*)$/.test(rest)) {
    const parsed = Number(rest)
    return Number.isSafeInteger(parsed) ? parsed : null
  }
  try {
    const encoded = rest.replaceAll('-', '+').replaceAll('_', '/')
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
