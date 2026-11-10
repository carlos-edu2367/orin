import type { JSX, Ref } from 'react'
import { McpApprovalCard } from '../McpApprovalCard'
import {
  PlanApprovalCard,
  extractPlanApprovalDetails,
} from '../PlanApprovalCard'
import { PluginApprovalCard } from '../PluginApprovalCard'
import { sanitizeText } from '../teamActivityModel'
import { ToolPermissionCard } from '../ToolPermissionCard'
import {
  UserQuestionCard,
  type UserQuestionAnswer,
} from '../UserQuestionCard'
import type {
  ConversationActivityEvent,
  ToolPermissionDecision,
} from '../activityTypes'
import {
  MAIN_AGENT_ID,
  type SceneAgent,
  type ScenePending,
} from './sceneTypes'

export interface SceneActionHandlers {
  onToolPermission?: (
    event: ConversationActivityEvent,
    decision: ToolPermissionDecision,
  ) => Promise<void> | void
  onAnswer?: (
    event: ConversationActivityEvent,
    answers: UserQuestionAnswer[],
  ) => Promise<void> | void
  onMcpApprove?: (
    event: ConversationActivityEvent,
    secrets: Record<string, string>,
  ) => Promise<void> | void
  onMcpDecline?: (event: ConversationActivityEvent) => Promise<void> | void
  onPluginApprove?: (event: ConversationActivityEvent) => Promise<void> | void
  onPluginDecline?: (event: ConversationActivityEvent) => Promise<void> | void
  onApprovePlanAcceptEdits?: (
    event: ConversationActivityEvent,
  ) => Promise<void> | void
  onApprovePlanAskPermissions?: (
    event: ConversationActivityEvent,
  ) => Promise<void> | void
  onRequestPlanChanges?: (
    feedback: string,
    event: ConversationActivityEvent,
  ) => Promise<void> | void
  onApproveAcceptEdits?: (
    event: ConversationActivityEvent,
  ) => Promise<void> | void
  onApproveAskPermissions?: (
    event: ConversationActivityEvent,
  ) => Promise<void> | void
  onRequestChanges?: (
    feedback: string,
    event: ConversationActivityEvent,
  ) => Promise<void> | void
}

export interface ScenePendingCardsProps {
  pending: ScenePending[]
  agents: SceneAgent[]
  events?: ConversationActivityEvent[]
  actions?: SceneActionHandlers
  openQuestionTurnIds?: ReadonlySet<string>
  onSelectAgent: (agentId: string | null) => void
  sectionRef?: Ref<HTMLElement>
  variant?: 'hud' | 'modal'
  testIdPrefix?: string
  hideSelectButton?: boolean
}

function isEventSettled(event: ConversationActivityEvent): boolean {
  return (
    event.state === 'completed' ||
    event.state === 'failed' ||
    event.state === 'cancelled' ||
    event.status === 'completed' ||
    event.status === 'succeeded' ||
    event.status === 'failed' ||
    event.status === 'cancelled'
  )
}

function isTurnOpen(
  event: ConversationActivityEvent,
  openQuestionTurnIds?: ReadonlySet<string>,
): boolean {
  if (!openQuestionTurnIds) return true
  if (typeof event.turnId === 'string' && event.turnId.length > 0) {
    return openQuestionTurnIds.has(event.turnId)
  }
  return true
}

function findPendingEvent(
  item: ScenePending,
  events?: ConversationActivityEvent[],
): ConversationActivityEvent | undefined {
  if (!Array.isArray(events) || events.length === 0) return undefined

  const byEventId = events.find(
    (ev) => ev && (ev.eventId === item.eventId || sanitizeText(ev.eventId) === item.eventId),
  )
  if (byEventId) return byEventId

  if (item.requestId) {
    const byRequestId = events.find(
      (ev) =>
        ev?.toolPermission &&
        (ev.toolPermission.requestId === item.requestId ||
          sanitizeText(ev.toolPermission.requestId) === item.requestId),
    )
    if (byRequestId) return byRequestId
  }

  return undefined
}

