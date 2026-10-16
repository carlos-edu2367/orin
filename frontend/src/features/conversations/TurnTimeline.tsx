import { AnimatePresence } from 'motion/react'
import { useMemo, useState, type ReactNode } from 'react'
import { MarkdownMessage } from './MarkdownMessage'
import { renderActivityGroup } from './ActivityStream'
import { CodeToolCallBlock, formatToolSignature, resolveVisualStatus } from './CodeToolCallBlock'
import { PlanApprovalCard } from './PlanApprovalCard'
import type { ApiClient } from '../../api/client'
import type { CodePermissionMode } from '../../api/codeMode'
import { rewindTurn, type ConversationMode } from '../../api/conversations'
import { WorkspaceFileCard, type WorkspaceFilePreviewHandler } from './WorkspaceFileCard'
import { summarizeActivities } from './activitySummary'
import { enrichEventsWithStartedMetadata, type TimelineItem } from './turnTimelineFold'
import type { ActivityGroup, ConversationActivityEvent, ToolPermissionDecision } from './activityTypes'
import type { UserQuestionAnswer } from './UserQuestionCard'

export type TimelineThoughtData = {
  id: string
  turnId?: string
  content: string
  status: 'streaming' | 'completed'
  startedAt?: number | string
  endedAt?: number | string
  durationMs?: number
}

export type TimelineRenderItem =
  | TimelineItem
  | { id: string; kind: 'thought'; thought: TimelineThoughtData }

export type TurnTimelineProps = {
  items?: TimelineRenderItem[]
  events?: ConversationActivityEvent[]
  conversationId?: string
  client?: ApiClient
  mode?: ConversationMode
  permissionMode?: CodePermissionMode
  turnCompleted?: boolean
  dockActive?: boolean
  onPreview?: WorkspaceFilePreviewHandler
  onOpenFile?: (path: string) => void
  openQuestionTurnIds?: ReadonlySet<string>
  onAnswer?: (event: ConversationActivityEvent, answers: UserQuestionAnswer[]) => Promise<void>
  onMcpApprove?: (event: ConversationActivityEvent, secrets: Record<string, string>) => Promise<void>
  onMcpDecline?: (event: ConversationActivityEvent) => Promise<void>
  onPluginApprove?: (event: ConversationActivityEvent) => Promise<void>
  onPluginDecline?: (event: ConversationActivityEvent) => Promise<void>
  onToolPermission?: (event: ConversationActivityEvent, decision: ToolPermissionDecision) => Promise<void>
  onRewindTurn?: (turnId: string) => Promise<void>
  onApprovePlanAcceptEdits?: (event: ConversationActivityEvent) => void | Promise<void>
  onApprovePlanAskPermissions?: (event: ConversationActivityEvent) => void | Promise<void>
  onRequestPlanChanges?: (feedback: string, event: ConversationActivityEvent) => void | Promise<void>
  onApproveAcceptEdits?: (event: ConversationActivityEvent) => void | Promise<void>
  onApproveAskPermissions?: (event: ConversationActivityEvent) => void | Promise<void>
  onRequestChanges?: (feedback: string, event: ConversationActivityEvent) => void | Promise<void>
}

const CODE_TOOL_NAMES = new Set([
  'read_file',
  'edit_file',
  'write_file',
  'run_command',
  'bash',
  'search_files',
  'glob_files',
  'list_files',
  'list_directory',
  'notebook_edit',
  'todo_write',
  'task_create',
  'task_update',
  'task_get',
  'task_list',
  'enter_plan_mode',
  'exit_plan_mode',
  'run_subagent',
  'verify_project',
  'verify_frontend',
])

function resolveEventToolName(event: ConversationActivityEvent): string {
  const raw = event as ConversationActivityEvent & { tool?: string }
  if (event.toolName) return event.toolName
  if (typeof raw.tool === 'string' && raw.tool) return raw.tool
  if (event.type === 'code_mode.file_edited') return 'edit_file'
  if (event.type === 'code_mode.command_batch' || event.type === 'code_mode.command_Batch') return 'run_command'
  return ''
}

