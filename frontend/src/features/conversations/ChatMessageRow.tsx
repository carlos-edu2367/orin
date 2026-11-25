import { motion } from 'motion/react'
import { memo, type ReactNode } from 'react'
import type { ApiClient } from '../../api/client'
import type { CodePermissionMode } from '../../api/codeMode'
import type { ConversationMessage, ConversationMode } from '../../api/conversations'
import type { ProviderModel, ProviderName } from '../../api/providers'
import { OrinMascot } from '../../components/OrinMascot'
import type { ConversationActivityEvent, ToolPermissionDecision } from './activityTypes'
import type { AgentMessagesState } from './agentMessages'
import type { AgentProgressMap } from './agentTree'
import type { CodeSessionProposalDecision } from './CodeSessionProposalCard'
import type { FriendDeliveryItem } from './FriendDeliveryCard'
import type { MergeResolveDetails } from './MergeConflictCard'
import { MarkdownMessage } from './MarkdownMessage'
import { MessageAttachments } from './MessageAttachments'
import { MessageCommandChip } from './MessageCommandChip'
import { TurnTimeline } from './TurnTimeline'
import type { TurnTimelineItem } from './turnTimelineFold'
import type { UserQuestionAnswer } from './UserQuestionCard'
import type { WorkspaceFilePreviewHandler } from './WorkspaceFileCard'

export function placeholderForMessage(message: ConversationMessage): string {
  if (message.role === 'user') return 'Mensagem enviada.'
  switch (message.status) {
    case 'queued':
      return 'Na fila…'
    case 'streaming':
      return 'Trabalhando…'
    case 'failed':
      return 'Não foi possível concluir esta resposta.'
    case 'cancelled':
      return 'Execução cancelada por você.'
    default:
      return 'Sem texto nesta resposta.'
  }
}

export type ChatMessageRowProps = {
  item: ConversationMessage
  timeline?: TurnTimelineItem[]
  conversationId: string
  client: ApiClient
  userInitial: string
  animateEntrance: boolean
  isEarliestPartialTurn: boolean
  loadingOlderActivities: boolean
  onLoadOlderActivities: () => void
  conversationMode: ConversationMode
  codePermissionMode: CodePermissionMode
  defaultProvider?: ProviderName
  defaultModelId?: string
  models?: Partial<Record<ProviderName, ProviderModel[]>>
  isTurnFinished: boolean
  /** A queued turn behind a live turn that still waits for the answer to its question. */
  queuedBehindQuestion?: boolean
  turnStartedAt: string | null
  turnFinishedAt: string | null
  turnWorkDurationLabel: string | null
  openQuestionTurnIds: ReadonlySet<string>
  isCopied: boolean
  retryContent?: string
  retryDisabled: boolean
  onCopy: (messageId: string, content: string) => void
  onRetry: (content: string) => void
  onPreview: WorkspaceFilePreviewHandler
  onOpenRewindTurn: (turnId: string) => void
  onAnswer: (event: ConversationActivityEvent, answers: UserQuestionAnswer[]) => Promise<void>
  onMcpApprove: (event: ConversationActivityEvent, secrets: Record<string, string>) => Promise<void>
  onMcpDecline: (event: ConversationActivityEvent) => Promise<void>
  onPluginApprove: (event: ConversationActivityEvent) => Promise<void>
  onPluginDecline: (event: ConversationActivityEvent) => Promise<void>
  onToolPermission: (event: ConversationActivityEvent, decision: ToolPermissionDecision) => Promise<void>
  onCodeSessionAccept: (event: ConversationActivityEvent, decision: CodeSessionProposalDecision) => Promise<void>
  onCodeSessionDecline: (event: ConversationActivityEvent) => Promise<void>
  onApprovePlanAcceptEdits?: (event: ConversationActivityEvent) => void | Promise<void>
  onApprovePlanAskPermissions?: (event: ConversationActivityEvent) => void | Promise<void>
  onRequestPlanChanges?: (feedback: string, event: ConversationActivityEvent) => void | Promise<void>
  agentMessages?: AgentMessagesState
  onAgentMessageSent?: (item: { controlId: string; agentId: string; message: string }) => void
  agentProgress?: AgentProgressMap
  onOpenWorktree?: (worktreePath: string, agentId: string) => void
  onAskAgentToResolveMerge?: (prompt: string, details: MergeResolveDetails) => void | Promise<void>
  friendDeliveries?: Record<string, FriendDeliveryItem>
  onOpenTargetConversation?: (conversationId: string) => void
}

