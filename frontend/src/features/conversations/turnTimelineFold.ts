import type { ActivityGroup, ConversationActivityEvent } from './activityTypes'
import {
  applyEventToGroup,
  codeModeRunGroup,
  codeModeRunKey,
  computeSettledEventIds,
  groupLabel,
  groupingKey,
  isCodeModeEvent,
  isRenderable,
  normalizeGeneratingArgumentsEvent,
} from './activitySummary'

export interface TimelineThought {
  id: string
  turnId: string
  content: string
  status: 'streaming' | 'completed'
  startedAt?: string
  endedAt?: string
  durationMs?: number
}

export type TimelineToolCall = ConversationActivityEvent
export type TimelineTextItem = { id: string; kind: 'text'; content: string }
export type TimelineActivityItem = {
  id: string
  kind: 'activity'
  group: ActivityGroup
  toolCall?: TimelineToolCall
}
export type TimelineThoughtItem = {
  id: string
  kind: 'thought'
  thought: TimelineThought
  group: ActivityGroup
}
export type TimelineItem = TimelineTextItem | TimelineActivityItem | TimelineThoughtItem
export type TurnTimelineItem = TimelineItem

const TERMINAL_TURN_EVENT_TYPES = new Set([
  'turn.completed',
  'TURN_COMPLETED',
  'turn.failed',
  'TURN_FAILED',
  'turn.cancelled',
  'TURN_CANCELLED',
  'execution.canceled',
  'EXECUTION_CANCELED',
  'code_mode.completed',
  'code_mode.completed_with_caveats',
  'code_mode.blocked',
])

/**
 * Backfills missing target metadata (`path`, `filePath`, `command`, `description`,
 * `toolName`, `agentName`, `parentAgentId`) on `tool.finished` / `tool.completed` /
 * `tool.failed` events from their corresponding `tool.started` event before the
 * started event is collapsed away.
 */
function extractUnchangedPathFromSummary(summary: string | undefined): string {
  if (!summary) return ''
  const match = summary.match(/^Arquivo inalterado:\s*(.+)$/i)
  return match ? match[1].trim() : ''
}

export function enrichEventsWithStartedMetadata(
  events: ConversationActivityEvent[],
): ConversationActivityEvent[] {
  const openByScope = new Map<string, ConversationActivityEvent[]>()
  const lastReadPathByScope = new Map<string, string>()
  let mutated = false

  const next = events.map((rawEvent) => {
    const event = normalizeGeneratingArgumentsEvent(rawEvent)
    if (event !== rawEvent) {
      mutated = true
    }
    const scope = `${event.agentId}:${event.turnId ?? ''}`
    const explicitReadPath =
      event.toolName === 'read_file'
        ? event.path || event.filePath || extractUnchangedPathFromSummary(event.summary)
        : ''
    if (explicitReadPath) {
      lastReadPathByScope.set(scope, explicitReadPath)
    }

    if (event.type === 'tool.started' || event.type === 'tool.progressed') {
      const list = openByScope.get(scope) ?? []
      list.push(event)
      openByScope.set(scope, list)
      return event
    }
    if (
      event.type === 'tool.finished' ||
      event.type === 'tool.completed' ||
      event.type === 'tool.failed'
    ) {
      const list = openByScope.get(scope)
      const match =
        list && list.length > 0
          ? (event.invocationId
              ? list.find((item) => item.invocationId === event.invocationId)
              : undefined) ??
            (event.toolName
              ? [...list].reverse().find((item) => item.toolName === event.toolName)
              : undefined) ??
            list[list.length - 1]
          : undefined

      const fallbackReadPath =
        event.toolName === 'read_file'
          ? explicitReadPath || lastReadPathByScope.get(scope) || ''
          : ''

      const nextPath = event.path || event.filePath || match?.path || match?.filePath || fallbackReadPath
      const nextFilePath = event.filePath || event.path || match?.filePath || match?.path || fallbackReadPath
      const nextCommand = event.command || match?.command
      const nextDescription = event.description || match?.description
      const nextToolName = event.toolName || match?.toolName
      const nextToolKind = event.toolKind || match?.toolKind
      const nextAgentName = event.agentName || match?.agentName
      const nextParentAgentId = event.parentAgentId || match?.parentAgentId

      if (nextToolName === 'read_file' && (nextPath || nextFilePath)) {
        lastReadPathByScope.set(scope, (nextPath || nextFilePath) as string)
      }

      const needsPatch =
        Boolean(nextPath && nextPath !== event.path) ||
        Boolean(nextFilePath && nextFilePath !== event.filePath) ||
        Boolean(nextCommand && nextCommand !== event.command) ||
        Boolean(nextDescription && nextDescription !== event.description) ||
        Boolean(nextToolName && nextToolName !== event.toolName) ||
        Boolean(nextToolKind && nextToolKind !== event.toolKind) ||
        Boolean(nextAgentName && nextAgentName !== event.agentName) ||
        Boolean(nextParentAgentId && nextParentAgentId !== event.parentAgentId)

      if (!needsPatch) return event
      mutated = true
      return {
        ...event,
        ...(nextPath ? { path: nextPath } : {}),
        ...(nextFilePath ? { filePath: nextFilePath } : {}),
        ...(nextCommand ? { command: nextCommand } : {}),
        ...(nextDescription ? { description: nextDescription } : {}),
        ...(nextToolName ? { toolName: nextToolName } : {}),
        ...(nextToolKind ? { toolKind: nextToolKind } : {}),
        ...(nextAgentName ? { agentName: nextAgentName } : {}),
        ...(nextParentAgentId ? { parentAgentId: nextParentAgentId } : {}),
      }
    }
    return event
  })

  return mutated ? next : events
}

