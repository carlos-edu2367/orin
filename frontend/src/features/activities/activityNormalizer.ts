import type { ClientEvent } from '../../api/types'
import { delegationEventTypes, lifecycleEventTypes, type Activity, type ActivityCategory } from './activityTypes'

type ActivityGroupBuilder = {
  executionId: string
  category: ActivityCategory
  eventIds: string[]
  orderedEvents: ClientEvent[]
  minSequence: number
  firstIndex: number
}

/**
 * Projects the authorized ClientEvent stream into compact, auditable Activities.
 * Grouping is keyed by `invocation_id` for tool facts; execution lifecycle events
 * are grouped per execution. No other family is authorized on the public stream
 * today (BACKEND_DISCOVERY.md), so a Tool Call is never inferred from Execution
 * transitions alone, and delegation/resource stay reserved, unobserved categories.
 */
export function normalizeActivities(events: ClientEvent[]): Activity[] {
  const seenEventIds = new Set<string>()
  const groups = new Map<string, ActivityGroupBuilder>()

  events.forEach((sourceEvent, index) => {
    if (seenEventIds.has(sourceEvent.event_id)) return
    seenEventIds.add(sourceEvent.event_id)

    const category = categorize(sourceEvent)
    const key = `${sourceEvent.execution_id}::${category}::${groupDiscriminator(sourceEvent, category)}`
    const existing = groups.get(key)
    if (existing) {
      existing.eventIds.push(sourceEvent.event_id)
      existing.orderedEvents.push(sourceEvent)
      existing.minSequence = Math.min(existing.minSequence, sourceEvent.sequence)
      return
    }
    groups.set(key, {
      executionId: sourceEvent.execution_id,
      category,
      eventIds: [sourceEvent.event_id],
      orderedEvents: [sourceEvent],
      minSequence: sourceEvent.sequence,
      firstIndex: index,
    })
  })

  return [...groups.entries()]
    .sort(([, a], [, b]) => a.executionId.localeCompare(b.executionId) || a.minSequence - b.minSequence || a.firstIndex - b.firstIndex)
    .map(([id, group]) => ({
      id,
      executionId: group.executionId,
      category: group.category,
      state: deriveState(group.category, [...group.orderedEvents].sort((a, b) => a.sequence - b.sequence)),
      eventIds: [...group.eventIds],
    }))
}

export type ToolActivityDetail = {
  toolKind: string | null
  progressKind: string | null
  errorCode: string | null
  summary: string | null
}

/**
 * Reads only the field names ToolActivityView documents. Everything else in the
 * payload (arguments, output, tokens, refs) is intentionally never inspected, so
 * it cannot leak into the UI even if a future payload carries it by mistake.
 */
export function deriveToolActivityDetail(events: ClientEvent[]): ToolActivityDetail {
  let toolKind: string | null = null
  let progressKind: string | null = null
  let errorCode: string | null = null
  let summary: string | null = null

  for (const sourceEvent of events) {
    const payload = sourceEvent.payload
    toolKind = safeString(payload.tool_kind) ?? toolKind
    progressKind = safeString(payload.progress_kind) ?? progressKind
    errorCode = safeString(payload.error_code) ?? errorCode
    summary = safeString(payload.summary) ?? summary
  }

  return { toolKind, progressKind, errorCode, summary }
}

function categorize(event: ClientEvent): ActivityCategory {
  const invocationId = event.payload.invocation_id
  if (typeof invocationId === 'string' && invocationId.length > 0) return 'tool'
  if (lifecycleEventTypes.has(event.event_type)) return 'lifecycle'
  if (delegationEventTypes.has(event.event_type)) return 'delegation'
  return 'resource'
}

function groupDiscriminator(event: ClientEvent, category: ActivityCategory): string {
  if (category === 'tool') return String(event.payload.invocation_id)
  if (category === 'lifecycle') return 'lifecycle'
  if (category === 'delegation') return safeString(event.payload.delegation_id) ?? event.event_id
  return safeString(event.payload.resource_id) ?? event.event_id
}

function deriveState(category: ActivityCategory, orderedEvents: ClientEvent[]): string {
  const last = orderedEvents[orderedEvents.length - 1]
  if (category === 'lifecycle') return safeString(last.payload.to_state) ?? last.event_type
  return safeString(last.payload.state) ?? last.event_type
}

function safeString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}
