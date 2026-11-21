import { useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import {
  deferConversationQueuedInput,
  type DeferQueuedInputResponse,
} from '../../api/conversations'
import { ApiError } from '../../api/errors'
import type { ConversationActivityEvent } from './activityTypes'
import './agentMessages.css'

export type FriendDeliveryStatus =
  | 'pending_in_turn'
  | 'injected'
  | 'queued_next_turn'
  | 'new_turn'
  | 'blocked'

export type FriendDeliveryItem = {
  id: string
  targetConversationId: string
  queueId: string | null
  turnId: string | null
  newTurnId?: string | null
  status: FriendDeliveryStatus
  deliveryFallback?: string | null
  reason?: string | null
  summary?: string | null
  updatedAt?: string | null
}

const DELIVERY_RANK: Record<FriendDeliveryStatus, number> = {
  pending_in_turn: 0,
  queued_next_turn: 1,
  new_turn: 1,
  injected: 2,
  blocked: 2,
}

export function friendDeliveryStatusLabel(status: FriendDeliveryStatus): string {
  switch (status) {
    case 'pending_in_turn':
      return 'Aguardando o próximo ponto seguro'
    case 'injected':
      return 'Entregue no turno em andamento'
    case 'queued_next_turn':
      return 'Na fila para depois do turno'
    case 'new_turn':
      return 'Novo turno iniciado'
    case 'blocked':
      return 'Bloqueada'
  }
}

function fallbackExplanation(fallback: string | null | undefined): string | null {
  switch (fallback) {
    case 'model_change':
      return 'Troca de provedor, modelo ou modo de permissão aplicada em um novo turno.'
    case 'queue_full':
      return 'A fila do turno em andamento estava cheia; a mensagem ficou para o próximo turno.'
    case 'turn_cancelling':
      return 'O turno alvo estava sendo cancelado; a mensagem ficou para o próximo turno.'
    default:
      return null
  }
}

function normalizeToolDeliveryStatus(raw: unknown): FriendDeliveryStatus | null {
  const value = typeof raw === 'string' ? raw.trim().toLowerCase() : ''
  if (
    value === 'pending_in_turn' ||
    value === 'queued_next_turn' ||
    value === 'new_turn' ||
    value === 'injected' ||
    value === 'blocked'
  ) {
    return value
  }
  return null
}

function normalizeWatchStatus(raw: unknown): FriendDeliveryStatus | null {
  const value = typeof raw === 'string' ? raw.trim().toLowerCase() : ''
  if (value === 'injected') return 'injected'
  if (value === 'promoted') return 'queued_next_turn'
  if (value === 'blocked') return 'blocked'
  return null
}

export function extractFriendDeliveryFromEvent(
  event: Pick<ConversationActivityEvent, 'type' | 'toolName' | 'payload' | 'summary' | 'occurredAt' | 'eventId'>,
): FriendDeliveryItem | null {
  const payload = event.payload ?? {}

  if (event.type === 'friend.delivery_status') {
    const status = normalizeWatchStatus(payload.status)
    const queueId = typeof payload.queue_id === 'string' && payload.queue_id.trim() ? payload.queue_id.trim() : null
    const targetConversationId =
      (typeof payload.target_conversation_id === 'string' && payload.target_conversation_id.trim()) || ''
    if (!status || !queueId) return null
    return {
      id: queueId,
      targetConversationId,
      queueId,
      turnId: typeof payload.target_turn_id === 'string' && payload.target_turn_id ? payload.target_turn_id : null,
      newTurnId: typeof payload.new_turn_id === 'string' && payload.new_turn_id ? payload.new_turn_id : null,
      status,
      reason: typeof payload.reason === 'string' && payload.reason.trim() ? payload.reason.trim() : null,
      summary:
        (typeof payload.summary === 'string' && payload.summary.trim()) ||
        (event.summary ? event.summary.trim() : null),
      updatedAt:
        (typeof payload.created_at === 'string' && payload.created_at) || event.occurredAt || null,
    }
  }

  const toolName = (event.toolName ?? (typeof payload.tool_name === 'string' ? payload.tool_name : '')).toLowerCase()
  const isFriendSendTool =
    (toolName === 'manage_session' || toolName === 'control_code_session' || payload.action === 'send_message') &&
    payload.action === 'send_message'
  if (!isFriendSendTool) return null

  const status = normalizeToolDeliveryStatus(payload.delivery)
  if (!status) return null
  const targetConversationId =
    (typeof payload.conversation_id === 'string' && payload.conversation_id.trim()) ||
    (typeof payload.code_conversation_id === 'string' && payload.code_conversation_id.trim()) ||
    ''
  if (!targetConversationId) return null

  const queueId = typeof payload.queue_id === 'string' && payload.queue_id.trim() ? payload.queue_id.trim() : null
  const turnId = typeof payload.turn_id === 'string' && payload.turn_id.trim() ? payload.turn_id.trim() : null
  const id = queueId ?? turnId ?? event.eventId

  return {
    id,
    targetConversationId,
    queueId,
    turnId,
    status,
    deliveryFallback:
      typeof payload.delivery_fallback === 'string' && payload.delivery_fallback.trim()
        ? payload.delivery_fallback.trim()
        : null,
    summary: event.summary ? event.summary.trim() : null,
    updatedAt: event.occurredAt ?? null,
  }
}

export function applyFriendDeliveryStatusEvent(
  state: Record<string, FriendDeliveryItem>,
  event: Pick<ConversationActivityEvent, 'type' | 'toolName' | 'payload' | 'summary' | 'occurredAt' | 'eventId'>,
): Record<string, FriendDeliveryItem> {
  const incoming = extractFriendDeliveryFromEvent(event)
  if (!incoming) return state
  const existing = state[incoming.id]
  if (!existing) {
    return { ...state, [incoming.id]: incoming }
  }
  const keepExistingStatus = DELIVERY_RANK[existing.status] > DELIVERY_RANK[incoming.status]
  const nextStatus = keepExistingStatus ? existing.status : incoming.status
  const merged: FriendDeliveryItem = {
    ...existing,
    ...incoming,
    targetConversationId: incoming.targetConversationId || existing.targetConversationId,
    queueId: incoming.queueId ?? existing.queueId,
    turnId: incoming.turnId ?? existing.turnId,
    newTurnId: incoming.newTurnId ?? existing.newTurnId,
    status: nextStatus,
    deliveryFallback: incoming.deliveryFallback ?? existing.deliveryFallback,
    reason: incoming.reason ?? existing.reason,
    summary: incoming.summary || existing.summary,
    updatedAt: incoming.updatedAt ?? existing.updatedAt,
  }
  if (
    merged.status === existing.status &&
    merged.reason === existing.reason &&
    merged.newTurnId === existing.newTurnId
  ) {
    return state
  }
  return { ...state, [incoming.id]: merged }
}

export type FriendDeliveryCardProps = {
  delivery: FriendDeliveryItem
  client?: ApiClient
  onDefer?: (targetConversationId: string, queueId: string) => Promise<DeferQueuedInputResponse | void>
  onDeferred?: (result: { queueId: string; turnId?: string | null }) => void
  onOpenTarget?: (conversationId: string) => void
}

export function FriendDeliveryCard({
  delivery,
  client,
  onDefer,
  onDeferred,
  onOpenTarget,
}: FriendDeliveryCardProps) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [localDeferred, setLocalDeferred] = useState(false)
  const [deferring, setDeferring] = useState(false)
  const [deferError, setDeferError] = useState<string | null>(null)

  const effectiveStatus: FriendDeliveryStatus =
    localDeferred && delivery.status === 'pending_in_turn' ? 'queued_next_turn' : delivery.status
  const statusLabel = friendDeliveryStatusLabel(effectiveStatus)
  const canDefer =
    effectiveStatus === 'pending_in_turn' &&
    Boolean(delivery.queueId) &&
    Boolean(delivery.targetConversationId)
  const fallbackNote = fallbackExplanation(delivery.deliveryFallback)

  async function handleDefer() {
    if (!canDefer || !delivery.queueId || deferring) return
    setDeferring(true)
    setDeferError(null)
    try {
      const response = onDefer
        ? await onDefer(delivery.targetConversationId, delivery.queueId)
        : await deferConversationQueuedInput(apiClient, delivery.targetConversationId, delivery.queueId)
      setLocalDeferred(true)
      onDeferred?.({
        queueId: delivery.queueId,
        turnId: response && typeof response.turn_id === 'string' ? response.turn_id : null,
      })
    } catch (caught: unknown) {
      if (caught instanceof ApiError && (caught.status === 404 || caught.status === 409)) {
        setDeferError('O turno em andamento já leu esta mensagem.')
      } else {
        setDeferError('Não foi possível deixar a mensagem para depois do turno.')
      }
    } finally {
      setDeferring(false)
    }
  }

  return (
    <section
      className="friend-delivery-card"
      data-testid="friend-delivery-card"
      data-delivery-status={effectiveStatus}
      aria-label="Entrega do Orin Friend"
    >
      <div className="friend-delivery-card__header">
        <span className="friend-delivery-card__title">Entrega na sessão</span>
        <span
          className={`friend-delivery-card__status friend-delivery-card__status--${effectiveStatus}`}
          data-testid="friend-delivery-status"
        >
          {statusLabel}
        </span>
      </div>

      {delivery.summary && effectiveStatus !== 'blocked' && (
        <p className="friend-delivery-card__summary">{delivery.summary}</p>
      )}

      {effectiveStatus === 'blocked' && (
        <p className="friend-delivery-card__note" role="alert">
          {delivery.reason
            ? `A mensagem não pôde ser aplicada: ${delivery.reason}`
            : 'A mensagem foi bloqueada por um hook e não foi aplicada.'}
        </p>
      )}

      {fallbackNote && <p className="friend-delivery-card__note">{fallbackNote}</p>}

      {deferError && (
        <p className="agent-msg-box__error" role="alert">
          {deferError}
        </p>
      )}

      {(canDefer || (onOpenTarget && delivery.targetConversationId)) && (
        <div className="friend-delivery-card__actions">
          {canDefer && (
            <button
              type="button"
              className="agent-msg-box__btn"
              data-testid="friend-delivery-defer-button"
              disabled={deferring}
              onClick={() => void handleDefer()}
            >
              {deferring ? 'Adiando...' : 'Deixar para depois do turno'}
            </button>
          )}
          {onOpenTarget && delivery.targetConversationId && (
            <button
              type="button"
              className="agent-msg-box__btn"
              onClick={() => onOpenTarget(delivery.targetConversationId)}
            >
              Abrir sessão
            </button>
          )}
        </div>
      )}
    </section>
  )
}
