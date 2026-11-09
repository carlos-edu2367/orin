import type { ConversationActivityEvent } from '../activityTypes'
import { orderAgentTree } from '../agentTree'
import { formatTimeHHMM, sanitizeText } from '../teamActivityModel'
import { isAgentTool, stationForTool } from './sceneStations'
import {
  MAIN_AGENT_ID,
  type SceneActivity,
  type SceneAgent,
  type SceneAgentStatus,
  type SceneBubble,
  type SceneEffect,
  type ScenePending,
  type ScenePendingKind,
  type ScenePose,
  type SceneState,
  type SceneStationId,
  type SceneTransfer,
  type SceneTransferKind,
} from './sceneTypes'

const MAX_AGENT_ACTIVITIES = 30
const RECENT_EVENT_WINDOW = 40
const MAX_BUBBLE_CHARS = 60
const MAX_THOUGHT_CHARS = 200
// Raw tail kept from streamed text. Sanitizing the whole buffer on every delta made each fold O(k^2).
const THOUGHT_BUFFER_CHARS = MAX_THOUGHT_CHARS * 2
const SPEECH_BUFFER_CHARS = MAX_BUBBLE_CHARS * 4

// Events that say something happened to the turn or arrived to an agent, not what the agent is doing.
const NON_TASK_EVENT_TYPES = new Set<string>([
  'agent.thought_delta',
  'AGENT_THOUGHT_DELTA',
  'assistant.delta',
  'assistant.completed',
  'agent.message_received',
  'delegation.completed',
  'context.updated',
  'memory.learned',
  'todo.updated',
  'notification.pushed',
])
// Receipts carry the child's agent id but describe the parent receiving: never the child's activity.
const NON_ACTIVITY_EVENT_TYPES = new Set<string>([
  'agent.thought_delta',
  'AGENT_THOUGHT_DELTA',
  'assistant.delta',
  'agent.message_received',
  'delegation.completed',
])

function isLifecycleEventType(type: string): boolean {
  return /^(turn|execution)[._]/i.test(type)
}

function appendTail(buffer: string, chunk: string, maxChars: number): string {
  const joined = buffer + chunk
  if (joined.length <= maxChars) return joined
  // Drop a low surrogate orphaned by the cut.
  return joined.slice(-maxChars).replace(/^[\uDC00-\uDFFF]/, '')
}

const STATUS_LABELS: Record<SceneAgentStatus, string> = {
  working: 'Trabalhando',
  thinking: 'Pensando',
  waiting_agent: 'Esperando um agente',
  waiting_user: 'Esperando você',
  queued: 'Na fila',
  stopped: 'parado por você',
  failed: 'Falhou',
  completed: 'Concluído',
  resting: 'Descansando',
}

export function statusLabelFor(status: SceneAgentStatus): string {
  return STATUS_LABELS[status] ?? 'Descansando'
}

interface MutableAgent {
  id: string
  isMain: boolean
  name: string
  subagentType?: string
  parentId: string | null
  depth: number
  station: SceneStationId
  pose: ScenePose
  status: SceneAgentStatus
  task: string
  currentTool: string | null
  lastThought: string
  thoughtBuffer: string
  speechBuffer: string
  bubble: SceneBubble | null
  activities: SceneActivity[]
  validTimestamps: number[]
  totalElapsedMs: number
}

function normalizeAgentId(rawId: unknown): string {
  const cleaned = sanitizeText(rawId)
  if (!cleaned) return MAIN_AGENT_ID
  const lower = cleaned.toLowerCase()
  if (
    lower === MAIN_AGENT_ID ||
    lower === 'main' ||
    lower === 'assistant' ||
    lower.endsWith(':main')
  ) {
    return MAIN_AGENT_ID
  }
  return cleaned
}

function truncateTail(
  text: string,
  maxChars: number,
  prefixEllipsis = false,
  alreadySanitized = false,
): string {
  const cleaned = alreadySanitized ? text : sanitizeText(text)
  if (!cleaned) return ''
  if (prefixEllipsis) {
    const sliceLen = Math.max(1, maxChars - 1)
    return `…${cleaned.slice(-sliceLen)}`
  }
  if (cleaned.length <= maxChars) return cleaned
  return `…${cleaned.slice(-(maxChars - 1))}`
}

function truncateHead(text: string, maxChars: number): string {
  const cleaned = sanitizeText(text)
  if (!cleaned) return ''
  if (cleaned.length <= maxChars) return cleaned
  return `${cleaned.slice(0, maxChars - 1)}…`
}

let cachedTimestampRaw: string | undefined
let cachedTimestampMs: number | null = null