function findLatestMatchingEvent(
  item: ScenePending,
  origin: ConversationActivityEvent,
  events?: ConversationActivityEvent[],
): ConversationActivityEvent {
  if (!Array.isArray(events) || events.length === 0) return origin
  const reqId = item.requestId ?? origin.toolPermission?.requestId
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const candidate = events[i]
    if (!candidate) continue
    if (
      candidate.eventId === origin.eventId ||
      (reqId && candidate.toolPermission?.requestId === reqId)
    ) {
      return candidate
    }
  }
  return origin
}

function renderPendingCardBody(
  item: ScenePending,
  event: ConversationActivityEvent | undefined,
  events: ConversationActivityEvent[] | undefined,
  actions: SceneActionHandlers | undefined,
  openQuestionTurnIds: ReadonlySet<string> | undefined,
): JSX.Element {
  const fallback = (
    <p className="scene-hud__pending-fallback" data-testid={`scene-pending-fallback-${sanitizeText(item.id)}`}>
      {sanitizeText(item.summary)}
    </p>
  )

  if (!event || !actions) {
    return fallback
  }

  switch (item.kind) {
    case 'permission': {
      if (!event.toolPermission || !actions.onToolPermission) {
        return fallback
      }
      const latest = findLatestMatchingEvent(item, event, events)
      const isResolved =
        isEventSettled(event) ||
        isEventSettled(latest) ||
        event.toolPermission.state === 'allowed' ||
        event.toolPermission.state === 'denied' ||
        latest.toolPermission?.state === 'allowed' ||
        latest.toolPermission?.state === 'denied' ||
        !isTurnOpen(event, openQuestionTurnIds)
      const onToolPermission = actions.onToolPermission
      const targetEvent = event
      return (
        <ToolPermissionCard
          request={targetEvent.toolPermission!}
          active={!isResolved}
          onDecision={async (decision) => {
            await onToolPermission(targetEvent, decision)
          }}
        />
      )
    }

    case 'question': {
      if (
        !Array.isArray(event.questions) ||
        event.questions.length === 0 ||
        !actions.onAnswer
      ) {
        return fallback
      }
      const active =
        isTurnOpen(event, openQuestionTurnIds) && !event.questionsAnswered
      const onAnswer = actions.onAnswer
      const targetEvent = event
      return (
        <UserQuestionCard
          questions={targetEvent.questions!}
          active={active}
          onSubmit={async (answers) => {
            await onAnswer(targetEvent, answers)
          }}
          answeredText={targetEvent.questionsAnswerText}
        />
      )
    }

    case 'mcp': {
      if (!event.mcpApproval || !actions.onMcpApprove || !actions.onMcpDecline) {
        return fallback
      }
      const active =
        isTurnOpen(event, openQuestionTurnIds) && !isEventSettled(event)
      const onMcpApprove = actions.onMcpApprove
      const onMcpDecline = actions.onMcpDecline
      const targetEvent = event
      return (
        <McpApprovalCard
          server={targetEvent.mcpApproval!}
          active={active}
          onApprove={async (secrets) => {
            await onMcpApprove(targetEvent, secrets)
          }}
          onDecline={async () => {
            await onMcpDecline(targetEvent)
          }}
        />
      )
    }

    case 'plugin': {
      if (
        !event.pluginApproval ||
        !actions.onPluginApprove ||
        !actions.onPluginDecline
      ) {
        return fallback
      }
      const active =
        isTurnOpen(event, openQuestionTurnIds) && !isEventSettled(event)
      if (!active) {
        return fallback
      }
      const onPluginApprove = actions.onPluginApprove
      const onPluginDecline = actions.onPluginDecline
      const targetEvent = event
      return (
        <PluginApprovalCard
          plugin={targetEvent.pluginApproval!}
          active={active}
          onApprove={async () => {
            await onPluginApprove(targetEvent)
          }}
          onDecline={async () => {
            await onPluginDecline(targetEvent)
          }}
        />
      )
    }

    case 'plan': {
      const approveAcceptHandler =
        actions.onApprovePlanAcceptEdits ?? actions.onApproveAcceptEdits
      const approveAskHandler =
        actions.onApprovePlanAskPermissions ?? actions.onApproveAskPermissions
      const requestChangesHandler =
        actions.onRequestPlanChanges ?? actions.onRequestChanges
      if (!approveAcceptHandler && !approveAskHandler) {
        return fallback
      }
      const { planPath, planContent } = extractPlanApprovalDetails(event)
      const latest = findLatestMatchingEvent(item, event, events)
      const isSettled =
        isEventSettled(event) ||
        isEventSettled(latest) ||
        event.toolPermission?.state === 'allowed' ||
        event.toolPermission?.state === 'denied' ||
        latest.toolPermission?.state === 'allowed' ||
        latest.toolPermission?.state === 'denied' ||
        !isTurnOpen(event, openQuestionTurnIds)
      const targetEvent = event
      return (
        <PlanApprovalCard
          planPath={planPath}
          planContent={planContent}
          disabled={isSettled}
          onApproveAcceptEdits={() => {
            void approveAcceptHandler?.(targetEvent)
          }}
          onApproveAskPermissions={() => {
            void approveAskHandler?.(targetEvent)
          }}
          onRequestChanges={
            requestChangesHandler
              ? (feedback) => {
                  void requestChangesHandler(feedback, targetEvent)
                }
              : undefined
          }
        />
      )
    }

    default:
      return fallback
  }
}

