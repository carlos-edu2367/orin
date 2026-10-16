import { AnimatePresence } from 'motion/react'
import { useMemo } from 'react'
import { ActivityCard } from './ActivityCard'
import { AgentBirth } from './AgentBirth'
import { AgentExchange } from './AgentExchange'
import { BrowserActivityCard } from './BrowserActivityCard'
import { CodeModeCard } from './CodeModeCard'
import { CodeSessionProposalCard, type CodeSessionProposalDecision } from './CodeSessionProposalCard'
import { McpApprovalCard } from './McpApprovalCard'
import { MemoryLearnedCard } from './MemoryLearnedCard'
import { PluginApprovalCard } from './PluginApprovalCard'
import { ToolPermissionCard } from './ToolPermissionCard'
import { summarizeActivities } from './activitySummary'
import type { ActivityGroup, ConversationActivityEvent, ToolPermissionDecision } from './activityTypes'
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
  onToolPermission?: (event: ConversationActivityEvent, decision: ToolPermissionDecision) => Promise<void>
  onCodeSessionAccept?: (event: ConversationActivityEvent, decision: CodeSessionProposalDecision) => Promise<void>
  onCodeSessionDecline?: (event: ConversationActivityEvent) => Promise<void>
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
export function ActivityStream({ events, limit = DEFAULT_LIMIT, openQuestionTurnIds, onAnswer, onMcpApprove, onMcpDecline, onPluginApprove, onPluginDecline, onToolPermission, onCodeSessionAccept, onCodeSessionDecline, conversationId, onPreview }: ActivityStreamProps) {
  const groups = useMemo(() => {
    const summarized = summarizeActivities(events)
    return summarized.length > limit ? summarized.slice(summarized.length - limit) : summarized
  }, [events, limit])

  if (groups.length === 0) return null

  return (
    <ol className="activity-stream" aria-label="Atividade do agente">
      <AnimatePresence initial={false}>
        {groups.map((group) => <li key={group.id + group.events[0].eventId}>{renderActivityGroup(group, openQuestionTurnIds, onAnswer, conversationId, onPreview, onMcpApprove, onMcpDecline, onPluginApprove, onPluginDecline, onToolPermission, onCodeSessionAccept, onCodeSessionDecline)}</li>)}
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
  onToolPermission?: (event: ConversationActivityEvent, decision: ToolPermissionDecision) => Promise<void>,
  onCodeSessionAccept?: (event: ConversationActivityEvent, decision: CodeSessionProposalDecision) => Promise<void>,
  onCodeSessionDecline?: (event: ConversationActivityEvent) => Promise<void>,
) {
  const first = group.events[0]
  if (first.toolPermission) {
    const latest = group.events[group.events.length - 1] ?? first
    const isResolved =
      group.state === 'completed' ||
      group.state === 'failed' ||
      group.state === 'cancelled' ||
      latest.state === 'completed' ||
      latest.state === 'failed' ||
      latest.state === 'cancelled' ||
      latest.status === 'completed' ||
      latest.status === 'succeeded' ||
      latest.status === 'failed' ||
      latest.status === 'cancelled'
    return (
      <ToolPermissionCard
        request={first.toolPermission}
        active={!isResolved}
        onDecision={onToolPermission ? (decision) => onToolPermission(first, decision) : undefined}
      />
    )
  }
  if ((first.toolName === 'ask_user' || first.codeApproval || first.questions) && first.questions && first.turnId && onAnswer) {
    return <UserQuestionCard questions={first.questions} active={openQuestionTurnIds?.has(first.turnId) ?? false} onSubmit={(answers) => onAnswer(first, answers)} />
  }
  if (first.pluginApproval && first.turnId && onPluginApprove && onPluginDecline) {
    return <PluginApprovalCard plugin={first.pluginApproval} active={openQuestionTurnIds?.has(first.turnId) ?? false} onApprove={() => onPluginApprove(first)} onDecline={() => onPluginDecline(first)} />
  }
  if (first.mcpApproval && first.turnId && onMcpApprove && onMcpDecline) {
    return (
      <McpApprovalCard
        server={first.mcpApproval}
        active={openQuestionTurnIds?.has(first.turnId) ?? false}
        onApprove={(secrets) => onMcpApprove(first, secrets)}
        onDecline={() => onMcpDecline(first)}
      />
    )
  }
  if (first.codeSessionProposal && onCodeSessionAccept && onCodeSessionDecline) {
    return (
      <CodeSessionProposalCard
        proposal={first.codeSessionProposal}
        active={first.turnId ? (openQuestionTurnIds?.has(first.turnId) ?? false) : true}
        onAccept={(decision) => onCodeSessionAccept(first, decision)}
        onDecline={() => onCodeSessionDecline(first)}
      />
    )
  }
  if (first.type.startsWith('code_mode.')) return <CodeModeCard group={group} />
  if (first.type === 'memory.learned') {
    return <>{group.events.map((event) => <MemoryLearnedCard key={event.eventId} event={event} />)}</>
  }
  if (group.kind === 'agent' && first.type === 'agent.created') return <AgentBirth event={first} />
  if (group.kind === 'agent' && (first.type === 'agent.message_sent' || first.type === 'agent.message_received')) {
    return <>{group.events.map((event) => <AgentExchange key={event.eventId} event={event} />)}</>
  }
  if (first.toolKind === 'browser') return <BrowserActivityCard group={group} conversationId={conversationId} onPreview={onPreview} />
  return <ActivityCard group={group} conversationId={conversationId} onPreview={onPreview} />
}
