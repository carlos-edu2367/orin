import type { ConversationActivityEvent } from '../conversations/activityTypes'
import { isFreshNotice } from '../conversations/PushNotificationToast'
import type { NotifySoundKind } from './notifySound'

export type AgentNoticeKind = NotifySoundKind

export type AgentNotice = {
  kind: AgentNoticeKind
  title: string
  body: string
}

export type AgentNoticeOptions = {
  system_notifications?: boolean
  notify_permissions?: boolean
  notify_completion?: boolean
  notification_sound?: boolean
}

export type AgentNoticeContext = {
  mode?: 'chat' | 'code' | 'friend' | 'cowork' | string
  away?: boolean
  now?: number
  maxAgeMs?: number
  shownIds?: Set<string>
  seenEventIds?: Set<string>
  notifiedCodeTurns?: Set<string>
  options?: AgentNoticeOptions
  settings?: AgentNoticeOptions
  system_notifications?: boolean
  notify_permissions?: boolean
  notify_completion?: boolean
}

export type NoticeEventInput = Partial<ConversationActivityEvent> & {
  type: string
  eventId?: string
}

function resolveActorName(event: NoticeEventInput): string {
  const rawAgentName =
    event.agentName ??
    (typeof event.payload?.agent_name === 'string' ? event.payload.agent_name : undefined)
  if (typeof rawAgentName === 'string' && rawAgentName.trim()) {
    const clean = rawAgentName.trim()
    if (clean.toLowerCase() !== 'main' && clean.toLowerCase() !== 'agent:main') {
      return clean
    }
  }
  if (event.parentAgentId && typeof event.agentId === 'string' && event.agentId.trim()) {
    const cleanId = event.agentId.trim().replace(/^agent:/i, '')
    if (cleanId && cleanId.toLowerCase() !== 'main') {
      return cleanId
    }
  }
  return 'Orin'
}

function resolveToolName(event: NoticeEventInput): string | null {
  const candidate =
    event.toolPermission?.toolName ??
    event.toolName ??
    event.tool ??
    (typeof event.payload?.tool_name === 'string' ? event.payload.tool_name : undefined)
  if (typeof candidate === 'string' && candidate.trim()) {
    return candidate.trim()
  }
  return null
}

function isUserFacingHostRequest(event: NoticeEventInput): boolean {
  if (event.toolPermission) return true
  if (Array.isArray(event.questions) && event.questions.length > 0) return true
  const payload = event.payload
  if (!payload || typeof payload !== 'object') return false
  if (payload.user_facing === true || payload.requires_user === true) return true
  const kind = typeof payload.kind === 'string' ? payload.kind : ''
  // Standard W6 host.request events ('hook_callback', 'mcp_message') target the SDK host process, not the person.
  if (kind === 'user_question' || kind === 'permission' || kind === 'user_decision') {
    return true
  }
  return false
}