function parseTimestampMs(raw?: string): number | null {
  if (!raw) return null
  if (raw === cachedTimestampRaw) return cachedTimestampMs
  const cleaned = sanitizeText(raw)
  if (!cleaned) {
    cachedTimestampRaw = raw
    cachedTimestampMs = null
    return null
  }
  const ms = Date.parse(cleaned)
  const valid = Number.isFinite(ms) ? ms : null
  cachedTimestampRaw = raw
  cachedTimestampMs = valid
  return valid
}

function computeDurationLabel(validTimestamps: number[], totalElapsedMs: number): string {
  if (validTimestamps.length >= 2) {
    const diffMs = Math.max(...validTimestamps) - Math.min(...validTimestamps)
    const mins = Math.max(1, Math.round(diffMs / 60000))
    return `${mins} min`
  }
  if (totalElapsedMs > 0) {
    const mins = Math.max(1, Math.round(totalElapsedMs / 60000))
    return `${mins} min`
  }
  return '1 min'
}

function selectLatestTurnEvents(events: ConversationActivityEvent[]): {
  turnId: string | null
  turnEvents: ConversationActivityEvent[]
} {
  let lastTurnStartedIdx = -1
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const t = events[i].type
    if (t === 'turn.started' || t === 'TURN_STARTED') {
      lastTurnStartedIdx = i
      break
    }
  }

  let activeTurnId: string | null = null
  if (lastTurnStartedIdx >= 0) {
    activeTurnId = sanitizeText(events[lastTurnStartedIdx].turnId) || null
  }
  if (!activeTurnId) {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const candidate = sanitizeText(events[i].turnId)
      if (candidate) {
        activeTurnId = candidate
        break
      }
    }
  }

  if (activeTurnId) {
    const filtered = events.filter((ev, idx) => {
      const evTurn = sanitizeText(ev.turnId)
      if (evTurn) return evTurn === activeTurnId
      return lastTurnStartedIdx >= 0 ? idx >= lastTurnStartedIdx : true
    })
    return { turnId: activeTurnId, turnEvents: filtered }
  }

  if (lastTurnStartedIdx >= 0) {
    return { turnId: null, turnEvents: events.slice(lastTurnStartedIdx) }
  }

  return { turnId: null, turnEvents: events }
}

function isUserWaitingEvent(ev: ConversationActivityEvent): boolean {
  if (ev.type === 'tool.permission_required' || ev.type === 'turn.waiting_user') return true
  if (ev.toolPermission && ev.toolPermission.state !== 'allowed' && ev.toolPermission.state !== 'denied') {
    return true
  }
  if (ev.planApproval && ev.state !== 'completed' && ev.state !== 'failed') return true
  if (Array.isArray(ev.questions) && ev.questions.length > 0 && ev.questionsAnswered !== true) {
    return true
  }
  if (ev.mcpApproval && ev.state !== 'completed' && ev.state !== 'failed') return true
  if (ev.pluginApproval && ev.state !== 'completed' && ev.state !== 'failed') return true
  return false
}

