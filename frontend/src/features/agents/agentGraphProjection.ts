import type { ClientEvent } from '../../api/types'
import type { ExecutionProjection } from '../executions/executionProjection'

export type AgentVisualState = 'idle' | 'queued' | 'running' | 'waiting' | 'terminal'

export type AgentNode = {
  agentId: string
  executionId?: string
  visualState: AgentVisualState
}

export type AgentFact = 'delegation' | 'message' | 'result'

export type AgentEdge = {
  id: string
  from: string
  to: string
  fact: AgentFact
  eventId: string
}

export type AgentGraph = { nodes: AgentNode[]; edges: AgentEdge[] }

type DelegationLink = { parentAgentId: string; childAgentId: string; childExecutionId: string | null }

/**
 * BACKEND_DISCOVERY.md confirms Delegation.parent_execution_id/child_execution_id,
 * AgentMessage sender/recipient and AgentWait exist in the domain, but no adapter
 * publishes DelegationCreated/DelegationResultReturned/AgentMessageCreated/AgentWait*
 * on the public stream yet, so no wire field names are documented. This module reads
 * a locally assumed, minimal payload shape (documented in IMPLEMENTATION_PLAN.md,
 * Fase 4 "Decisões locais"): DelegationCreated.{delegation_id, child_execution_id,
 * child_agent_id}, DelegationResultReturned.{delegation_id}, AgentMessageCreated.
 * {sender_agent_id, recipient_agent_id}, AgentWait*.{wait_id}. Any payload missing
 * these fields is tolerated: the affected fact is dropped, never thrown.
 */
function readString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function baseVisualState(executionId: string | undefined, executions: Record<string, ExecutionProjection>): AgentVisualState {
  const view = executionId ? executions[executionId] : undefined
  if (!view) return 'idle'
  switch (view.state) {
    case 'QUEUED':
    case 'STARTING':
      return 'queued'
    case 'RUNNING':
    case 'WAITING_TOOL':
    case 'WAITING_USER':
      return 'running'
    case 'COMPLETED':
    case 'FAILED':
    case 'CANCELLED':
      return 'terminal'
    case 'PAUSED':
    default:
      return 'idle'
  }
}

/**
 * Projects the authorized ClientEvent stream into a compact multi-agent graph. A
 * node or edge is created only from a fact actually observed on an event; a child
 * execution's `parent_execution_id` alone never draws an edge (mirrors the Fase 3
 * rule that a Tool Call is never inferred from Execution transitions alone).
 */
export function projectAgentGraph(events: ClientEvent[], executions: Record<string, ExecutionProjection>): AgentGraph {
  const seenEventIds = new Set<string>()
  const nodes = new Map<string, AgentNode>()
  const edges = new Map<string, AgentEdge>()
  const waitingAgentIds = new Set<string>()
  const delegations = new Map<string, DelegationLink>()

  function computeVisualState(agentId: string, executionId: string | undefined): AgentVisualState {
    return waitingAgentIds.has(agentId) ? 'waiting' : baseVisualState(executionId, executions)
  }

  function upsertNode(agentId: string, executionId: string | undefined): void {
    const existing = nodes.get(agentId)
    const resolvedExecutionId = executionId ?? existing?.executionId
    nodes.set(agentId, { agentId, executionId: resolvedExecutionId, visualState: computeVisualState(agentId, resolvedExecutionId) })
  }

  function refreshVisualStates(): void {
    for (const node of nodes.values()) {
      nodes.set(node.agentId, { ...node, visualState: computeVisualState(node.agentId, node.executionId) })
    }
  }

  for (const sourceEvent of events) {
    if (seenEventIds.has(sourceEvent.event_id)) continue
    seenEventIds.add(sourceEvent.event_id)
    const payload = sourceEvent.payload

    if (sourceEvent.event_type === 'DelegationCreated') {
      const delegationId = readString(payload.delegation_id)
      const childExecutionId = readString(payload.child_execution_id)
      if (!childExecutionId) continue

      const parentExecutionId = sourceEvent.execution_id
      const parentAgentId = executions[parentExecutionId]?.agent_id ?? parentExecutionId
      const childAgentId = readString(payload.child_agent_id) ?? executions[childExecutionId]?.agent_id ?? childExecutionId

      upsertNode(parentAgentId, parentExecutionId)
      upsertNode(childAgentId, childExecutionId)
      if (delegationId) delegations.set(delegationId, { parentAgentId, childAgentId, childExecutionId })

      edges.set(sourceEvent.event_id, {
        id: `delegation-${sourceEvent.event_id}`,
        from: parentAgentId,
        to: childAgentId,
        fact: 'delegation',
        eventId: sourceEvent.event_id,
      })
      continue
    }

    if (sourceEvent.event_type === 'DelegationResultReturned') {
      const delegationId = readString(payload.delegation_id)
      const link = delegationId ? delegations.get(delegationId) : undefined
      if (!link) continue

      upsertNode(link.childAgentId, link.childExecutionId ?? undefined)
      upsertNode(link.parentAgentId, undefined)
      edges.set(sourceEvent.event_id, {
        id: `result-${sourceEvent.event_id}`,
        from: link.childAgentId,
        to: link.parentAgentId,
        fact: 'result',
        eventId: sourceEvent.event_id,
      })
      continue
    }

    if (sourceEvent.event_type === 'AgentMessageCreated') {
      const senderAgentId = readString(payload.sender_agent_id)
      const recipientAgentId = readString(payload.recipient_agent_id)
      if (!senderAgentId || !recipientAgentId) continue

      upsertNode(senderAgentId, undefined)
      upsertNode(recipientAgentId, undefined)
      edges.set(sourceEvent.event_id, {
        id: `message-${sourceEvent.event_id}`,
        from: senderAgentId,
        to: recipientAgentId,
        fact: 'message',
        eventId: sourceEvent.event_id,
      })
      continue
    }

    if (sourceEvent.event_type === 'AgentWaitRegistered') {
      const parentAgentId = executions[sourceEvent.execution_id]?.agent_id ?? sourceEvent.execution_id
      waitingAgentIds.add(parentAgentId)
      upsertNode(parentAgentId, sourceEvent.execution_id)
      refreshVisualStates()
      continue
    }

    if (sourceEvent.event_type === 'AgentWaitSatisfied') {
      const parentAgentId = executions[sourceEvent.execution_id]?.agent_id ?? sourceEvent.execution_id
      waitingAgentIds.delete(parentAgentId)
      refreshVisualStates()
      continue
    }
  }

  return {
    nodes: [...nodes.values()].sort((a, b) => a.agentId.localeCompare(b.agentId)),
    edges: [...edges.values()].sort((a, b) => a.id.localeCompare(b.id)),
  }
}
