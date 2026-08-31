import type { ActivityGroup, ConversationActivityEvent } from './activityTypes'
import { codeModeRunGroup, codeModeRunKey, groupLabel, groupingKey, isCodeModeEvent, isRenderable } from './activitySummary'

export type TimelineTextItem = { id: string; kind: 'text'; content: string }
export type TimelineActivityItem = { id: string; kind: 'activity'; group: ActivityGroup }
export type TimelineItem = TimelineTextItem | TimelineActivityItem

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
  const turnEvents = events.filter((event) => event.turnId === turnId)
  const settled = new Set(
    turnEvents.filter((event) => event.type === 'tool.finished' && event.invocationId).map((event) => event.invocationId as string),
  )

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
    const key = groupingKey(event)
    if (group && group.id === key && group.kind === event.kind) {
      group.count += 1
      group.events.push(event)
      group.state = event.state
      group.failed = group.failed || event.state === 'failed'
      group.label = groupLabel(group)
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
  messages: { message_id: string; role: string; content?: string }[],
  events: ConversationActivityEvent[],
): { timelines: Map<string, TimelineItem[]>; claimedTurnIds: Set<string> } {
  const timelines = new Map<string, TimelineItem[]>()
  const claimedTurnIds = new Set<string>()
  for (const message of messages) {
    if (message.role !== 'assistant') continue
    const turnId = resolveTurnId(events, message.message_id)
    if (!turnId) continue
    const timeline = buildTurnTimeline(events, turnId, message.message_id, message.content ?? '')
    if (timeline.length === 0) continue
    timelines.set(message.message_id, timeline)
    claimedTurnIds.add(turnId)
  }
  return { timelines, claimedTurnIds }
}
