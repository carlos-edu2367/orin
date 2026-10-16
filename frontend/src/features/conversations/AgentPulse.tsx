import { useEffect, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import type { ActivityState } from './activityTypes'
import { activityStateLabel } from './activitySummary'

export type AgentActivityMode =
  | 'idle'
  | 'thinking'
  | 'planning'
  | 'responding'
  | 'reading'
  | 'writing'
  | 'terminal'
  | 'browsing'
  | 'remembering'
  | 'waiting_agent'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type AgentPulseProps = {
  mode: AgentActivityMode
  state?: ActivityState | string
  status?: string
  active?: boolean
  detail?: string
  activeTurnId?: string | null
}

const INACTIVE_PULSE_STATES = new Set([
  'completed',
  'failed',
  'cancelled',
  'canceled',
  'waiting_user',
  'idle',
])

const KNOWN_CONVERSATION_STATES = new Set([
  'queued',
  'starting',
  'running',
  'streaming',
  'cancelling',
  'working',
  'waiting_tool',
  'waiting_agent',
  'waiting_user',
  'retrying',
  'completed',
  'failed',
  'cancelled',
  'canceled',
  'idle',
])

const TERMINAL_COMPLETED_EVENT_TYPES = new Set([
  'turn.completed',
  'TURN_COMPLETED',
  'code_mode.completed',
  'code_mode.completed_with_caveats',
])

const TERMINAL_FAILED_EVENT_TYPES = new Set([
  'turn.failed',
  'TURN_FAILED',
  'code_mode.blocked',
])

const TERMINAL_CANCELLED_EVENT_TYPES = new Set([
  'turn.cancelled',
  'TURN_CANCELLED',
  'execution.canceled',
  'EXECUTION_CANCELED',
])

export const STALE_RESPONDING_THRESHOLD_MS = 2500

export function formatElapsed(elapsedSec: number): string {
  const total = Math.max(0, Math.floor(elapsedSec))
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes}m ${seconds}s`
}

/**
 * The agent's current state, as motion rather than a spinner.
 *
 * Each mode has its own rhythm — a slow breath while thinking, a stepped pulse
 * while a command runs, a travelling dot while waiting on another agent — so the
 * shape of the work is readable from across the room. Every variant collapses to
 * a static mark under reduced motion, while the shimmer label and live elapsed
 * counter keep progress visible even when OS animations are disabled.
 */
export function AgentPulse({
  mode,
  state = 'working',
  status,
  active,
  detail,
  activeTurnId,
}: AgentPulseProps) {
  const reduced = useReducedMotion()
  const isInactive =
    active === false ||
    INACTIVE_PULSE_STATES.has(mode) ||
    Boolean(state && INACTIVE_PULSE_STATES.has(state)) ||
    Boolean(status && INACTIVE_PULSE_STATES.has(status))

  const scopeKey = `${activeTurnId ?? '__default__'}:${isInactive ? '0' : '1'}`
  const [clock, setClock] = useState<{ key: string; sec: number }>(() => ({
    key: scopeKey,
    sec: 0,
  }))
  const elapsedSec = clock.key === scopeKey ? clock.sec : 0

  useEffect(() => {
    if (isInactive) return undefined
    const timer = setInterval(() => {
      setClock((prev) => ({
        key: scopeKey,
        sec: prev.key === scopeKey ? prev.sec + 1 : 1,
      }))
    }, 1000)
    return () => clearInterval(timer)
  }, [isInactive, scopeKey])

  if (isInactive) {
    return null
  }

  const effectiveMode: AgentActivityMode =
    mode === 'responding' && elapsedSec * 1000 >= STALE_RESPONDING_THRESHOLD_MS
      ? 'thinking'
      : mode

  const label = detail ?? modeLabel(effectiveMode, state as ActivityState)
  const isShimmer = effectiveMode === 'thinking' || effectiveMode === 'planning'
  const labelClassName = isShimmer
    ? 'agent-pulse__label agent-pulse__label--shimmer'
    : 'agent-pulse__label'

  return (
    <div
      className="agent-pulse"
      data-mode={effectiveMode}
      data-turn-id={activeTurnId ?? undefined}
      role="status"
      aria-live="polite"
    >
      <span className="agent-pulse__stage" aria-hidden="true">
        {reduced ? <span className="agent-pulse__core" /> : <PulseFigure mode={effectiveMode} />}
      </span>
      <span className={labelClassName}>{label}</span>
      {elapsedSec >= 1 && (
        <span className="agent-pulse__elapsed" aria-hidden="true">
          {formatElapsed(elapsedSec)}
        </span>
      )}
    </div>
  )
}

function PulseFigure({ mode }: { mode: AgentActivityMode }) {
  if (mode === 'reading' || mode === 'writing') {
    return (
      <span className="agent-pulse__lines">
        {[0, 1, 2].map((index) => (
          <motion.span
            key={index}
            className="agent-pulse__line"
            initial={{ scaleX: 0.15, opacity: 0.3 }}
            animate={{ scaleX: mode === 'writing' ? [0.15, 1, 0.15] : [0.2, 0.9, 0.2], opacity: [0.3, 0.95, 0.3] }}
            transition={{ duration: 1.1, repeat: Infinity, delay: index * 0.16, ease: 'easeInOut' }}
          />
        ))}
      </span>
    )
  }
  if (mode === 'terminal') {
    return (
      <span className="agent-pulse__steps">
        {[0, 1, 2, 3].map((index) => (
          <motion.span
            key={index}
            className="agent-pulse__step"
            animate={{ opacity: [0.18, 1, 0.18] }}
            transition={{ duration: 0.9, repeat: Infinity, delay: index * 0.12, ease: 'linear' }}
          />
        ))}
      </span>
    )
  }
  if (mode === 'browsing') {
    return (
      <span className="agent-pulse__scan">
        <motion.span
          className="agent-pulse__scanner"
          animate={{ x: ['-120%', '120%'] }}
          transition={{ duration: 1.5, repeat: Infinity, ease: 'easeInOut' }}
        />
      </span>
    )
  }
  if (mode === 'waiting_agent') {
    return (
      <span className="agent-pulse__link">
        <span className="agent-pulse__node" />
        <motion.span
          className="agent-pulse__travel"
          animate={{ left: ['6%', '82%'], opacity: [0, 1, 0] }}
          transition={{ duration: 1.25, repeat: Infinity, ease: 'easeInOut' }}
        />
        <span className="agent-pulse__node" />
      </span>
    )
  }
  if (mode === 'completed' || mode === 'failed' || mode === 'cancelled') {
    return (
      <motion.span
        className="agent-pulse__core"
        initial={{ scale: 0.6, opacity: 0.4 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.32, ease: 'easeOut' }}
      />
    )
  }
  return (
    <motion.span
      className="agent-pulse__core"
      animate={{ scale: [1, 1.28, 1], opacity: [0.55, 1, 0.55] }}
      transition={{ duration: mode === 'remembering' ? 1.4 : 1.9, repeat: Infinity, ease: 'easeInOut' }}
    />
  )
}

function modeLabel(mode: AgentActivityMode, state: ActivityState): string {
  switch (mode) {
    case 'thinking': return 'Pensando'
    case 'planning': return 'Planejando'
    case 'responding': return 'Respondendo'
    case 'reading': return 'Lendo arquivos'
    case 'writing': return 'Escrevendo arquivos'
    case 'terminal': return 'Executando comandos'
    case 'browsing': return 'Consultando a web'
    case 'remembering': return 'Atualizando memória'
    case 'waiting_agent': return 'Aguardando outro agente'
    default: return activityStateLabel(state)
  }
}

export type PulseEventCandidate = {
  type: string
  toolKind?: string
  toolName?: string
  tool?: string
  title?: string
  state?: string
  turnId?: string
  parentAgentId?: string | null
  occurredAt?: string
  phase?: string
  payload?: Record<string, unknown>
}

function resolveModeArgs(
  secondArg?: string | null,
  thirdArg?: string | number | null,
  fourthArg?: number,
): { conversationState: string; activeTurnId: string | null; nowMs: number } {
  let conversationState = 'running'
  let activeTurnId: string | null = null
  let nowMs = typeof fourthArg === 'number' ? fourthArg : Date.now()

  if (typeof thirdArg === 'number') {
    nowMs = thirdArg
  }

  if (typeof secondArg === 'string' && secondArg.length > 0) {
    if (KNOWN_CONVERSATION_STATES.has(secondArg)) {
      conversationState = secondArg
      if (typeof thirdArg === 'string' && thirdArg.length > 0 && !KNOWN_CONVERSATION_STATES.has(thirdArg)) {
        activeTurnId = thirdArg
      }
    } else {
      activeTurnId = secondArg
      if (typeof thirdArg === 'string' && KNOWN_CONVERSATION_STATES.has(thirdArg)) {
        conversationState = thirdArg
      }
    }
  } else if (typeof thirdArg === 'string' && thirdArg.length > 0) {
    if (KNOWN_CONVERSATION_STATES.has(thirdArg)) {
      conversationState = thirdArg
    } else {
      activeTurnId = thirdArg
    }
  }

  return { conversationState, activeTurnId, nowMs }
}

/** Derive the visual mode from the most recent meaningful event of the active turn. */
export function modeFromEvents(
  events: PulseEventCandidate[],
  conversationStateOrActiveTurnId?: string | null,
  activeTurnIdOrNow?: string | number | null,
  nowMsArg?: number,
): AgentActivityMode {
  const { conversationState, activeTurnId, nowMs } = resolveModeArgs(
    conversationStateOrActiveTurnId,
    activeTurnIdOrNow,
    nowMsArg,
  )

  if (conversationState === 'completed') return 'completed'
  if (conversationState === 'failed') return 'failed'
  if (conversationState === 'cancelled' || conversationState === 'canceled') return 'cancelled'
  if (conversationState === 'idle' || conversationState === 'waiting_user') return 'idle'

  const scopedEvents = activeTurnId
    ? events.filter((event) => event.turnId === activeTurnId)
    : events

  for (let index = scopedEvents.length - 1; index >= 0; index -= 1) {
    const event = scopedEvents[index]
    if (event.type.startsWith('context.')) continue

    if (!event.parentAgentId) {
      if (TERMINAL_COMPLETED_EVENT_TYPES.has(event.type)) return 'completed'
      if (TERMINAL_CANCELLED_EVENT_TYPES.has(event.type) || event.state === 'cancelled') return 'cancelled'
      if (TERMINAL_FAILED_EVENT_TYPES.has(event.type)) return 'failed'
    }

    if (event.type === 'turn.waiting_user' || event.type === 'tool.permission_required' || event.state === 'waiting_user') {
      return 'idle'
    }
    if (event.type === 'agent.thought_delta' || event.type === 'AGENT_THOUGHT_DELTA') {
      return 'thinking'
    }
    if (event.type === 'assistant.delta' || event.type === 'ASSISTANT_DELTA') {
      if (event.occurredAt) {
        const occurredMs = Date.parse(event.occurredAt)
        if (Number.isFinite(occurredMs) && nowMs - occurredMs >= STALE_RESPONDING_THRESHOLD_MS) {
          return 'thinking'
        }
      }
      return 'responding'
    }
    if (event.type === 'agent.message_sent') return 'waiting_agent'
    if (event.type === 'tool.started' || event.type === 'tool.progressed') {
      const phase = event.payload?.phase ?? event.phase
      if (event.type === 'tool.progressed' && phase === 'generating_arguments') {
        return 'thinking'
      }
      if (event.state && INACTIVE_PULSE_STATES.has(event.state)) return 'thinking'
      switch (event.toolKind ?? toolKindOf(event.toolName ?? event.tool)) {
        case 'terminal': return 'terminal'
        case 'web':
        case 'browser': return 'browsing'
        case 'memory': return 'remembering'
        case 'agent': return 'waiting_agent'
        case 'filesystem': {
          const name = event.toolName ?? event.tool
          return name === 'write_file' || name === 'edit_file' || name === 'notebook_edit'
            ? 'writing'
            : 'reading'
        }
        default: return 'thinking'
      }
    }
    if (event.type === 'tool.finished') return 'thinking'
  }
  return 'thinking'
}

function toolKindOf(toolName: string | undefined): string | undefined {
  switch (toolName) {
    case 'read_file':
    case 'write_file':
    case 'edit_file':
    case 'notebook_edit':
    case 'list_files':
    case 'glob_files':
    case 'search_files':
      return 'filesystem'
    case 'run_command':
      return 'terminal'
    case 'fetch_url':
      return 'web'
    case 'remember':
    case 'recall':
      return 'memory'
    case 'create_agent':
    case 'ask_agent':
    case 'spawn_subagent':
      return 'agent'
    default:
      return undefined
  }
}


