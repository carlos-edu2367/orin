import { AnimatePresence } from 'motion/react'
import { useMemo } from 'react'
import { ActivityCard } from './ActivityCard'
import { AgentBirth } from './AgentBirth'
import { AgentExchange } from './AgentExchange'
import { BrowserActivityCard } from './BrowserActivityCard'
import { CodeModeCard } from './CodeModeCard'
import { McpApprovalCard } from './McpApprovalCard'
import { PluginApprovalCard } from './PluginApprovalCard'
import { summarizeActivities } from './activitySummary'
import type { ActivityGroup, ConversationActivityEvent } from './activityTypes'
import { UserQuestionCard, type UserQuestionAnswer } from './UserQuestionCard'
import type { WorkspaceFilePreviewHandler } from './WorkspaceFileCard'

type ActivityStreamProps = {
  events: ConversationActivityEvent[]
  /** Cap the rendered rows so a very long run cannot stall the chat. */
  limit?: number
  openQuestionTurnIds?: ReadonlySet<string>
  onAnswer?: (event: ConversationActivityEvent, answers: UserQuestionAnswer[]) => Promise<void>
  onMcpApprove?: (event: ConversationActivityEvent, secrets: Record<string, string>) => Promise<void>
  onMcpDecline?: (event: ConversationActivityEvent) => Promise<void>
  onPluginApprove?: (event: ConversationActivityEvent) => Promise<void>
  onPluginDecline?: (event: ConversationActivityEvent) => Promise<void>
  conversationId?: string
  onPreview?: WorkspaceFilePreviewHandler
}

const DEFAULT_LIMIT = 120

/**
 * The agent's visible work, in order.
 *
 * Most rows collapse into one summarizing card. Two moments get their own
 * treatment because they carry the product's identity: a subagent being born, and
 * a message crossing between agents.
 */
export function ActivityStream({ events, limit = DEFAULT_LIMIT, openQuestionTurnIds, onAnswer, onMcpApprove, onMcpDecline, onPluginApprove, onPluginDecline, conversationId, onPreview }: ActivityStreamProps) {
  const groups = useMemo(() => {
    const summarized = summarizeActivities(events)
    return summarized.length > limit ? summarized.slice(summarized.length - limit) : summarized
  }, [events, limit])

  if (groups.length === 0) return null

  return (
    <ol className="activity-stream" aria-label="Atividade do agente">
      <AnimatePresence initial={false}>
        {groups.map((group) => <li key={group.id + group.events[0].eventId}>{renderActivityGroup(group, openQuestionTurnIds, onAnswer, conversationId, onPreview, onMcpApprove, onMcpDecline, onPluginApprove, onPluginDecline)}</li>)}
      </AnimatePresence>
    </ol>
  )
}

export function renderActivityGroup(
  group: ActivityGroup,
  openQuestionTurnIds?: ReadonlySet<string>,
  onAnswer?: (event: ConversationActivityEvent, answers: UserQuestionAnswer[]) => Promise<void>,
  conversationId?: string,
  onPreview?: WorkspaceFilePreviewHandler,
  onMcpApprove?: (event: ConversationActivityEvent, secrets: Record<string, string>) => Promise<void>,
  onMcpDecline?: (event: ConversationActivityEvent) => Promise<void>,
  onPluginApprove?: (event: ConversationActivityEvent) => Promise<void>,
  onPluginDecline?: (event: ConversationActivityEvent) => Promise<void>,
) {
  const first = group.events[0]
  if (first.type.startsWith('code_mode.')) return <CodeModeCard group={group} />
  if ((first.toolName === 'ask_user' || first.codeApproval) && first.questions && first.turnId && onAnswer) {
    return <UserQuestionCard questions={first.questions} active={openQuestionTurnIds?.has(first.turnId) ?? false} onSubmit={(answers) => onAnswer(first, answers)} />
  }
  if (first.toolName === 'install_plugin' && first.pluginApproval && first.turnId && onPluginApprove && onPluginDecline) {
    return <PluginApprovalCard plugin={first.pluginApproval} active={openQuestionTurnIds?.has(first.turnId) ?? false} onApprove={() => onPluginApprove(first)} onDecline={() => onPluginDecline(first)} />
  }
  if (first.toolName === 'configure_mcp' && first.mcpApproval && first.turnId && onMcpApprove && onMcpDecline) {
    return (
      <McpApprovalCard
        server={first.mcpApproval}
        active={openQuestionTurnIds?.has(first.turnId) ?? false}
        onApprove={(secrets) => onMcpApprove(first, secrets)}
        onDecline={() => onMcpDecline(first)}
      />
    )
  }
  if (group.kind === 'agent' && first.type === 'agent.created') return <AgentBirth event={first} />
  if (group.kind === 'agent' && (first.type === 'agent.message_sent' || first.type === 'agent.message_received')) {
    return <>{group.events.map((event) => <AgentExchange key={event.eventId} event={event} />)}</>
  }
  if (first.toolKind === 'browser') return <BrowserActivityCard group={group} conversationId={conversationId} onPreview={onPreview} />
  return <ActivityCard group={group} />
}