/**
 * The turn a message belongs to, found through the message's own delta or
 * completed event — the public API has no direct message→turn field.
 */
export function resolveTurnId(events: ConversationActivityEvent[], messageId: string): string | null {
  const owner = events.find(
    (event) =>
      (event.kind === 'message' || event.type === 'agent.thought_delta' || event.type === 'AGENT_THOUGHT_DELTA') &&
      event.messageId === messageId &&
      event.turnId,
  )
  return owner?.turnId ?? null
}

function extractThoughtDelta(event: ConversationActivityEvent): string {
  const raw =
    event.payload?.delta ??
    event.detail ??
    event.delta ??
    event.content ??
    event.payload?.content ??
    ''
  return String(raw ?? '')
}

function computeDurationMs(startedAt?: string, endedAt?: string): number | undefined {
  if (!startedAt || !endedAt) return undefined
  const startMs = Date.parse(startedAt)
  const endMs = Date.parse(endedAt)
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return undefined
  return Math.max(0, endMs - startMs)
}

export type BuildTurnTimelineOptions = {
  turnId?: string
  messageId?: string
  completeContent?: string
  turnCompleted?: boolean
}

/**
 * One assistant turn folded into the order it actually happened: narration
 * text broken wherever a tool or agent event landed, instead of prose
 * followed by a replayed report. Reuses the same grouping rules as
 * `summarizeActivities` so one continuous tool sequence still collapses into
 * one card — just interrupted by whatever text fell between it.
 */
