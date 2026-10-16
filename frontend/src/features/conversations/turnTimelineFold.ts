import type { ActivityGroup, ConversationActivityEvent } from './activityTypes'
import { applyEventToGroup, codeModeRunGroup, codeModeRunKey, computeSettledEventIds, groupLabel, groupingKey, isCodeModeEvent, isRenderable } from './activitySummary'

export type TimelineTextItem = { id: string; kind: 'text'; content: string }
export type TimelineActivityItem = { id: string; kind: 'activity'; group: ActivityGroup }
export type TimelineItem = TimelineTextItem | TimelineActivityItem

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

  const next = events.map((event) => {
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
  const owner = events.find((event) => event.kind === 'message' && event.messageId === messageId && event.turnId)
  return owner?.turnId ?? null
}

/**
 * One assistant turn folded into the order it actually happened: narration
 * text broken wherever a tool or agent event landed, instead of prose
 * followed by a replayed report. Reuses the same grouping rules as
 * `summarizeActivities` so one continuous tool sequence still collapses into
 * one card — just interrupted by whatever text fell between it.
 */
export function buildTurnTimeline(events: ConversationActivityEvent[], turnId: string, messageId: string, completeContent = ''): TimelineItem[] {
  const turnEvents = enrichEventsWithStartedMetadata(events.filter((event) => event.turnId === turnId))
  const settled = computeSettledEventIds(turnEvents)
  const firstCodeEvent = turnEvents.find((event) => isCodeModeEvent(event))

  const items: TimelineItem[] = []
  let textBuffer = ''
  let textId = ''
  let group: ActivityGroup | null = null
  const renderedCodeRuns = new Set<string>()

  const flushGroup = () => {
    if (!group) return
    items.push({ id: `activity:${group.id}:${group.events[0].eventId}`, kind: 'activity', group })
    group = null
  }
  const flushText = () => {
    if (!textBuffer) return
    items.push({ id: textId, kind: 'text', content: textBuffer })
    textBuffer = ''
    textId = ''
  }

  for (const event of turnEvents) {
    if (event.kind === 'message') {
      if (event.messageId !== messageId) continue
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
        flushText()
        flushGroup()
        items.push({ id: codeKey, kind: 'activity', group: codeModeRunGroup(turnEvents, firstCodeEvent) })
      }
    }
    if (isCodeModeEvent(event)) {
      const key = codeModeRunKey(event)
      if (renderedCodeRuns.has(key)) continue
      renderedCodeRuns.add(key)
      flushText()
      flushGroup()
      items.push({ id: key, kind: 'activity', group: codeModeRunGroup(turnEvents, event) })
      continue
    }
    if (!isRenderable(event, settled)) continue
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
    group = { id: key, kind: event.kind, state: event.state, label: '', count: 1, events: [event], agentId: event.agentId, agentName: event.agentName, failed: event.state === 'failed' }
    group.label = groupLabel(group)
  }
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
function reconcileCompleteText(items: TimelineItem[], completeContent: string, messageId: string): TimelineItem[] {
  if (!completeContent) return items
  const visibleContent = items.filter((item): item is TimelineTextItem => item.kind === 'text').map((item) => item.content).join('')
  if (visibleContent === completeContent) return items
  if (visibleContent && !completeContent.endsWith(visibleContent)) return items
  const missingPrefix = completeContent.slice(0, completeContent.length - visibleContent.length)
  if (!missingPrefix) return items
  const firstTextIndex = items.findIndex((item) => item.kind === 'text')
  if (firstTextIndex < 0) return [...items, { id: `text:complete:${messageId}`, kind: 'text', content: completeContent }]
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
  messages: { message_id: string; role: string; content?: string; turn_id?: string | null }[],
  events: ConversationActivityEvent[],
): { timelines: Map<string, TimelineItem[]>; claimedTurnIds: Set<string> } {
  const timelines = new Map<string, TimelineItem[]>()
  const claimedTurnIds = new Set<string>()
  for (const message of messages) {
    if (message.role !== 'assistant') continue
    const turnId = (typeof message.turn_id === 'string' && message.turn_id ? message.turn_id : null) ?? resolveTurnId(events, message.message_id)
    if (!turnId) continue
    const timeline = buildTurnTimeline(events, turnId, message.message_id, message.content ?? '')
    if (timeline.length === 0) continue
    timelines.set(message.message_id, timeline)
    claimedTurnIds.add(turnId)
  }
  return { timelines, claimedTurnIds }
}
