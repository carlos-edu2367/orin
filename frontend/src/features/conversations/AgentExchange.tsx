import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useId, useState } from 'react'
import type { ConversationActivityEvent } from './activityTypes'
import { formatTime } from './ActivityCard'

type AgentExchangeProps = {
  event: ConversationActivityEvent
}

/**
 * One message crossing between two agents.
 *
 * Direction is the whole point, so it is carried by the layout and by a particle
 * that travels the connector — not by a label the reader has to parse. The body
 * stays a preview until asked to open.
 */
export function AgentExchange({ event }: AgentExchangeProps) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const reduced = useReducedMotion()
  const outbound = event.type === 'agent.message_sent'
  const name = event.label ?? event.agentName ?? 'Agente'
  const from = outbound ? 'Main' : name
  const to = outbound ? name : 'Main'
  const preview = (event.content ?? '').trim()

  return (
    <motion.article
      className="agent-exchange"
      data-direction={outbound ? 'out' : 'in'}
      initial={reduced ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.24, ease: [0.22, 0.61, 0.36, 1] }}
    >
      <div className="agent-exchange__route" aria-hidden="true">
        <span className="agent-exchange__node">{from === 'Main' ? '◆' : '◇'}</span>
        <span className="agent-exchange__wire">
          {!reduced && (
            <motion.span
              className="agent-exchange__particle"
              initial={{ offsetDistance: '0%', opacity: 0 }}
              animate={{ offsetDistance: '100%', opacity: [0, 1, 0] }}
              transition={{ duration: 0.85, ease: 'easeInOut' }}
            />
          )}
        </span>
        <span className="agent-exchange__node">{to === 'Main' ? '◆' : '◇'}</span>
      </div>
      <div className="agent-exchange__body">
        <button type="button" className="agent-exchange__trigger" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((value) => !value)}>
          {/* Always sender → recipient. Flipping the glyph for an inbound
              message made the line read backwards: "Pesquisador ← Main" says
              Main sent it, which is the opposite of what happened. */}
          <span className="agent-exchange__headline">
            <strong>{from}</strong>
            <span aria-hidden="true">→</span>
            <strong>{to}</strong>
          </span>
          {preview && <span className="agent-exchange__preview">{preview.slice(0, 130)}{preview.length > 130 ? '…' : ''}</span>}
        </button>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              id={panelId}
              className="agent-exchange__detail"
              role="region"
              aria-label={`Mensagem de ${from} para ${to}`}
              initial={reduced ? false : { height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              <p>{preview || 'Sem conteúdo público para esta mensagem.'}</p>
              <dl>
                <div><dt>Origem</dt><dd>{from}</dd></div>
                <div><dt>Destino</dt><dd>{to}</dd></div>
                {event.occurredAt && <div><dt>Horário</dt><dd>{formatTime(event.occurredAt)}</dd></div>}
              </dl>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.article>
  )
}