export function buildTurnTimeline(
  events: ConversationActivityEvent[],
  turnIdOrOptions?: string | BuildTurnTimelineOptions,
  messageIdOrCompleted?: string | boolean,
  completeContentOrCompleted: string | boolean = '',
  turnCompletedArg = false,
): TurnTimelineItem[] {
  let turnId = ''
  let messageId = ''
  let completeContent = ''
  let explicitTurnCompleted = turnCompletedArg

  if (typeof turnIdOrOptions === 'object' && turnIdOrOptions !== null) {
    turnId = turnIdOrOptions.turnId ?? events.find((e) => e.turnId)?.turnId ?? ''
    messageId = turnIdOrOptions.messageId ?? ''
    completeContent = turnIdOrOptions.completeContent ?? ''
    explicitTurnCompleted = Boolean(turnIdOrOptions.turnCompleted)
  } else {
    turnId = turnIdOrOptions ?? events.find((e) => e.turnId)?.turnId ?? ''
    if (typeof messageIdOrCompleted === 'boolean') {
      explicitTurnCompleted = messageIdOrCompleted
    } else {
      messageId = messageIdOrCompleted ?? ''
    }
    if (typeof completeContentOrCompleted === 'boolean') {
      explicitTurnCompleted = completeContentOrCompleted
    } else {
      completeContent = completeContentOrCompleted
    }
  }

  const hasAnyTurnId = events.some((event) => Boolean(event.turnId))
  const rawTurnEvents = turnId && hasAnyTurnId
    ? events.filter((event) => event.turnId === turnId)
    : events
  const turnEvents = enrichEventsWithStartedMetadata(rawTurnEvents)
  const settled = computeSettledEventIds(turnEvents)
  const firstCodeEvent = turnEvents.find((event) => isCodeModeEvent(event))
  const hasTerminalTurnEvent = turnEvents.some(
    (event) => !event.parentAgentId && TERMINAL_TURN_EVENT_TYPES.has(event.type),
  )
  const isTurnCompleted = explicitTurnCompleted || hasTerminalTurnEvent

  const items: TurnTimelineItem[] = []
  let textBuffer = ''
  let textId = ''
  let group: ActivityGroup | null = null
  let thoughtBuffer: {
    id: string
    turnId: string
    content: string
    startedAt?: string
    endedAt?: string
    agentId: string
    agentName?: string
  } | null = null
  const renderedCodeRuns = new Set<string>()

  const flushGroup = () => {
    if (!group) return
    const latestTool = [...group.events].reverse().find((ev) => ev.kind === 'tool') ?? group.events[0]
    items.push({
      id: `activity:${group.id}:${group.events[0].eventId}`,
      kind: 'activity',
      group,
      ...(group.kind === 'tool' && latestTool ? { toolCall: latestTool } : {}),
    })
    group = null
  }
  const flushText = () => {
    if (!textBuffer) return
    items.push({ id: textId, kind: 'text', content: textBuffer })
    textBuffer = ''
    textId = ''
  }
  const flushThought = (completed: boolean, nextEventOccurredAt?: string) => {
    if (!thoughtBuffer) return
    const status: 'streaming' | 'completed' = completed ? 'completed' : 'streaming'
    const rawEndedAt =
      thoughtBuffer.endedAt && thoughtBuffer.endedAt !== thoughtBuffer.startedAt
        ? thoughtBuffer.endedAt
        : nextEventOccurredAt ?? thoughtBuffer.endedAt ?? thoughtBuffer.startedAt
    const endedAt = completed ? rawEndedAt : thoughtBuffer.endedAt
    const durationMs = completed ? computeDurationMs(thoughtBuffer.startedAt, endedAt) : undefined
    const thought: TimelineThought = {
      id: thoughtBuffer.id,
      turnId: thoughtBuffer.turnId,
      content: thoughtBuffer.content,
      status,
      ...(thoughtBuffer.startedAt ? { startedAt: thoughtBuffer.startedAt } : {}),
      ...(endedAt ? { endedAt } : {}),
      ...(durationMs !== undefined ? { durationMs } : {}),
    }
    items.push({
      id: thought.id,
      kind: 'thought',
      thought,
      group: {
        id: thought.id,
        kind: 'lifecycle',
        state: status === 'completed' ? 'completed' : 'working',
        label: 'Pensando',
        count: 1,
        events: [],
        agentId: thoughtBuffer.agentId,
        agentName: thoughtBuffer.agentName,
        failed: false,
      },
    })
    thoughtBuffer = null
  }

  for (const event of turnEvents) {
    if (event.type === 'agent.thought_delta' || event.type === 'AGENT_THOUGHT_DELTA') {
      flushGroup()
      flushText()
      const deltaText = extractThoughtDelta(event)
      if (!thoughtBuffer) {
        thoughtBuffer = {
          id: `thought:${event.eventId}`,
          turnId: event.turnId ?? turnId,
          content: deltaText,
          startedAt: event.occurredAt,
          endedAt: event.occurredAt,
          agentId: event.agentId,
          agentName: event.agentName,
        }
      } else {
        thoughtBuffer.content += deltaText
        if (!thoughtBuffer.startedAt && event.occurredAt) {
          thoughtBuffer.startedAt = event.occurredAt
        }
        if (event.occurredAt) {
          thoughtBuffer.endedAt = event.occurredAt
        }
      }
      continue
    }
    if (event.kind === 'message') {
      if (messageId && event.messageId && event.messageId !== messageId) continue
      flushThought(true, event.occurredAt)
      flushGroup()
      if (!event.content) continue
      if (!textBuffer) textId = `text:${event.eventId}`
      textBuffer += event.content
      continue
    }
    if (firstCodeEvent) {
      const codeKey = codeModeRunKey(firstCodeEvent)
      if (!renderedCodeRuns.has(codeKey)) {
        renderedCodeRuns.add(codeKey)
        flushThought(true, event.occurredAt)
        flushText()
        flushGroup()
        items.push({ id: codeKey, kind: 'activity', group: codeModeRunGroup(turnEvents, firstCodeEvent) })
      }
    }
    if (isCodeModeEvent(event)) {
      const key = codeModeRunKey(event)
      if (renderedCodeRuns.has(key)) continue
      renderedCodeRuns.add(key)
      flushThought(true, event.occurredAt)
      flushText()
      flushGroup()
      items.push({ id: key, kind: 'activity', group: codeModeRunGroup(turnEvents, event) })
      continue
    }
    if (TERMINAL_TURN_EVENT_TYPES.has(event.type) && !event.parentAgentId) {
      flushThought(true, event.occurredAt)
    }
    if (!isRenderable(event, settled)) {
      if (event.type.startsWith('tool.')) {
        flushThought(true, event.occurredAt)
      }
      continue
    }
    flushThought(true, event.occurredAt)
    flushText()
    if (event.kind === 'artifact' && group && group.kind === 'tool' && group.agentId === event.agentId) {
      group.events.push(event)
      group.count += 1
      continue
    }
    const key = groupingKey(event)
    if (group && group.id === key && group.kind === event.kind) {
      applyEventToGroup(group, event)
      continue
    }
    flushGroup()
    group = {
      id: key,
      kind: event.kind,
      state: event.state,
      label: '',
      count: 1,
      events: [event],
      agentId: event.agentId,
      agentName: event.agentName,
      failed: event.state === 'failed',
    }
    group.label = groupLabel(group)
  }
  flushThought(isTurnCompleted)
  flushGroup()
  flushText()
  return reconcileCompleteText(items, completeContent, messageId)
}

