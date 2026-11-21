/**
 * Pure state reducer for direct messages sent to running subagents (WS-B7, B-RF11).
 *
 * Keeps per-agent message lists synchronized from both the listing endpoint
 * (`GET /v1/conversations/{id}/agents/{agent_id}/messages`) and live SSE events
 * (`agent.message_queued`, `agent.message_delivered`, `agent.message_forwarded`, `agent.message_expired`).
 *
 * Invariant: before `agent.message_delivered` (or a persisted `delivered` row), the
 * visual state is strictly `'queued'` ("Na fila"), never `'delivered'`.
 */

import type {
  AgentMessageSenderKind,
  AgentMessageState,
  ConversationAgentMessage,
} from '../../api/conversations'
import type { ConversationActivityEvent } from './activityTypes'

export type AgentMessageVisualState = 'queued' | 'delivered' | 'forwarded' | 'expired' | 'stopped'

export type AgentMessageItem = {
  controlId: string
  agentId: string
  senderKind: AgentMessageSenderKind
  senderAgentId: string | null
  message: string
  preview: string
  state: AgentMessageState
  visualState: AgentMessageVisualState
  createdAt: string | null
  deliveredAt: string | null
}

export type AgentMessagesState = Record<string, AgentMessageItem[]>

const STATE_RANK: Record<AgentMessageState, number> = {
  queued: 0,
  injected: 1,
  delivered: 2,
  forwarded: 2,
  expired: 2,
  stopped: 2,
}

export function toVisualAgentMessageState(state: AgentMessageState): AgentMessageVisualState {
  if (state === 'injected' || state === 'queued') {
    return 'queued'
  }
  return state
}

export function agentMessageStateLabel(state: AgentMessageState | AgentMessageVisualState): string {
  switch (state) {
    case 'queued':
    case 'injected':
      return 'Na fila'
    case 'delivered':
      return 'Entregue'
    case 'forwarded':
      return 'Repassada ao Orin'
    case 'expired':
      return 'Expirou'
    case 'stopped':
      return 'Parado'
  }
}

function mergeMessageItem(existing: AgentMessageItem | undefined, incoming: AgentMessageItem): AgentMessageItem {
  if (!existing) {
    return incoming
  }
  const keepExistingState = STATE_RANK[existing.state] > STATE_RANK[incoming.state]
  const nextState = keepExistingState ? existing.state : incoming.state
  return {
    controlId: incoming.controlId,
    agentId: incoming.agentId || existing.agentId,
    senderKind: incoming.senderKind || existing.senderKind,
    senderAgentId: incoming.senderAgentId ?? existing.senderAgentId,
    message: incoming.message || existing.message,
    preview: incoming.preview || existing.preview,
    state: nextState,
    visualState: toVisualAgentMessageState(nextState),
    createdAt: existing.createdAt ?? incoming.createdAt,
    deliveredAt:
      nextState === 'delivered'
        ? incoming.deliveredAt ?? existing.deliveredAt
        : existing.deliveredAt ?? incoming.deliveredAt,
  }
}

function upsertForAgent(
  state: AgentMessagesState,
  agentId: string,
  incoming: AgentMessageItem,
): AgentMessagesState {
  if (!agentId || !incoming.controlId) {
    return state
  }
  const current = state[agentId] ?? []
  const idx = current.findIndex((item) => item.controlId === incoming.controlId)
  if (idx === -1) {
    return { ...state, [agentId]: [...current, incoming] }
  }
  const merged = mergeMessageItem(current[idx], incoming)
  if (
    merged.state === current[idx].state &&
    merged.deliveredAt === current[idx].deliveredAt &&
    merged.message === current[idx].message &&
    merged.preview === current[idx].preview
  ) {
    return state
  }
  const next = current.slice()
  next[idx] = merged
  return { ...state, [agentId]: next }
}

