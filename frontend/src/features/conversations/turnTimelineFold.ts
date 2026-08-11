import type { ActivityGroup, ConversationActivityEvent } from './activityTypes'
import { groupLabel, groupingKey, isRenderable } from './activitySummary'

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
 * `summarizeActivities` so a run of same-family tool calls still collapses
 * into one card — just interrupted by whatever text fell between them.
 */
export function buildTurnTimeline(events: ConversationActivityEvent[], turnId: string, messageId: string): TimelineItem[] {
  const turnEvents = events.filter((event) => event.turnId === turnId)
  const settled = new Set(
    turnEvents.filter((event) => event.type === 'tool.finished' && event.invocationId).map((event) => event.invocationId as string),
  )

  const items: TimelineItem[] = []
  let textBuffer = ''
  let textId = ''
  let group: ActivityGroup | null = null

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
  return items
}

/**
 * Every assistant message paired with its own interleaved timeline, plus the
 * set of turn ids those timelines account for. A turn that no message could
 * claim (unresolvable, or still running before its first delta lands) is
 * left out of `claimedTurnIds` on purpose — the caller renders it through the
 * existing flat activity stream instead of dropping it.
 */
export function buildMessageTimelines(
  messages: { message_id: string; role: string }[],
  events: ConversationActivityEvent[],
): { timelines: Map<string, TimelineItem[]>; claimedTurnIds: Set<string> } {
  const timelines = new Map<string, TimelineItem[]>()
  const claimedTurnIds = new Set<string>()
  for (const message of messages) {
    if (message.role !== 'assistant') continue
    const turnId = resolveTurnId(events, message.message_id)
    if (!turnId) continue
    const timeline = buildTurnTimeline(events, turnId, message.message_id)
    if (timeline.length === 0) continue
    timelines.set(message.message_id, timeline)
    claimedTurnIds.add(turnId)
  }
  return { timelines, claimedTurnIds }
}