export function foldScene(
  events: readonly ConversationActivityEvent[] | null | undefined,
  options: { running: boolean } = { running: false },
): SceneState {
  const running = Boolean(options?.running)
  const validEvents: ConversationActivityEvent[] = []
  if (Array.isArray(events)) {
    for (const item of events) {
      if (item && typeof item === 'object' && !Array.isArray(item)) {
        validEvents.push(item)
      }
    }
  }

  const { turnId, turnEvents } = selectLatestTurnEvents(validEvents)
  const recentCutoffIdx = Math.max(0, turnEvents.length - RECENT_EVENT_WINDOW)

  const agentsMap = new Map<string, MutableAgent>()
  const orin: MutableAgent = {
    id: MAIN_AGENT_ID,
    isMain: true,
    name: 'Orin',
    parentId: null,
    depth: 0,
    station: running ? 'meeting' : 'lounge',
    pose: running ? 'idle' : 'sit',
    status: running ? 'working' : 'resting',
    task: '',
    currentTool: null,
    lastThought: '',
    thoughtBuffer: '',
    speechBuffer: '',
    bubble: null,
    activities: [],
    validTimestamps: [],
    totalElapsedMs: 0,
  }
  agentsMap.set(MAIN_AGENT_ID, orin)

  const ensureAgent = (rawId: unknown): MutableAgent => {
    const id = normalizeAgentId(rawId)
    const existing = agentsMap.get(id)
    if (existing) return existing
    const created: MutableAgent = {
      id,
      isMain: false,
      name: 'Subagente',
      parentId: MAIN_AGENT_ID,
      depth: 1,
      station: 'meeting',
      pose: 'idle',
      status: 'working',
      task: '',
      currentTool: null,
      lastThought: '',
      thoughtBuffer: '',
      speechBuffer: '',
      bubble: null,
      activities: [],
      validTimestamps: [],
      totalElapsedMs: 0,
    }
    agentsMap.set(id, created)
    return created
  }

  let alarmEventId: string | null = null
  // The turn ended with a classic `turn.waiting_user` (not answered inside the turn).
  let endedWaitingUser = false
  // Children each parent delegated to and is still waiting on (parallel ask_agents).
  const openDelegations = new Map<string, Set<string>>()
  const openDelegation = (parentId: string, childId: string) => {
    if (parentId === childId) return
    const open = openDelegations.get(parentId) ?? new Set<string>()
    open.add(childId)
    openDelegations.set(parentId, open)
  }
  // Closes one delegation; true when the parent has nothing else to wait for.
  const closeDelegation = (parentId: string, childId: string): boolean => {
    const open = openDelegations.get(parentId)
    if (!open) return true
    open.delete(childId)
    return open.size === 0
  }
  const transfers: SceneTransfer[] = []
  const effects: SceneEffect[] = []
  let pending: ScenePending[] = []
  const delegatedAgentIds = new Set<string>()

  const pushTransfer = (idx: number, transfer: SceneTransfer) => {
    if (idx >= recentCutoffIdx) {
      transfers.push(transfer)
    }
  }

  const pushEffect = (idx: number, effect: SceneEffect) => {
    if (idx >= recentCutoffIdx) {
      effects.push(effect)
    }
  }

  const upsertPending = (item: ScenePending) => {
    const existingIdx = pending.findIndex(
      (p) => (item.requestId && p.requestId === item.requestId) || p.id === item.id,
    )
    if (existingIdx >= 0) {
      pending[existingIdx] = item
    } else {
      pending.push(item)
    }
  }

  for (let i = 0; i < turnEvents.length; i += 1) {
    const ev = turnEvents[i]
    const evId = sanitizeText(ev.eventId, `ev-${i}`)
    const type = sanitizeText(ev.type)
    const actor = ensureAgent(ev.agentId)

    // Update subagent metadata when seen on its own event (excluding message_sent where agentName is recipient).
    if (!actor.isMain) {
      const parentRaw = sanitizeText(ev.parentAgentId)
      if (parentRaw) {
        const normalizedParent = normalizeAgentId(parentRaw)
        if (normalizedParent !== actor.id) {
          actor.parentId = normalizedParent
        }
      }
      if (type !== 'agent.message_sent' && type !== 'AGENT_MESSAGE_SENT') {
        const candidateName = sanitizeText(ev.agentName)
        if (candidateName) {
          actor.name = candidateName
        }
      }
      const subType = sanitizeText(ev.payload?.subagent_type)
      if (subType) {
        actor.subagentType = subType
      }
    }

    // Track timestamps and elapsed duration for this agent.
    const parsedMs = parseTimestampMs(ev.occurredAt)
    if (parsedMs !== null) {
      actor.validTimestamps.push(parsedMs)
    }
    if (typeof ev.elapsedMs === 'number' && Number.isFinite(ev.elapsedMs) && ev.elapsedMs > 0) {
      actor.totalElapsedMs += ev.elapsedMs
    }

    const isUserDirectMessage =
      (type === 'agent.message_sent' || type === 'AGENT_MESSAGE_SENT') &&
      sanitizeText(ev.payload?.source) === 'user'

    // Record human-readable activity item for the agent.
    if (!NON_ACTIVITY_EVENT_TYPES.has(type)) {
      const candidateSummary = sanitizeText(
        ev.summary || ev.description || ev.title || ev.toolName,
      )
      if (
        candidateSummary &&
        !NON_TASK_EVENT_TYPES.has(type) &&
        !isLifecycleEventType(type) &&
        !isUserDirectMessage
      ) {
        actor.task = candidateSummary
      }
      if (candidateSummary) {
        const detail = sanitizeText(ev.toolName || ev.path || '')
        const timeStr = ev.occurredAt ? formatTimeHHMM(ev.occurredAt, '') : ''
        actor.activities.push({
          id: evId,
          label: candidateSummary,
          ...(detail ? { detail } : {}),
          ...(timeStr ? { timestamp: timeStr } : {}),
        })
        if (actor.activities.length > MAX_AGENT_ACTIVITIES) {
          actor.activities.splice(0, actor.activities.length - MAX_AGENT_ACTIVITIES)
        }
      }
    }

    // Clear pending items of this agent when a subsequent non-waiting event arrives.
    if (pending.length > 0 && !isUserWaitingEvent(ev)) {
      pending = pending.filter((p) => p.agentId !== actor.id)
    }

    // Check explicit permission resolution on any event carrying resolved toolPermission or type tool.permission_resolved.
    const permState = ev.toolPermission?.state
    if (
      type === 'tool.permission_resolved' ||
      permState === 'allowed' ||
      permState === 'denied'
    ) {
      const reqId = sanitizeText(
        ev.toolPermission?.requestId ||
          (typeof ev.payload?.request_id === 'string' ? ev.payload.request_id : ''),
      )
      pending = pending.filter((p) => {
        if (reqId && p.requestId === reqId) return false
        if (!reqId && p.agentId === actor.id && (p.kind === 'permission' || p.kind === 'plan')) {
          return false
        }
        return true
      })
      const denied =
        permState === 'denied' ||
        ev.status === 'denied' ||
        ev.payload?.decision === 'deny' ||
        ev.payload?.state === 'denied'
      if (denied) {
        actor.bubble = { kind: 'speech', text: 'ok', eventId: evId }
      }
      actor.pose = 'idle'
      actor.status = 'working'
      if (type === 'tool.permission_resolved') {
        continue
      }
    }

    // Check pending user approvals / questions on the event.
    if (
      type === 'tool.permission_required' ||
      (ev.toolPermission && permState !== 'allowed' && permState !== 'denied') ||
      (ev.planApproval && ev.state !== 'completed' && ev.state !== 'failed')
    ) {
      const kind: ScenePendingKind =
        ev.toolPermission?.planApproval || ev.planApproval ? 'plan' : 'permission'
      const reqId =
        sanitizeText(
          ev.toolPermission?.requestId ||
            (typeof ev.payload?.request_id === 'string' ? ev.payload.request_id : ''),
        ) || undefined
      const summary = sanitizeText(
        ev.summary ||
          ev.toolPermission?.reason ||
          ev.toolPermission?.command ||
          ev.toolPermission?.toolName ||
          ev.toolName ||
          'Permissão necessária',
      )
      upsertPending({
        id: reqId ?? evId,
        agentId: actor.id,
        kind,
        eventId: evId,
        summary,
        ...(reqId ? { requestId: reqId } : {}),
      })
      actor.pose = 'raise_hand'
      actor.status = 'waiting_user'
      continue
    }

    if (ev.mcpApproval && ev.state !== 'completed' && ev.state !== 'failed') {
      const summary = sanitizeText(
        ev.summary || ev.mcpApproval.display_name || 'Aprovar servidor MCP',
      )
      upsertPending({
        id: evId,
        agentId: actor.id,
        kind: 'mcp',
        eventId: evId,
        summary,
      })
      actor.pose = 'raise_hand'
      actor.status = 'waiting_user'
      continue
    }

    if (ev.pluginApproval && ev.state !== 'completed' && ev.state !== 'failed') {
      const summary = sanitizeText(
        ev.summary || ev.pluginApproval.display_name || 'Aprovar plugin',
      )
      upsertPending({
        id: evId,
        agentId: actor.id,
        kind: 'plugin',
        eventId: evId,
        summary,
      })
      actor.pose = 'raise_hand'
      actor.status = 'waiting_user'
      continue
    }

    if (
      type === 'turn.waiting_user' ||
      (Array.isArray(ev.questions) && ev.questions.length > 0 && ev.questionsAnswered !== true)
    ) {
      if (ev.questionsAnswered === true) {
        pending = pending.filter((p) => !(p.agentId === actor.id && p.kind === 'question'))
      } else {
        const qText = sanitizeText(
          ev.questions?.[0]?.question || ev.summary || 'Aguardando resposta',
        )
        upsertPending({
          id: evId,
          agentId: actor.id,
          kind: 'question',
          eventId: evId,
          summary: qText,
        })
        actor.pose = 'raise_hand'
        actor.status = 'waiting_user'
        if (type === 'turn.waiting_user' && ev.payload?.in_turn !== true) {
          endedWaitingUser = true
        }
      }
      continue
    }

    // Main switch by event type.
    switch (type) {
      case 'turn.started':
      case 'TURN_STARTED': {
        orin.station = 'meeting'
        orin.pose = 'idle'
        orin.status = 'working'
        break
      }

      case 'agent.thought_delta':
      case 'AGENT_THOUGHT_DELTA': {
        const chunk =
          (typeof ev.delta === 'string' && ev.delta) ||
          (typeof ev.payload?.delta === 'string' && ev.payload.delta) ||
          (typeof ev.content === 'string' && ev.content) ||
          (typeof ev.payload?.content === 'string' && ev.payload.content) ||
          (typeof ev.detail === 'string' && ev.detail) ||
          (typeof ev.summary === 'string' && ev.summary) ||
          ''
        actor.thoughtBuffer = appendTail(actor.thoughtBuffer, chunk, THOUGHT_BUFFER_CHARS)
        actor.pose = 'think'
        actor.status = 'thinking'
        const nextEv = turnEvents[i + 1]
        const nextSameThought =
          nextEv &&
          (nextEv.type === 'agent.thought_delta' || nextEv.type === 'AGENT_THOUGHT_DELTA') &&
          nextEv.agentId === ev.agentId
        if (!nextSameThought) {
          const cleanThought = sanitizeText(actor.thoughtBuffer)
          actor.lastThought =
            cleanThought.length > MAX_THOUGHT_CHARS
              ? cleanThought.slice(-MAX_THOUGHT_CHARS)
              : cleanThought
          const bubbleText = truncateTail(cleanThought, MAX_BUBBLE_CHARS, true, true) || '…'
          actor.bubble = {
            kind: 'thought',
            text: bubbleText,
            eventId: evId,
          }
        }
        break
      }

      case 'assistant.delta': {
        const chunk =
          (typeof ev.content === 'string' && ev.content) ||
          (typeof ev.delta === 'string' && ev.delta) ||
          (typeof ev.payload?.delta === 'string' && ev.payload.delta) ||
          (typeof ev.summary === 'string' && ev.summary) ||
          ''
        actor.speechBuffer = appendTail(actor.speechBuffer, chunk, SPEECH_BUFFER_CHARS)
        actor.pose = 'idle'
        actor.status = 'working'
        const nextEv = turnEvents[i + 1]
        const nextSameSpeech =
          nextEv && nextEv.type === 'assistant.delta' && nextEv.agentId === ev.agentId
        if (!nextSameSpeech) {
          const cleanSpeech = sanitizeText(actor.speechBuffer)
          if (cleanSpeech) {
            actor.bubble = {
              kind: 'speech',
              text: truncateTail(cleanSpeech, MAX_BUBBLE_CHARS, false, true),
              eventId: evId,
            }
          }
        }
        break
      }

      case 'tool.requested':
      case 'tool.started':
      case 'tool.progressed': {
        actor.thoughtBuffer = ''
        actor.speechBuffer = ''
        const rawToolName = sanitizeText(
          ev.toolName ||
            ev.tool ||
            (typeof ev.payload?.tool_name === 'string' ? ev.payload.tool_name : ''),
        )
        const effectiveToolName = rawToolName || actor.currentTool || ''
        const rawToolKind = sanitizeText(
          ev.toolKind ||
            (typeof ev.payload?.tool_kind === 'string' ? ev.payload.tool_kind : ''),
        )
        actor.currentTool = effectiveToolName || null

        const taskCandidate = sanitizeText(
          ev.summary || ev.description || ev.title || effectiveToolName,
        )
        if (taskCandidate) {
          actor.task = taskCandidate
        }

        if (isAgentTool(effectiveToolName, rawToolKind)) {
          actor.station = 'meeting'
          actor.pose = 'wait'
          actor.status = 'waiting_agent'
        } else {
          actor.station = stationForTool(effectiveToolName, rawToolKind)
          actor.pose = 'work'
          actor.status = 'working'
        }
        break
      }

      case 'tool.finished':
      case 'tool.completed': {
        const rawToolName = sanitizeText(
          ev.toolName ||
            ev.tool ||
            (typeof ev.payload?.tool_name === 'string' ? ev.payload.tool_name : ''),
        )
        const rawToolKind = sanitizeText(
          ev.toolKind ||
            (typeof ev.payload?.tool_kind === 'string' ? ev.payload.tool_kind : ''),
        )
        if ((rawToolName || rawToolKind) && actor.station === 'lounge') {
          actor.station = stationForTool(rawToolName, rawToolKind)
        }
        const statusStr = sanitizeText(ev.status)
        const isFailure =
          ev.state === 'failed' ||
          (Boolean(statusStr) && statusStr !== 'succeeded' && statusStr !== 'completed')
        actor.currentTool = null

        if (isFailure) {
          pushEffect(i, {
            id: evId,
            kind: 'spark',
            agentId: actor.id,
            station: actor.station,
          })
          actor.pose = 'scared'
          actor.status = 'failed'
          const errText =
            truncateHead(
              sanitizeText(
                ev.summary ||
                  ev.content ||
                  ev.detail ||
                  ev.description ||
                  ev.title ||
                  'Erro na ferramenta',
              ),
              MAX_BUBBLE_CHARS,
            ) || 'Erro na ferramenta'
          actor.bubble = {
            kind: 'error',
            text: errText,
            eventId: evId,
          }
        } else {
          pushEffect(i, {
            id: evId,
            kind: 'check',
            agentId: actor.id,
            station: actor.station,
          })
          actor.pose = 'idle'
          actor.status = 'working'
        }
        break
      }

      case 'tool.failed': {
        const rawToolName = sanitizeText(
          ev.toolName ||
            ev.tool ||
            (typeof ev.payload?.tool_name === 'string' ? ev.payload.tool_name : ''),
        )
        const rawToolKind = sanitizeText(
          ev.toolKind ||
            (typeof ev.payload?.tool_kind === 'string' ? ev.payload.tool_kind : ''),
        )
        if ((rawToolName || rawToolKind) && actor.station === 'lounge') {
          actor.station = stationForTool(rawToolName, rawToolKind)
        }
        actor.currentTool = null
        pushEffect(i, {
          id: evId,
          kind: 'spark',
          agentId: actor.id,
          station: actor.station,
        })
        actor.pose = 'scared'
        actor.status = 'failed'
        const errText =
          truncateHead(
            sanitizeText(
              ev.summary ||
                ev.content ||
                ev.detail ||
                ev.description ||
                ev.title ||
                'Erro na ferramenta',
            ),
            MAX_BUBBLE_CHARS,
          ) || 'Erro na ferramenta'
        actor.bubble = {
          kind: 'error',
          text: errText,
          eventId: evId,
        }
        break
      }

      case 'agent.created':
      case 'delegation.created': {
        const toRaw = sanitizeText(ev.payload?.to_agent_id)
        const childId =
          !actor.isMain ? actor.id : toRaw ? normalizeAgentId(toRaw) : actor.id
        const child = ensureAgent(childId)
        const parentRaw = sanitizeText(ev.parentAgentId)
        const parentId = parentRaw
          ? normalizeAgentId(parentRaw)
          : child.parentId ?? MAIN_AGENT_ID
        const parent = ensureAgent(parentId)

        if (!child.isMain) {
          if (parent.id !== child.id) {
            child.parentId = parent.id
          }
          const candidateName = sanitizeText(ev.agentName)
          if (candidateName) child.name = candidateName
          const subType = sanitizeText(ev.payload?.subagent_type)
          if (subType) child.subagentType = subType
        }

        child.station = 'meeting'
        child.pose = 'idle'
        child.status = 'working'
        if (!child.isMain) openDelegation(parent.id, child.id)

        const label = truncateHead(sanitizeText(ev.content || ev.summary), MAX_BUBBLE_CHARS)
        pushTransfer(i, {
          id: evId,
          kind: 'delegation',
          fromId: parent.id,
          toId: child.id,
          ...(label ? { label } : {}),
        })
        delegatedAgentIds.add(child.id)
        pushEffect(i, {
          id: evId,
          kind: 'poof',
          agentId: child.id,
          station: 'meeting',
        })
        break
      }

      case 'agent.message_sent':
      case 'AGENT_MESSAGE_SENT': {
        const source = sanitizeText(ev.payload?.source)
        const rawTo = sanitizeText(ev.payload?.to_agent_id ?? ev.payload?.toAgentId)
        if (source === 'user') {
          const targetId = rawTo ? normalizeAgentId(rawTo) : actor.id
          const recipient = ensureAgent(targetId)
          if (!recipient.isMain) {
            const recipientName = sanitizeText(
              ev.payload?.recipient_name ?? ev.payload?.recipientName ?? ev.agentName,
            )
            if (recipientName) {
              recipient.name = recipientName
            }
          }
          const rawMsg = sanitizeText(
            ev.content ||
              (typeof ev.payload?.content === 'string' ? ev.payload.content : '') ||
              ev.summary,
          )
          const prefixed = rawMsg
            ? rawMsg.startsWith('Você:')
              ? rawMsg
              : `Você: ${rawMsg}`
            : 'Você: …'
          const text = truncateHead(prefixed, MAX_BUBBLE_CHARS) || 'Você: …'
          recipient.bubble = {
            kind: 'speech',
            text,
            eventId: evId,
          }
          break
        }
        if (rawTo) {
          const toId = normalizeAgentId(rawTo)
          const wasKnown = agentsMap.has(toId)
          const recipient = ensureAgent(toId)
          if (!recipient.isMain) {
            if (!wasKnown && actor.id !== recipient.id) {
              recipient.parentId = actor.id
            }
            const recipientName = sanitizeText(ev.agentName)
            if (recipientName) {
              recipient.name = recipientName
            }
            const subType = sanitizeText(ev.payload?.subagent_type)
            if (subType) {
              recipient.subagentType = subType
            }
            if (!recipient.task) {
              const delegatedTask = sanitizeText(ev.content || ev.summary)
              if (delegatedTask) recipient.task = delegatedTask
            }
          }

          const transferKind: SceneTransferKind = !delegatedAgentIds.has(recipient.id)
            ? 'delegation'
            : 'message'
          delegatedAgentIds.add(recipient.id)
          if (!recipient.isMain) openDelegation(actor.id, recipient.id)

          const label = truncateHead(sanitizeText(ev.content || ev.summary), MAX_BUBBLE_CHARS)
          pushTransfer(i, {
            id: evId,
            kind: transferKind,
            fromId: actor.id,
            toId: recipient.id,
            ...(label ? { label } : {}),
          })

          actor.station = 'meeting'
          actor.pose = 'wait'
          actor.status = 'waiting_agent'
        } else {
          const text =
            truncateHead(sanitizeText(ev.content || ev.summary), MAX_BUBBLE_CHARS) ||
            'Mensagem'
          actor.bubble = {
            kind: 'speech',
            text,
            eventId: evId,
          }
        }
        break
      }

      case 'delegation.waiting': {
        const parentRaw = sanitizeText(ev.parentAgentId)
        if (!actor.isMain && !parentRaw) {
          // A subagent waiting for a free slot: it is the one queued, nobody waits on it yet.
          actor.station = 'meeting'
          actor.pose = 'wait'
          actor.status = 'queued'
          break
        }
        const target = !actor.isMain ? ensureAgent(normalizeAgentId(parentRaw)) : actor
        target.station = 'meeting'
        target.pose = 'wait'
        target.status = 'waiting_agent'
        break
      }

      case 'agent.message_received':
      case 'delegation.completed': {
        const rawFrom = sanitizeText(ev.payload?.from_agent_id)
        const childId =
          !actor.isMain ? actor.id : rawFrom ? normalizeAgentId(rawFrom) : actor.id
        const child = ensureAgent(childId)
        const parentRaw = sanitizeText(ev.parentAgentId)
        const parentId = parentRaw
          ? normalizeAgentId(parentRaw)
          : child.parentId ?? MAIN_AGENT_ID
        const parent = ensureAgent(parentId)

        const label = truncateHead(sanitizeText(ev.content || ev.summary), MAX_BUBBLE_CHARS)
        pushTransfer(i, {
          id: evId,
          kind: 'return',
          fromId: child.id,
          toId: parent.id,
          ...(label ? { label } : {}),
        })

        child.status = 'completed'
        child.pose = 'deliver'
        child.currentTool = null

        if (closeDelegation(parent.id, child.id)) {
          parent.status = 'working'
          parent.pose = 'idle'
        }
        break
      }

      case 'delegation.failed':
      case 'DELEGATION_FAILED': {
        const rawFrom = sanitizeText(ev.payload?.from_agent_id ?? ev.payload?.fromAgentId)
        const childId =
          !actor.isMain ? actor.id : rawFrom ? normalizeAgentId(rawFrom) : actor.id
        const child = ensureAgent(childId)
        const parentRaw = sanitizeText(ev.parentAgentId)
        const parentId = parentRaw
          ? normalizeAgentId(parentRaw)
          : child.parentId ?? MAIN_AGENT_ID
        const parent = ensureAgent(parentId)

        const errCode = sanitizeText(
          ev.errorCode ?? ev.payload?.error_code ?? ev.payload?.errorCode,
        )
        if (errCode === 'STOPPED_BY_USER') {
          child.status = 'stopped'
          child.pose = 'idle'
          child.currentTool = null
          if (child.bubble?.kind === 'error') {
            child.bubble = null
          }
          pending = pending.filter((p) => p.agentId !== child.id)
          if (closeDelegation(parent.id, child.id) && parent.status === 'waiting_agent') {
            parent.status = 'working'
            parent.pose = 'idle'
          }
          break
        }

        const errText =
          truncateHead(
            sanitizeText(ev.summary || ev.content || ev.detail || 'Falha na delegação'),
            MAX_BUBBLE_CHARS,
          ) || 'Falha na delegação'

        pushTransfer(i, {
          id: evId,
          kind: 'failed_delegation',
          fromId: child.id,
          toId: parent.id,
          label: errText,
        })
        pushEffect(i, {
          id: evId,
          kind: 'spark',
          agentId: child.id,
          station: child.station,
        })

        child.status = 'failed'
        child.pose = 'scared'
        child.currentTool = null
        child.bubble = {
          kind: 'error',
          text: errText,
          eventId: evId,
        }

        if (closeDelegation(parent.id, child.id) && parent.status === 'waiting_agent') {
          parent.status = 'working'
          parent.pose = 'idle'
        }
        break
      }

      case 'turn.completed':
      case 'TURN_COMPLETED': {
        endedWaitingUser = false
        pending = []
        for (const ag of agentsMap.values()) {
          ag.station = 'lounge'
          ag.pose = 'sit'
          ag.status = 'resting'
          ag.currentTool = null
        }
        break
      }

      case 'turn.failed':
      case 'TURN_FAILED':
      case 'turn.cancelled':
      case 'TURN_CANCELLED':
      case 'execution.canceled':
      case 'EXECUTION_CANCELED': {
        alarmEventId = evId
        endedWaitingUser = false
        pending = []
        for (const ag of agentsMap.values()) {
          ag.station = 'lounge'
          ag.pose = 'sit'
          ag.status = 'resting'
          ag.currentTool = null
        }
        break
      }

      default:
        break
    }
  }

  // Build ordered agent list: Orin first (depth 0), then delegation tree depth-first (depth = treeDepth + 1).
  const subagents: MutableAgent[] = []
  for (const ag of agentsMap.values()) {
    if (!ag.isMain) {
      subagents.push(ag)
    }
  }

  const orderedSubagents = orderAgentTree(
    subagents,
    (ag) => ag.id,
    (ag) => (ag.parentId === MAIN_AGENT_ID ? null : ag.parentId),
  )

  // A classic question ends the turn, but whoever asked keeps holding the sign until the user answers.
  const waitingQuestions =
    !running && endedWaitingUser ? pending.filter((p) => p.kind === 'question') : []
  const askerIds = new Set(waitingQuestions.map((p) => p.agentId))

  const toSceneAgent = (ag: MutableAgent, depth: number): SceneAgent => {
    if (!running && askerIds.has(ag.id)) {
      return {
        id: ag.id,
        isMain: ag.isMain,
        name: ag.name || (ag.isMain ? 'Orin' : 'Subagente'),
        ...(ag.subagentType ? { subagentType: ag.subagentType } : {}),
        parentId: ag.isMain ? null : ag.parentId ?? MAIN_AGENT_ID,
        depth,
        station: ag.station === 'lounge' ? 'meeting' : ag.station,
        pose: 'raise_hand',
        status: 'waiting_user',
        statusLabel: statusLabelFor('waiting_user'),
        task: ag.task,
        currentTool: null,
        lastThought: ag.lastThought,
        bubble: null,
        activities: ag.activities,
        durationLabel: computeDurationLabel(ag.validTimestamps, ag.totalElapsedMs),
      }
    }
    const finalStatus: SceneAgentStatus = running ? ag.status : 'resting'
    const finalStation: SceneStationId = running ? ag.station : 'lounge'
    const finalPose: ScenePose = running ? ag.pose : 'sit'
    return {
      id: ag.id,
      isMain: ag.isMain,
      name: ag.name || (ag.isMain ? 'Orin' : 'Subagente'),
      ...(ag.subagentType ? { subagentType: ag.subagentType } : {}),
      parentId: ag.isMain ? null : ag.parentId ?? MAIN_AGENT_ID,
      depth,
      station: finalStation,
      pose: finalPose,
      status: finalStatus,
      statusLabel: statusLabelFor(finalStatus),
      task: ag.task,
      currentTool: running ? ag.currentTool : null,
      lastThought: ag.lastThought,
      bubble: running ? ag.bubble : null,
      activities: ag.activities,
      durationLabel: computeDurationLabel(ag.validTimestamps, ag.totalElapsedMs),
    }
  }

  const finalAgents: SceneAgent[] = [
    toSceneAgent(orin, 0),
    ...orderedSubagents.map(({ item, depth }) => toSceneAgent(item, depth + 1)),
  ]

  return {
    turnId,
    live: running,
    waitingUser: waitingQuestions.length > 0,
    // Stable at rest: on whenever the conversation has a turn, whatever page of events is loaded.
    lightsOn: running || turnEvents.length > 0,
    alarmEventId,
    agents: finalAgents,
    transfers: running ? transfers : [],
    effects: running ? effects : [],
    pending: running ? pending : waitingQuestions,
  }
}