export const ChatMessageRow = memo(function ChatMessageRow({
  item,
  timeline,
  conversationId,
  client,
  userInitial,
  animateEntrance,
  isEarliestPartialTurn,
  loadingOlderActivities,
  onLoadOlderActivities,
  conversationMode,
  codePermissionMode,
  defaultProvider,
  defaultModelId,
  models,
  isTurnFinished,
  queuedBehindQuestion = false,
  turnStartedAt,
  turnFinishedAt,
  turnWorkDurationLabel,
  openQuestionTurnIds,
  isCopied,
  retryContent,
  retryDisabled,
  onCopy,
  onRetry,
  onPreview,
  onOpenRewindTurn,
  onAnswer,
  onMcpApprove,
  onMcpDecline,
  onPluginApprove,
  onPluginDecline,
  onToolPermission,
  onCodeSessionAccept,
  onCodeSessionDecline,
  onApprovePlanAcceptEdits,
  onApprovePlanAskPermissions,
  onRequestPlanChanges,
  agentMessages,
  onAgentMessageSent,
  agentProgress,
  onOpenWorktree,
  onAskAgentToResolveMerge,
  friendDeliveries,
  onOpenTargetConversation,
}: ChatMessageRowProps) {
  let bodyContent: ReactNode
  if (timeline) {
    const timelineTextSegments = timeline.filter(
      (seg): seg is { id: string; kind: 'text'; content: string } => seg.kind === 'text',
    )
    const combinedTimelineText = timelineTextSegments.map((seg) => seg.content).join('')
    const trimmedContent = item.content?.trim() ?? ''
    const trimmedTimelineText = combinedTimelineText.trim()
    const missingFinalContent =
      item.role === 'assistant' &&
      (item.status === 'completed' || isTurnFinished) &&
      trimmedContent.length > 0 &&
      trimmedTimelineText !== trimmedContent &&
      !combinedTimelineText.includes(trimmedContent)
        ? combinedTimelineText.length > 0 && item.content.startsWith(combinedTimelineText)
          ? item.content.slice(combinedTimelineText.length).trim()
          : trimmedContent
        : ''

    bodyContent = (
      <>
        <TurnTimeline
          items={timeline}
          conversationId={conversationId}
          client={client}
          defaultProvider={defaultProvider}
          defaultModelId={defaultModelId}
          models={models}
          mode={conversationMode}
          permissionMode={codePermissionMode}
          turnCompleted={isTurnFinished}
          turnStartedAt={turnStartedAt}
          turnFinishedAt={turnFinishedAt}
          dockActive={true}
          onPreview={onPreview}
          openQuestionTurnIds={openQuestionTurnIds}
          onAnswer={onAnswer}
          onMcpApprove={onMcpApprove}
          onMcpDecline={onMcpDecline}
          onPluginApprove={onPluginApprove}
          onPluginDecline={onPluginDecline}
          onToolPermission={onToolPermission}
          onCodeSessionAccept={onCodeSessionAccept}
          onCodeSessionDecline={onCodeSessionDecline}
          onOpenRewindTurn={onOpenRewindTurn}
          onApprovePlanAcceptEdits={isTurnFinished ? undefined : onApprovePlanAcceptEdits}
          onApprovePlanAskPermissions={isTurnFinished ? undefined : onApprovePlanAskPermissions}
          onRequestPlanChanges={isTurnFinished ? undefined : onRequestPlanChanges}
          agentMessages={agentMessages}
          onAgentMessageSent={onAgentMessageSent}
          agentProgress={agentProgress}
          onOpenWorktree={onOpenWorktree}
          onAskAgentToResolveMerge={onAskAgentToResolveMerge}
          friendDeliveries={friendDeliveries}
          onOpenTargetConversation={onOpenTargetConversation}
        />
        {missingFinalContent.length > 0 && (
          <MarkdownMessage
            content={missingFinalContent}
            conversationId={conversationId}
            client={client}
            onPreview={onPreview}
          />
        )}
      </>
    )
  } else if (item.role === 'assistant' && item.content) {
    bodyContent = (
      <>
        <MarkdownMessage
          content={item.content}
          conversationId={conversationId}
          client={client}
          onPreview={onPreview}
        />
        {turnWorkDurationLabel && (
          <div
            className="turn-timeline__work-duration font-mono text-[11px] text-[var(--text-secondary)] pt-1"
            data-testid="turn-work-duration"
          >
            {turnWorkDurationLabel}
          </div>
        )}
      </>
    )
  } else if (item.command) {
    bodyContent = <MessageCommandChip command={item.command} />
  } else if (item.role === 'assistant' && !item.content && (item.status === 'streaming' || item.status === 'queued')) {
    bodyContent = (
      <div className="assistant-live-placeholder" data-testid="assistant-live-placeholder">
        <span className="agent-pulse__dot" aria-hidden="true" />
        <span className="agent-pulse__label--shimmer">
          {item.status === 'queued'
            ? (queuedBehindQuestion ? 'Na fila: o agente ainda espera a resposta da pergunta acima' : 'Na fila…')
            : 'Pensando…'}
        </span>
      </div>
    )
  } else {
    bodyContent = <p>{item.content || placeholderForMessage(item)}</p>
  }

  const inner = (
    <>
      {item.role === 'assistant' && (
        <div className="chat__assistant-mascot" data-testid="assistant-turn-mascot">
          <OrinMascot size={28} variant="flat" ariaHidden />
        </div>
      )}
      {item.role === 'user' && (
        <span className="chat__user-avatar" data-testid="user-message-avatar" aria-hidden="true">
          {userInitial}
        </span>
      )}
      {isEarliestPartialTurn && (
        <div className="flex justify-start pb-2">
          <button
            type="button"
            data-testid="load-older-activities"
            disabled={loadingOlderActivities}
            onClick={onLoadOlderActivities}
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-1 text-[12px] text-[var(--text-secondary)] hover:border-[var(--accent-primary)] hover:text-[var(--text-primary)] disabled:opacity-60 transition-colors"
          >
            {loadingOlderActivities ? 'Carregando atividades anteriores…' : '↑ Carregar atividades anteriores deste turno'}
          </button>
        </div>
      )}
      {bodyContent}
      {item.role === 'assistant' && item.content.trim().length > 0 && item.status === 'completed' && (
        <div className="bubble__actions" style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '6px' }}>
          <button
            type="button"
            className="ghost-button"
            aria-label="Copiar resposta"
            data-testid={`copy-message-${item.message_id}`}
            onClick={() => onCopy(item.message_id, item.content)}
            style={{ fontSize: '11px', padding: '2px 8px' }}
          >
            {isCopied ? 'Copiado' : 'Copiar resposta'}
          </button>
        </div>
      )}
      {item.role === 'user' && item.attachments.length > 0 && (
        <MessageAttachments conversationId={conversationId} items={item.attachments} client={client} onPreview={onPreview} />
      )}
      {item.retryable && (
        <div className="bubble__retry">
          <span role="status">Você pode tentar novamente sem perder a solicitação.</span>
          {retryContent !== undefined && (
            <button
              type="button"
              className="ghost-button"
              disabled={retryDisabled}
              onClick={() => onRetry(retryContent)}
            >
              Usar solicitação novamente
            </button>
          )}
        </div>
      )}
    </>
  )

  if (!animateEntrance) {
    return (
      <article
        className={`bubble bubble--${item.role}`}
        data-sticky-user-id={item.role === 'user' ? item.message_id : undefined}
      >
        {inner}
      </article>
    )
  }

  return (
    <motion.article
      className={`bubble bubble--${item.role}`}
      data-sticky-user-id={item.role === 'user' ? item.message_id : undefined}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.24, ease: [0.22, 0.61, 0.36, 1] }}
    >
      {inner}
    </motion.article>
  )
})