function candidateNotice(event: NoticeEventInput): AgentNotice | null {
  const { type } = event
  const isSubagent = Boolean(event.parentAgentId)

  // 1. Attention events
  if (type === 'tool.permission_required') {
    if (event.toolPermission?.state === 'allowed' || event.toolPermission?.state === 'denied') {
      return null
    }
    const actor = resolveActorName(event)
    const tool = resolveToolName(event)
    return {
      kind: 'attention',
      title: `${actor} precisa da sua permissão`,
      body: tool
        ? `Autorize o uso da ferramenta ${tool} para continuar.`
        : 'Há uma ação aguardando sua aprovação.',
    }
  }

  if (type === 'workspace.trust_required') {
    const folder = event.workspaceTrust?.path
    return {
      kind: 'attention',
      title: 'Confiança na pasta necessária',
      body: folder
        ? `Confirme se confia na pasta ${folder} para continuar.`
        : 'Confirme se confia na pasta do projeto para continuar.',
    }
  }

  if (type === 'turn.waiting_user') {
    if (isSubagent) {
      const actor = resolveActorName(event)
      return {
        kind: 'attention',
        title: `${actor} está esperando você`,
        body: 'Há uma pergunta para você responder na conversa.',
      }
    }
    return {
      kind: 'attention',
      title: 'O Orin está esperando você',
      body: 'Há uma pergunta para você responder na conversa.',
    }
  }

  if (type === 'code_mode.decision_required') {
    return {
      kind: 'attention',
      title: 'Decisão necessária no Modo Code',
      body: 'O Orin precisa da sua confirmação para continuar.',
    }
  }

  if (type === 'host.request') {
    if (!isUserFacingHostRequest(event)) return null
    const tool = resolveToolName(event)
    return {
      kind: 'attention',
      title: 'O Orin precisa da sua confirmação',
      body: tool
        ? `Confirme a solicitação para ${tool}.`
        : event.summary?.trim() || 'Há uma decisão aguardando sua resposta.',
    }
  }

  // Subagents finishing or failing do not trigger completion/failure notifications (noise).
  if (isSubagent) {
    return null
  }

  // 2. Completion ('done') events for main agent
  if (type === 'code_mode.completed') {
    return {
      kind: 'done',
      title: 'Modo Code concluído',
      body: 'A entrega está pronta para sua revisão.',
    }
  }

  if (type === 'code_mode.completed_with_caveats') {
    return {
      kind: 'done',
      title: 'Modo Code concluído com ressalvas',
      body: 'A entrega está pronta, mas há uma limitação documentada.',
    }
  }

  if (type === 'turn.completed') {
    return {
      kind: 'done',
      title: 'Orin respondeu',
      body: 'A resposta está pronta.',
    }
  }

  // 3. Failure ('failed') events for main agent
  if (type === 'code_mode.blocked') {
    return {
      kind: 'failed',
      title: 'Modo Code bloqueado',
      body: 'O Orin encontrou um bloqueio que precisa da sua decisão.',
    }
  }

  if (type === 'turn.failed') {
    if (
      event.errorCode === 'TURN_CANCELLED' ||
      event.status === 'cancelled' ||
      event.state === 'cancelled'
    ) {
      return null
    }
    return {
      kind: 'failed',
      title: 'O turno falhou',
      body: 'O Orin encontrou um erro ao concluir a resposta.',
    }
  }

  return null
}

/**
 * Pure decision function for system notifications on conversation activity events.
 * Respects user preferences, freshness window, eventId deduplication, and avoids duplicate
 * completion notifications in Code mode when `code_mode.completed*` precedes `turn.completed`.
 */
export function noticeForEvent(
  event: NoticeEventInput,
  ctx: AgentNoticeContext = {},
): AgentNotice | null {
  if (ctx.away === false) return null

  const opts = ctx.options ?? ctx.settings
  const systemNotifications = ctx.system_notifications ?? opts?.system_notifications ?? true
  if (!systemNotifications) return null

  const notifyPermissions = ctx.notify_permissions ?? opts?.notify_permissions ?? true
  const notifyCompletion = ctx.notify_completion ?? opts?.notify_completion ?? true

  const now = ctx.now ?? Date.now()
  const maxAgeMs = ctx.maxAgeMs ?? 120_000
  if (!isFreshNotice(event.occurredAt, now, maxAgeMs)) {
    return null
  }

  const shownIds = ctx.shownIds ?? ctx.seenEventIds
  const eventId = typeof event.eventId === 'string' && event.eventId.trim() ? event.eventId.trim() : null
  if (eventId && shownIds?.has(eventId)) {
    return null
  }

  const notice = candidateNotice(event)
  if (!notice) return null

  if (notice.kind === 'attention' && !notifyPermissions) {
    return null
  }
  if ((notice.kind === 'done' || notice.kind === 'failed') && !notifyCompletion) {
    return null
  }

  const turnId = typeof event.turnId === 'string' && event.turnId.trim() ? event.turnId.trim() : '__turn__'
  const codeDoneKey = `code_done:${turnId}`

  if (
    event.type === 'turn.completed' &&
    (ctx.notifiedCodeTurns?.has(turnId) || shownIds?.has(codeDoneKey))
  ) {
    return null
  }

  if (
    event.type === 'code_mode.completed' ||
    event.type === 'code_mode.completed_with_caveats'
  ) {
    ctx.notifiedCodeTurns?.add(turnId)
    shownIds?.add(codeDoneKey)
  }

  if (eventId && shownIds) {
    shownIds.add(eventId)
  }

  return notice
}
