import { motion, useReducedMotion } from 'motion/react'
import type { ActivityState } from './activityTypes'
import { activityStateLabel } from './activitySummary'

export type AgentActivityMode =
  | 'idle'
  | 'thinking'
  | 'reading'
  | 'writing'
  | 'terminal'
  | 'browsing'
  | 'remembering'
  | 'waiting_agent'
  | 'completed'
  | 'failed'
  | 'cancelled'

type AgentPulseProps = {
  mode: AgentActivityMode
  state: ActivityState
  detail?: string
}

/**
 * The agent's current state, as motion rather than a spinner.
 *
 * Each mode has its own rhythm — a slow breath while thinking, a stepped pulse
 * while a command runs, a travelling dot while waiting on another agent — so the
 * shape of the work is readable from across the room. Every variant collapses to
 * a static mark under reduced motion.
 */
export function AgentPulse({ mode, state, detail }: AgentPulseProps) {
  const reduced = useReducedMotion()
  const label = detail ?? modeLabel(mode, state)

  return (
    <div className="agent-pulse" data-mode={mode} role="status" aria-live="polite">
      <span className="agent-pulse__stage" aria-hidden="true">
        {reduced ? <span className="agent-pulse__core" /> : <PulseFigure mode={mode} />}
      </span>
      <span className="agent-pulse__label">{label}</span>
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
    case 'reading': return 'Lendo arquivos'
    case 'writing': return 'Escrevendo arquivos'
    case 'terminal': return 'Executando comandos'
    case 'browsing': return 'Consultando a web'
    case 'remembering': return 'Atualizando memória'
    case 'waiting_agent': return 'Aguardando outro agente'
    default: return activityStateLabel(state)
  }
}

/** Derive the visual mode from the most recent meaningful event. */
export function modeFromEvents(events: { type: string; toolKind?: string; toolName?: string; state: string }[], conversationState: string): AgentActivityMode {
  if (conversationState === 'completed') return 'completed'
  if (conversationState === 'failed') return 'failed'
  if (conversationState === 'cancelled') return 'cancelled'
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event.type === 'agent.message_sent') return 'waiting_agent'
    if (event.type === 'tool.started') {
      switch (event.toolKind ?? toolKindOf(event.toolName)) {
        case 'terminal': return 'terminal'
        case 'web': return 'browsing'
        case 'memory': return 'remembering'
        case 'agent': return 'waiting_agent'
        case 'filesystem': return event.toolName === 'write_file' ? 'writing' : 'reading'
        default: return 'thinking'
      }
    }
    if (event.type === 'tool.finished') return 'thinking'
  }
  return 'thinking'
}

function toolKindOf(toolName: string | undefined): string | undefined {
  switch (toolName) {
    case 'read_file': case 'write_file': case 'list_files': return 'filesystem'
    case 'run_command': return 'terminal'
    case 'fetch_url': return 'web'
    case 'remember': case 'recall': return 'memory'
    case 'create_agent': case 'ask_agent': return 'agent'
    default: return undefined
  }
}
