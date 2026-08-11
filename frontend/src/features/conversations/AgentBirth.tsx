import { motion, useReducedMotion } from 'motion/react'
import type { ConversationActivityEvent } from './activityTypes'

type AgentBirthProps = {
  event: ConversationActivityEvent
}

const SPARKS = [0, 60, 120, 180, 240, 300]

/**
 * The moment a subagent comes into existence.
 *
 * This is the one place in the chat that earns a longer, narrative animation: a
 * line drops from the main agent, a core forms at its end, and a short burst
 * settles. It runs once, takes well under a second, and leaves a static row
 * behind so re-reading the conversation is never a light show.
 */
export function AgentBirth({ event }: AgentBirthProps) {
  const reduced = useReducedMotion()
  const name = event.label ?? event.agentName ?? 'Agente'

  if (reduced) {
    return (
      <div className="agent-birth agent-birth--static">
        <span className="agent-birth__core" aria-hidden="true">◇</span>
        <p>Criou o agente <strong>{name}</strong></p>
      </div>
    )
  }

  return (
    <motion.div
      className="agent-birth"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.18 }}
    >
      <motion.span
        className="agent-birth__thread"
        aria-hidden="true"
        initial={{ scaleY: 0 }}
        animate={{ scaleY: 1 }}
        transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      />
      <span className="agent-birth__stage" aria-hidden="true">
        {SPARKS.map((angle, index) => (
          <motion.span
            key={angle}
            className="agent-birth__spark"
            style={{ rotate: `${angle}deg` }}
            initial={{ opacity: 0, scale: 0.2 }}
            animate={{ opacity: [0, 0.9, 0], scale: [0.2, 1.5, 2.1] }}
            transition={{ duration: 0.66, delay: 0.24 + index * 0.02, ease: 'easeOut' }}
          />
        ))}
        <motion.span
          className="agent-birth__halo"
          initial={{ opacity: 0, scale: 0.4 }}
          animate={{ opacity: [0, 0.55, 0.16], scale: [0.4, 1.35, 1] }}
          transition={{ duration: 0.7, delay: 0.22, ease: 'easeOut' }}
        />
        <motion.span
          className="agent-birth__core"
          initial={{ opacity: 0, scale: 0.1, rotate: -140 }}
          animate={{ opacity: 1, scale: [0.1, 1.22, 1], rotate: 0 }}
          transition={{ duration: 0.62, delay: 0.2, ease: [0.34, 1.56, 0.64, 1] }}
        >
          ◇
        </motion.span>
      </span>
      <motion.p
        initial={{ opacity: 0, x: -6 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.28, delay: 0.42 }}
      >
        Criou o agente <strong>{name}</strong>
        {event.summary && event.summary !== `Criou o agente ${name}` && <span className="agent-birth__role">{event.summary}</span>}
      </motion.p>
    </motion.div>
  )
}