export function ScenePendingCards({
  pending,
  agents,
  events,
  actions,
  openQuestionTurnIds,
  onSelectAgent,
  sectionRef,
  variant = 'hud',
  testIdPrefix = 'scene-pending',
  hideSelectButton = false,
}: ScenePendingCardsProps): JSX.Element | null {
  if (pending.length === 0) return null

  const sectionClassName =
    variant === 'modal'
      ? 'scene-hud__card scene-hud__pending-section scene-agent-modal__pending-section'
      : 'scene-hud__card scene-hud__pending-section'

  return (
    <section
      ref={sectionRef}
      tabIndex={-1}
      className={sectionClassName}
      data-testid={`${testIdPrefix}-section`}
      aria-label="Esperando você"
    >
      <div className="scene-hud__section-header">
        <h3 className="scene-hud__section-title">Esperando você</h3>
        <span className="scene-hud__section-meta">
          {pending.length === 1
            ? '1 pendência'
            : `${pending.length} pendências`}
        </span>
      </div>

      <div className="scene-hud__pending-list">
        {pending.map((item) => {
          const agent = agents.find((ag) => ag.id === item.agentId)
          const agentName = sanitizeText(
            agent?.name,
            item.agentId === MAIN_AGENT_ID ? 'Orin' : 'Subagente',
          )
          const event = findPendingEvent(item, events)
          const cleanId = sanitizeText(item.id)

          return (
            <div
              key={item.id}
              className="scene-hud__pending-card"
              data-testid={`${testIdPrefix}-item-${cleanId}`}
              data-pending-kind={item.kind}
            >
              <div className="scene-hud__pending-header">
                <span className="scene-hud__pending-agent">{agentName}</span>
                {!hideSelectButton ? (
                  <button
                    type="button"
                    className="scene-hud__btn scene-hud__btn--compact"
                    data-testid={`${testIdPrefix}-select-${cleanId}`}
                    onClick={() => onSelectAgent(item.agentId)}
                  >
                    Ver na cena
                  </button>
                ) : null}
              </div>
              <div className="scene-hud__pending-body">
                {renderPendingCardBody(
                  item,
                  event,
                  events,
                  actions,
                  openQuestionTurnIds,
                )}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