function isPlanApprovalEvent(
  event: ConversationActivityEvent,
  permissionMode?: CodePermissionMode,
): boolean {
  const toolName = resolveEventToolName(event)
  if (event.planApproval) return true
  if (toolName === 'exit_plan_mode') return true
  if (event.toolPermission?.toolName === 'exit_plan_mode' || event.toolPermission?.planApproval) return true
  if (
    event.type === 'code_mode.plan_ready' &&
    Boolean(event.planPath || event.planContent || permissionMode === 'plan_mode')
  ) {
    return true
  }
  return false
}

function isCodeToolEvent(
  event: ConversationActivityEvent,
  mode?: ConversationMode,
  permissionMode?: CodePermissionMode,
): boolean {
  if (
    event.toolPermission ||
    event.questions ||
    event.mcpApproval ||
    event.pluginApproval ||
    isPlanApprovalEvent(event, permissionMode)
  ) {
    return false
  }
  if (event.kind === 'artifact' || event.toolKind === 'browser') return false
  const toolName = resolveEventToolName(event)
  if (
    event.type === 'code_mode.file_edited' ||
    event.type === 'code_mode.command_batch' ||
    event.type === 'code_mode.command_Batch'
  ) {
    return true
  }
  if (
    (event.structuredPatch && event.structuredPatch.length > 0) ||
    Boolean(event.diff) ||
    Boolean(event.bashOutput) ||
    event.unchanged !== undefined ||
    event.addedLines !== undefined ||
    event.removedLines !== undefined
  ) {
    return true
  }
  if (mode !== 'chat' && CODE_TOOL_NAMES.has(toolName)) return true
  if (
    mode !== 'chat' &&
    (event.kind === 'tool' ||
      event.type === 'tool.started' ||
      event.type === 'tool.completed' ||
      event.type === 'tool.finished' ||
      event.type === 'tool.failed')
  ) {
    return true
  }
  return false
}

function normalizeCodeToolEvent(event: ConversationActivityEvent): ConversationActivityEvent {
  const toolName = resolveEventToolName(event)
  if (!toolName || event.toolName === toolName) return event
  return { ...event, toolName }
}

function turnModifiedTurnId(items: TimelineRenderItem[]): string | null {
  for (const item of items) {
    if (item.kind !== 'activity') continue
    for (const event of item.group.events) {
      const toolName = resolveEventToolName(event)
      const modified =
        toolName === 'write_file' ||
        toolName === 'edit_file' ||
        toolName === 'notebook_edit' ||
        event.kind === 'artifact' ||
        Boolean(event.structuredPatch && event.structuredPatch.length > 0) ||
        Boolean(event.diff && event.diff.structured_patch.length > 0)
      if (modified && event.turnId) return event.turnId
    }
  }
  return null
}

function formatPermissionReceiptLabel(event: ConversationActivityEvent): string {
  const perm = event.toolPermission as
    | (NonNullable<ConversationActivityEvent['toolPermission']> & { target?: string })
    | undefined
  const toolName = perm?.toolName || resolveEventToolName(event) || 'tool'
  const rawTarget =
    perm?.command ||
    perm?.path ||
    perm?.target ||
    event.command ||
    event.filePath ||
    event.path ||
    ''
  const compactTarget = rawTarget.length > 72 ? `${rawTarget.slice(0, 69)}…` : rawTarget

  if (toolName === 'run_command' || toolName === 'bash') {
    return compactTarget ? `Bash(${compactTarget})` : 'Bash'
  }
  if (toolName === 'edit_file' || toolName === 'notebook_edit') {
    return compactTarget ? `Update(${compactTarget})` : 'Update'
  }
  if (toolName === 'write_file') {
    return compactTarget ? `Write(${compactTarget})` : 'Write'
  }
  return compactTarget ? `${toolName}(${compactTarget})` : toolName
}

