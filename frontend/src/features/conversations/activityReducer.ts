import type { ConversationActivityEvent } from './activityTypes'

export type ActivityConnectionState = 'snapshot' | 'connecting' | 'live' | 'degraded' | 'resyncing'

export type ActivityStoreState = {
  events: ConversationActivityEvent[]
  cursor: string
  connection: ActivityConnectionState
  resyncRequired: boolean
}

export type ActivityAction =
  | { type: 'snapshot'; events: ConversationActivityEvent[]; cursor: string }
  | { type: 'event'; event: ConversationActivityEvent }
  | { type: 'cursor'; cursor: string }
  | { type: 'connection'; connection: ActivityConnectionState }
  | { type: 'resync' }

const EVENT_LIMIT = 500

export function createActivityState(cursor = '0'): ActivityStoreState {
  return { events: [], cursor, connection: 'snapshot', resyncRequired: false }
}

export function applyActivityEvent(state: ActivityStoreState, event: ConversationActivityEvent): ActivityStoreState {
  // Identity is the real duplicate guard. The cursor comparison only blocks a
  // genuine regression: if a cursor cannot be ordered at all, dropping the event
  // would silently empty the feed, which is far worse than showing it twice —
  // and the event_id check above already prevents that.
  if (state.events.some((item) => item.eventId === event.eventId)) return state
  if (isCursorRegression(state.cursor, event.cursor)) return state
  return {
    ...state,
    events: [...state.events, event].slice(-EVENT_LIMIT),
    cursor: event.cursor,
    resyncRequired: false,
  }
}

export function activityReducer(state: ActivityStoreState, action: ActivityAction): ActivityStoreState {
  switch (action.type) {
    case 'snapshot':
      // The durable snapshot is the authority for the whole conversation, so it
      // replaces local state unconditionally. Gating it on cursor ordering once
      // made an unparseable cursor erase every activity row in the chat.
      return {
        events: dedupe(action.events).slice(-EVENT_LIMIT),
        cursor: action.cursor,
        connection: 'snapshot',
        resyncRequired: false,
      }
    case 'event':
      return applyActivityEvent(state, action.event)
    case 'cursor':
      return isCursorRegression(state.cursor, action.cursor) ? state : { ...state, cursor: action.cursor }
    case 'connection':
      return { ...state, connection: action.connection }
    case 'resync':
      return { ...state, events: [], cursor: '0', connection: 'resyncing', resyncRequired: true }
  }
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