export function hydrateAgentMessages(
  state: AgentMessagesState,
  agentId: string,
  listed: readonly ConversationAgentMessage[],
): AgentMessagesState {
  if (!agentId) {
    return state
  }
  const existingById = new Map<string, AgentMessageItem>(
    (state[agentId] ?? []).map((item) => [item.controlId, item]),
  )
  const ordered: AgentMessageItem[] = []
  const seen = new Set<string>()
  for (const row of listed) {
    if (!row.control_id) {
      continue
    }
    seen.add(row.control_id)
    const incoming: AgentMessageItem = {
      controlId: row.control_id,
      agentId: row.agent_id || agentId,
      senderKind: row.sender_kind,
      senderAgentId: row.sender_agent_id,
      message: row.message,
      preview: row.preview || row.message.slice(0, 200),
      state: row.state,
      visualState: toVisualAgentMessageState(row.state),
      createdAt: row.created_at,
      deliveredAt: row.delivered_at,
    }
    ordered.push(mergeMessageItem(existingById.get(row.control_id), incoming))
  }
  for (const prev of state[agentId] ?? []) {
    if (!seen.has(prev.controlId)) {
      ordered.push(prev)
    }
  }
  return { ...state, [agentId]: ordered }
}

export function recordOptimisticAgentMessage(
  state: AgentMessagesState,
  input: {
    controlId: string
    agentId: string
    message: string
    senderKind?: AgentMessageSenderKind
    senderAgentId?: string | null
    createdAt?: string | null
  },
): AgentMessagesState {
  const cleanMsg = input.message.trim()
  const item: AgentMessageItem = {
    controlId: input.controlId,
    agentId: input.agentId,
    senderKind: input.senderKind ?? 'user',
    senderAgentId: input.senderAgentId ?? null,
    message: cleanMsg,
    preview: cleanMsg.slice(0, 200),
    state: 'queued',
    visualState: 'queued',
    createdAt: input.createdAt ?? new Date().toISOString(),
    deliveredAt: null,
  }
  return upsertForAgent(state, input.agentId, item)
}

function eventStateFromType(eventType: string): AgentMessageState | null {
  switch (eventType) {
    case 'agent.message_queued':
      return 'queued'
    case 'agent.message_delivered':
      return 'delivered'
    case 'agent.message_forwarded':
      return 'forwarded'
    case 'agent.message_expired':
      return 'expired'
    default:
      return null
  }
}

export function applyAgentMessageEvent(
  state: AgentMessagesState,
  event: Pick<ConversationActivityEvent, 'type' | 'agentId' | 'payload' | 'occurredAt'>,
): AgentMessagesState {
  const nextState = eventStateFromType(event.type)
  if (!nextState) {
    return state
  }
  const payload = event.payload ?? {}
  const controlId = typeof payload.control_id === 'string' ? payload.control_id.trim() : ''
  if (!controlId) {
    return state
  }
  const agentId =
    (typeof payload.agent_id === 'string' && payload.agent_id.trim()) ||
    (typeof event.agentId === 'string' ? event.agentId.trim() : '')
  if (!agentId) {
    return state
  }
  const rawSender = typeof payload.sender_kind === 'string' ? payload.sender_kind.trim().toLowerCase() : 'user'
  const senderKind: AgentMessageSenderKind = rawSender === 'parent' ? 'parent' : 'user'
  const senderAgentId =
    typeof payload.sender_agent_id === 'string' && payload.sender_agent_id.trim()
      ? payload.sender_agent_id.trim()
      : null
  const preview = typeof payload.preview === 'string' ? payload.preview : ''
  const deliveredAt =
    nextState === 'delivered'
      ? (typeof payload.delivered_at === 'string' && payload.delivered_at ? payload.delivered_at : event.occurredAt ?? null)
      : null
  const createdAt = nextState === 'queued' ? event.occurredAt ?? null : null

  const incoming: AgentMessageItem = {
    controlId,
    agentId,
    senderKind,
    senderAgentId,
    message: preview,
    preview,
    state: nextState,
    visualState: toVisualAgentMessageState(nextState),
    createdAt,
    deliveredAt,
  }
  return upsertForAgent(state, agentId, incoming)
}

export function getAgentMessages(state: AgentMessagesState, agentId: string): readonly AgentMessageItem[] {
  return state[agentId] ?? []
}