function ThoughtBlock({
  thought,
  turnCompleted = false,
}: {
  thought: TimelineThoughtData
  turnCompleted?: boolean
}) {
  const isStreaming = thought.status === 'streaming' && !turnCompleted
  const [expanded, setExpanded] = useState(false)

  const durationSeconds =
    typeof thought.durationMs === 'number' && Number.isFinite(thought.durationMs) && thought.durationMs >= 500
      ? Math.max(1, Math.round(thought.durationMs / 1000))
      : null

  if (isStreaming) {
    return (
      <div
        className="thought-block thought-block--streaming"
        data-testid="thought-block-streaming"
      >
        <div className="thought-block__header">
          <span className="agent-pulse__dot" aria-hidden="true" />
          <span className="agent-pulse__label--shimmer">Pensando...</span>
        </div>
        {thought.content && (
          <div className="thought-block__body">
            {thought.content}
          </div>
        )}
      </div>
    )
  }

  const summaryLabel = durationSeconds !== null ? `Pensou por ${durationSeconds}s` : 'Pensou'

  return (
    <div
      className="thought-block thought-block--completed"
      data-testid="thought-block-completed"
    >
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        data-testid="thought-block-toggle"
        className="thought-block__toggle"
      >
        <span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
        <span>{summaryLabel}</span>
      </button>
      {expanded && thought.content && (
        <div className="thought-block__body">
          {thought.content}
        </div>
      )}
    </div>
  )
}

