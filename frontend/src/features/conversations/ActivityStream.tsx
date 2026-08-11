import { AnimatePresence } from 'motion/react'
import { useMemo } from 'react'
import { ActivityCard } from './ActivityCard'
import { AgentBirth } from './AgentBirth'
import { AgentExchange } from './AgentExchange'
import { summarizeActivities } from './activitySummary'
import type { ActivityGroup, ConversationActivityEvent } from './activityTypes'

type ActivityStreamProps = {
  events: ConversationActivityEvent[]
  /** Cap the rendered rows so a very long run cannot stall the chat. */
  limit?: number
}

const DEFAULT_LIMIT = 120

/**
 * The agent's visible work, in order.
 *
 * Most rows collapse into one summarizing card. Two moments get their own
 * treatment because they carry the product's identity: a subagent being born, and
 * a message crossing between agents.
 */
export function ActivityStream({ events, limit = DEFAULT_LIMIT }: ActivityStreamProps) {
  const groups = useMemo(() => {
    const summarized = summarizeActivities(events)
    return summarized.length > limit ? summarized.slice(summarized.length - limit) : summarized
  }, [events, limit])

  if (groups.length === 0) return null

  return (
    <ol className="activity-stream" aria-label="Atividade do agente">
      <AnimatePresence initial={false}>
        {groups.map((group) => <li key={group.id + group.events[0].eventId}>{renderActivityGroup(group)}</li>)}
      </AnimatePresence>
    </ol>
  )
}

export function renderActivityGroup(group: ActivityGroup) {
  const first = group.events[0]
  if (group.kind === 'agent' && first.type === 'agent.created') return <AgentBirth event={first} />
  if (group.kind === 'agent' && (first.type === 'agent.message_sent' || first.type === 'agent.message_received')) {
    return <>{group.events.map((event) => <AgentExchange key={event.eventId} event={event} />)}</>
  }
  return <ActivityCard group={group} />
}