/**
 * Activity history is intentionally bounded, while the message projection is
 * durable. When the history window rolls over during a long stream, prepend the
 * missing text to the first surviving text item instead of showing a truncated
 * answer. If no text delta survived, place the complete message before the
 * remaining activity timeline.
 */
function reconcileCompleteText(items: TurnTimelineItem[], completeContent: string, messageId: string): TurnTimelineItem[] {
  if (!completeContent) return items
  const visibleContent = items.filter((item): item is TimelineTextItem => item.kind === 'text').map((item) => item.content).join('')
  if (visibleContent === completeContent) return items
  if (visibleContent && !completeContent.endsWith(visibleContent)) return items
  const missingPrefix = completeContent.slice(0, completeContent.length - visibleContent.length)
  if (!missingPrefix) return items
  const firstTextIndex = items.findIndex((item) => item.kind === 'text')
  if (firstTextIndex < 0) return [{ id: `text:complete:${messageId}`, kind: 'text', content: completeContent }, ...items]
  const first = items[firstTextIndex]
  if (first.kind !== 'text') return items
  const next = [...items]
  next[firstTextIndex] = { ...first, content: missingPrefix + first.content }
  return next
}

/**
 * Every assistant message paired with its own interleaved timeline, plus the
 * set of turn ids those timelines account for. A turn that no message could
 * claim (unresolvable, or still running before its first delta lands) is
 * left out of `claimedTurnIds` on purpose — the caller renders it through the
 * existing flat activity stream instead of dropping it.
 */
export function buildMessageTimelines(
  messages: { message_id: string; role: string; content?: string; turn_id?: string | null; status?: string }[],
  events: ConversationActivityEvent[],
): { timelines: Map<string, TurnTimelineItem[]>; claimedTurnIds: Set<string> } {
  const timelines = new Map<string, TurnTimelineItem[]>()
  const claimedTurnIds = new Set<string>()
  for (const message of messages) {
    if (message.role !== 'assistant') continue
    const turnId = (typeof message.turn_id === 'string' && message.turn_id ? message.turn_id : null) ?? resolveTurnId(events, message.message_id)
    if (!turnId) continue
    const isMsgCompleted = message.status === 'completed' || message.status === 'failed' || message.status === 'cancelled'
    const timeline = buildTurnTimeline(events, turnId, message.message_id, message.content ?? '', isMsgCompleted)
    if (timeline.length === 0) continue
    timelines.set(message.message_id, timeline)
    claimedTurnIds.add(turnId)
  }
  return { timelines, claimedTurnIds }
}