function RepeatedToolFailureGroup({
  events,
  toolLabel,
  onOpenFile,
}: {
  events: ConversationActivityEvent[]
  toolLabel: string
  onOpenFile?: (path: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const first = events[0]
  const firstSig = formatToolSignature(first)
  const errorPreview = first.errorCode || first.summary || ''

  return (
    <div
      className="code-tool-failure-group my-1.5 rounded-lg border border-rose-500/30 bg-rose-950/15 px-3 py-2 text-[12px] text-[var(--text-primary)]"
      data-testid="code-tool-failure-group"
    >
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        data-testid="code-tool-failure-group-toggle"
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <div className="flex min-w-0 flex-1 items-center gap-2 font-mono">
          <span className="inline-block w-3.5 shrink-0 text-center text-rose-400" aria-hidden="true">
            ✕
          </span>
          <span className="font-semibold text-rose-300">
            {events.length} falhas de {toolLabel} recolhidas
          </span>
          {firstSig.arg && (
            <span className="truncate text-[11px] text-[var(--text-muted)]">
              ({firstSig.arg})
            </span>
          )}
          {errorPreview && (
            <span className="truncate text-[11px] text-rose-300/80">
              · {errorPreview}
            </span>
          )}
        </div>
        <span className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] text-rose-300 hover:bg-rose-500/15">
          {expanded ? 'Recolher ▴' : 'Expandir ▾'}
        </span>
      </button>
      {expanded && (
        <div className="mt-2 space-y-1 border-t border-rose-500/20 pt-2">
          {events.map((event) => (
            <CodeToolCallBlock
              key={event.eventId}
              event={event}
              onOpenFile={onOpenFile}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function SubagentTaskContainer({
  agentName,
  count,
  failed = false,
  children,
}: {
  agentName: string
  count: number
  failed?: boolean
  children: ReactNode
}) {
  const [expanded, setExpanded] = useState(true)

  return (
    <div
      className="subagent-task-group my-1.5 ml-3 rounded-r-lg border-l-2 border-[var(--accent-primary)]/40 bg-[var(--surface-raised)]/40 pl-3 pr-2 py-1.5"
      data-testid="subagent-task-group"
    >
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        data-testid="subagent-task-toggle"
        className="flex w-full items-center justify-between gap-2 text-left font-mono text-[12px]"
      >
        <div className="flex min-w-0 items-center gap-1.5">
          <span
            className={`inline-block w-3.5 shrink-0 text-center ${failed ? 'text-rose-400' : 'text-emerald-400'}`}
            aria-hidden="true"
          >
            {failed ? '✕' : '●'}
          </span>
          <span className="font-semibold text-[var(--text-primary)]">Task</span>
          <span className="text-[var(--text-muted)]">(</span>
          <span className="truncate text-[var(--text-secondary)]">{agentName}</span>
          <span className="text-[var(--text-muted)]">)</span>
          <span className="ml-1 rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-[var(--text-muted)]">
            {count} {count === 1 ? 'ação' : 'ações'}
          </span>
        </div>
        <span className="shrink-0 text-[11px] text-[var(--text-muted)]">
          {expanded ? 'Recolher ▴' : 'Expandir ▾'}
        </span>
      </button>
      {expanded && <div className="mt-1.5 space-y-1">{children}</div>}
    </div>
  )
}

/**
 * One assistant turn, told in order: narration and the action it triggered,
 * back to back, instead of the finished text followed by a replayed log.
 */
export function TurnTimeline({
  items,
  events,
  conversationId,
  client,
  mode,
  permissionMode,
  turnCompleted = false,
  dockActive = false,
  onPreview,
  onOpenFile,
  openQuestionTurnIds,
  onAnswer,
  onMcpApprove,
  onMcpDecline,
  onPluginApprove,
  onPluginDecline,
  onToolPermission,
  onRewindTurn,
  onApprovePlanAcceptEdits,
  onApprovePlanAskPermissions,
  onRequestPlanChanges,
  onApproveAcceptEdits,
  onApproveAskPermissions,
  onRequestChanges,
}: TurnTimelineProps) {
  const [confirmingRewind, setConfirmingRewind] = useState(false)
  const [rewinding, setRewinding] = useState(false)
  const [rewound, setRewound] = useState(false)
  const [rewindError, setRewindError] = useState<string | null>(null)

  const resolvedItems: TimelineRenderItem[] = useMemo(() => {
    if (items) return items
    if (!events) return []
    const enriched = enrichEventsWithStartedMetadata(events)
    return summarizeActivities(enriched).map((group) => ({
      id: `activity:${group.id}:${group.events[0]?.eventId ?? '0'}`,
      kind: 'activity' as const,
      group,
    }))
  }, [items, events])

  const primaryAgentId = useMemo(() => {
    for (const item of resolvedItems) {
      if (item.kind !== 'activity') continue
      for (const ev of item.group.events) {
        if (!ev.parentAgentId && ev.type !== 'agent.created' && ev.agentId) {
          return ev.agentId
        }
      }
    }
    return null
  }, [resolvedItems])

  const modifiedTurnId = turnModifiedTurnId(resolvedItems)
  const canShowRewind = Boolean(modifiedTurnId && (onRewindTurn || (client && conversationId)))

  const handleOpenFile = (path: string) => {
    onOpenFile?.(path)
    if (onPreview) {
      onPreview({
        conversationId: conversationId ?? '',
        path,
        name: path.split('/').at(-1),
      })
    }
  }

  async function handleRewind() {
    if (!modifiedTurnId || rewinding || rewound) return
    setRewinding(true)
    setRewindError(null)
    try {
      if (onRewindTurn) {
        await onRewindTurn(modifiedTurnId)
      } else if (client && conversationId) {
        await rewindTurn(client, conversationId, modifiedTurnId)
      }
      setRewound(true)
      setConfirmingRewind(false)
    } catch (error) {
      const message =
        error instanceof Error && error.message
          ? error.message
          : 'Não foi possível reverter as alterações deste turno.'
      setRewindError(message)
      setConfirmingRewind(false)
    } finally {
      setRewinding(false)
    }
  }

  function renderPlanApproval(event: ConversationActivityEvent, group?: ActivityGroup): ReactNode {
    const planPath =
      event.planApproval?.plan_path ??
      event.planPath ??
      event.toolPermission?.planApproval?.plan_path ??
      event.toolPermission?.path ??
      event.filePath ??
      event.path ??
      null
    const planContent =
      event.planApproval?.plan_content ??
      event.planContent ??
      event.toolPermission?.planApproval?.plan_content ??
      event.content ??
      null

    const approveAcceptHandler = onApprovePlanAcceptEdits ?? onApproveAcceptEdits
    const approveAskHandler = onApprovePlanAskPermissions ?? onApproveAskPermissions
    const requestChangesHandler = onRequestPlanChanges ?? onRequestChanges

    const isEventSettled =
      event.state === 'completed' ||
      event.state === 'failed' ||
      event.state === 'cancelled' ||
      event.status === 'completed' ||
      event.status === 'succeeded' ||
      event.status === 'failed' ||
      event.status === 'cancelled' ||
      group?.state === 'completed' ||
      group?.state === 'failed' ||
      group?.state === 'cancelled' ||
      (openQuestionTurnIds !== undefined &&
        typeof event.turnId === 'string' &&
        event.turnId.length > 0 &&
        !openQuestionTurnIds.has(event.turnId))

    return (
      <PlanApprovalCard
        key={event.eventId}
        planPath={planPath}
        planContent={planContent}
        disabled={isEventSettled}
        onApproveAcceptEdits={() => {
          void approveAcceptHandler?.(event)
        }}
        onApproveAskPermissions={() => {
          void approveAskHandler?.(event)
        }}
        onRequestChanges={
          requestChangesHandler
            ? (feedback) => {
                void requestChangesHandler(feedback, event)
              }
            : undefined
        }
      />
    )
  }

  function renderCodeToolSequence(toolEvents: ConversationActivityEvent[]): ReactNode[] {
    const nodes: ReactNode[] = []
    const openFileHandler = onOpenFile || onPreview ? handleOpenFile : undefined
    let index = 0

    while (index < toolEvents.length) {
      const current = normalizeCodeToolEvent(toolEvents[index])
      const currentStatus = resolveVisualStatus(current)
      const currentSig = formatToolSignature(current)

      if (currentStatus === 'failed') {
        const failedRun: ConversationActivityEvent[] = [current]
        let lookahead = index + 1
        while (lookahead < toolEvents.length) {
          const candidate = normalizeCodeToolEvent(toolEvents[lookahead])
          const candidateStatus = resolveVisualStatus(candidate)
          const candidateSig = formatToolSignature(candidate)
          if (candidateStatus === 'failed' && candidateSig.verb === currentSig.verb) {
            failedRun.push(candidate)
            lookahead += 1
          } else {
            break
          }
        }

        if (failedRun.length > 2) {
          nodes.push(
            <RepeatedToolFailureGroup
              key={`fail-group:${failedRun[0].eventId}`}
              events={failedRun}
              toolLabel={currentSig.verb}
              onOpenFile={openFileHandler}
            />,
          )
          index = lookahead
          continue
        }
      }

      nodes.push(
        <CodeToolCallBlock
          key={current.eventId}
          event={current}
          onOpenFile={openFileHandler}
        />,
      )
      index += 1
    }

    return nodes
  }

  function maybeWrapSubagentGroup(group: ActivityGroup, content: ReactNode): ReactNode {
    if (!content) return null
    const first = group.events[0]
    if (!first) return content
    const isSubagentGroup =
      group.kind === 'tool' &&
      !group.id.startsWith('code-mode:') &&
      ( group.events.some((ev) => Boolean(ev.parentAgentId)) ||
        Boolean(primaryAgentId && group.agentId && group.agentId !== primaryAgentId) )

    if (!isSubagentGroup) return content

    const subagentLabel =
      group.agentName ||
      first.agentName ||
      group.events.find((ev) => ev.agentName)?.agentName ||
      group.agentId
    const toolCount = group.events.filter((ev) => ev.kind === 'tool').length || group.events.length

    return (
      <SubagentTaskContainer
        agentName={subagentLabel}
        count={toolCount}
        failed={group.failed}
      >
        {content}
      </SubagentTaskContainer>
    )
  }

  function renderToolPermissionTimelineNode(
    event: ConversationActivityEvent,
    group: ActivityGroup,
  ): ReactNode {
    if (isPlanApprovalEvent(event, permissionMode)) {
      return renderPlanApproval(event, group)
    }

    const perm = event.toolPermission as
      | (NonNullable<ConversationActivityEvent['toolPermission']> & {
          state?: 'pending' | 'allowed' | 'denied'
        })
      | undefined
    const permState =
      perm?.state ??
      (event.state === 'completed'
        ? 'allowed'
        : event.state === 'failed' || event.state === 'cancelled'
          ? 'denied'
          : 'pending')
    const toolLabel = formatPermissionReceiptLabel(event)

    if (permState === 'allowed') {
      return (
        <div
          key={event.eventId}
          className="perm-receipt perm-receipt--allowed"
          data-testid="perm-receipt-allowed"
        >
          ✓ Permissão concedida · {toolLabel}
        </div>
      )
    }

    if (permState === 'denied') {
      return (
        <div
          key={event.eventId}
          className="perm-receipt perm-receipt--denied"
          data-testid="perm-receipt-denied"
        >
          ✕ Permissão negada · {toolLabel}
        </div>
      )
    }

    return (
      <div key={event.eventId}>
        <div
          className="perm-receipt perm-receipt--pending"
          data-testid="perm-receipt-pending"
        >
          ⏳ Aguardando permissão: {toolLabel}
        </div>
        {!dockActive &&
          renderActivityGroup(
            { ...group, count: 1, events: [event] },
            openQuestionTurnIds,
            onAnswer,
            conversationId,
            onPreview,
            onMcpApprove,
            onMcpDecline,
            onPluginApprove,
            onPluginDecline,
            onToolPermission,
          )}
      </div>
    )
  }

  function renderTimelineGroup(group: ActivityGroup): ReactNode {
    const first = group.events[0]
    if (!first) return null

    if (group.kind === 'artifact' && first.path && conversationId) {
      return (
        <WorkspaceFileCard
          reference={{ conversationId, path: first.path }}
          client={client}
          onPreview={onPreview}
        />
      )
    }

    if (first.toolPermission) {
      return renderToolPermissionTimelineNode(first, group)
    }

    // Retire the 5-stage purple CodeModeCard stepper from normal Code Mode turns,
    // while keeping plan approval and concrete file/command events.
    if (group.id.startsWith('code-mode:') || first.type.startsWith('code_mode.')) {
      const renderedNodes: ReactNode[] = []
      const seenPlanTurn = new Set<string>()
      let pendingCodeToolBatch: ConversationActivityEvent[] = []

      const flushCodeToolBatch = () => {
        if (pendingCodeToolBatch.length === 0) return
        renderedNodes.push(...renderCodeToolSequence(pendingCodeToolBatch))
        pendingCodeToolBatch = []
      }

      for (const event of group.events) {
        if (isPlanApprovalEvent(event, permissionMode)) {
          flushCodeToolBatch()
          const planKey = event.turnId ?? event.eventId
          if (!seenPlanTurn.has(planKey)) {
            seenPlanTurn.add(planKey)
            renderedNodes.push(renderPlanApproval(event, group))
          }
        } else if (event.toolPermission) {
          flushCodeToolBatch()
          renderedNodes.push(renderToolPermissionTimelineNode(event, group))
        } else if (isCodeToolEvent(event, mode, permissionMode)) {
          pendingCodeToolBatch.push(event)
        }
      }
      flushCodeToolBatch()
      return renderedNodes.length > 0 ? <>{renderedNodes}</> : null
    }

    const hasCodeOrPlanEvents = group.events.some(
      (event) => isPlanApprovalEvent(event, permissionMode) || isCodeToolEvent(event, mode, permissionMode),
    )

    if (hasCodeOrPlanEvents) {
      const nodes: ReactNode[] = []
      let pendingCodeToolBatch: ConversationActivityEvent[] = []

      const flushCodeToolBatch = () => {
        if (pendingCodeToolBatch.length === 0) return
        nodes.push(...renderCodeToolSequence(pendingCodeToolBatch))
        pendingCodeToolBatch = []
      }

      for (const event of group.events) {
        if (event.kind === 'artifact') continue
        if (isPlanApprovalEvent(event, permissionMode)) {
          flushCodeToolBatch()
          nodes.push(renderPlanApproval(event, group))
        } else if (event.toolPermission) {
          flushCodeToolBatch()
          nodes.push(renderToolPermissionTimelineNode(event, group))
        } else if (isCodeToolEvent(event, mode, permissionMode)) {
          pendingCodeToolBatch.push(event)
        } else {
          flushCodeToolBatch()
          nodes.push(
            <div key={event.eventId}>
              {renderActivityGroup(
                { ...group, count: 1, events: [event] },
                openQuestionTurnIds,
                onAnswer,
                conversationId,
                onPreview,
                onMcpApprove,
                onMcpDecline,
                onPluginApprove,
                onPluginDecline,
                onToolPermission,
              )}
            </div>,
          )
        }
      }
      flushCodeToolBatch()
      return nodes.length > 0 ? maybeWrapSubagentGroup(group, <>{nodes}</>) : null
    }

    return maybeWrapSubagentGroup(
      group,
      renderActivityGroup(
        group,
        openQuestionTurnIds,
        onAnswer,
        conversationId,
        onPreview,
        onMcpApprove,
        onMcpDecline,
        onPluginApprove,
        onPluginDecline,
        onToolPermission,
      ),
    )
  }

  return (
    <div className="turn-timeline">
      {canShowRewind && (
        <header
          className="turn-timeline__header turn-timeline__action-bar flex flex-wrap items-center justify-between gap-2 pb-1 text-[11px] text-[var(--text-muted)]"
          data-testid="turn-timeline-header"
        >
          <span className="font-mono text-[11px] opacity-75">Checkpoint disponível</span>
          <div className="flex flex-wrap items-center gap-1.5">
            {confirmingRewind && !rewound ? (
              <div className="flex items-center gap-1.5" data-testid="rewind-confirm-bar">
                <span className="font-mono text-[11px] text-amber-300">Confirmar reversão?</span>
                <button
                  type="button"
                  data-testid="rewind-confirm-button"
                  disabled={rewinding}
                  onClick={() => void handleRewind()}
                  className="inline-flex items-center gap-1 rounded-md border border-rose-500/40 bg-rose-500/20 px-2 py-0.5 font-mono text-[11px] font-medium text-rose-200 hover:bg-rose-500/30 disabled:opacity-40 transition-colors"
                >
                  {rewinding ? 'Revertendo…' : 'Sim, reverter'}
                </button>
                <button
                  type="button"
                  data-testid="rewind-cancel-button"
                  disabled={rewinding}
                  onClick={() => setConfirmingRewind(false)}
                  className="inline-flex items-center rounded-md border border-[var(--border-subtle)] bg-[var(--surface-raised)]/70 px-2 py-0.5 font-mono text-[11px] text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] disabled:opacity-40 transition-colors"
                >
                  Cancelar
                </button>
              </div>
            ) : (
              <button
                type="button"
                className="turn-timeline__rewind-button inline-flex items-center gap-1 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-raised)]/70 px-2 py-0.5 font-mono text-[11px] text-[var(--text-secondary)] hover:border-[var(--accent-primary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)] disabled:opacity-40 transition-colors"
                data-testid="rewind-turn-button"
                disabled={rewinding || rewound}
                onClick={() => {
                  setRewindError(null)
                  setConfirmingRewind(true)
                }}
              >
                <span aria-hidden="true">↺</span>
                <span>{rewound ? 'Alterações revertidas' : rewinding ? 'Revertendo…' : 'Reverter alterações'}</span>
              </button>
            )}
          </div>
          {rewindError && (
            <div
              role="alert"
              data-testid="rewind-turn-error"
              className="w-full rounded-md border border-rose-500/30 bg-rose-950/30 px-2.5 py-1 font-mono text-[11px] text-rose-300"
            >
              {rewindError}
            </div>
          )}
        </header>
      )}
      <AnimatePresence initial={false}>
        {resolvedItems.map((item) => {
          if (item.kind === 'thought') {
            return (
              <div key={item.id} className="turn-timeline__thought">
                <ThoughtBlock thought={item.thought} turnCompleted={turnCompleted} />
              </div>
            )
          }
          if (item.kind === 'text') {
            return (
              <div key={item.id} className="turn-timeline__text">
                <MarkdownMessage
                  content={item.content}
                  conversationId={conversationId}
                  client={client}
                  onPreview={onPreview}
                />
              </div>
            )
          }
          const rendered = renderTimelineGroup(item.group)
          if (rendered === null) return null
          return (
            <div key={item.id} className="turn-timeline__activity">
              {rendered}
            </div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}

