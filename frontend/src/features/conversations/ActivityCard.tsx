import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useId, useState } from 'react'
import type { ActivityGroup, ConversationActivityEvent } from './activityTypes'
import { activityStateLabel } from './activitySummary'

type ActivityCardProps = {
  group: ActivityGroup
}

const GLYPHS: Record<string, string> = {
  filesystem: '◫',
  terminal: '›_',
  web: '◍',
  browser: '◉',
  memory: '◈',
  agent: '◇',
  artifact: '▣',
  lifecycle: '◦',
}

/**
 * One collapsed line of agent activity.
 *
 * Closed, it is a sentence a person can read at a glance. Open, it is the
 * technical record: every underlying event, its status and its identifiers. The
 * chat never shows the second form unless it is asked for.
 */
export function ActivityCard({ group }: ActivityCardProps) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const reduced = useReducedMotion()
  const glyph = GLYPHS[group.kind === 'tool' ? (group.events[0].toolKind ?? 'lifecycle') : group.kind] ?? '◦'
  const running = group.state === 'waiting_tool' || group.state === 'working' || group.state === 'waiting_agent'
  const failedCalls = group.events.filter((event) => event.state === 'failed').length

  return (
    <motion.article
      className="activity-card"
      data-state={group.state}
      data-kind={group.kind}
      // No layout animation: a stream of dozens of rows would re-measure and
      // tween every sibling on each new event, which both costs frames and lets
      // a screenshot or a fast reader catch two rows mid-flight on top of each
      // other. The entrance animation alone carries the arrival.
      initial={reduced ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: [0.22, 0.61, 0.36, 1] }}
    >
      <button
        type="button"
        className="activity-card__trigger"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={`activity-card__glyph${running ? ' is-running' : ''}`} aria-hidden="true">{glyph}</span>
        <span className="activity-card__label">{group.label}</span>
        {group.count > 1 && <span className="activity-card__count">{formatActivityCount(group.count)}</span>}
        {failedCalls > 0 && <span className="activity-card__failure">{formatFailureCount(failedCalls)}</span>}
        {group.agentName && <span className="activity-card__agent">{group.agentName}</span>}
        <span className="activity-card__state">{activityStateLabel(group.state)}</span>
        <span className={`activity-card__chevron${open ? ' is-open' : ''}`} aria-hidden="true">›</span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id={panelId}
            className="activity-card__details"
            role="region"
            aria-label={`Detalhes: ${group.label}`}
            initial={reduced ? false : { height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
          >
            <ul className="activity-card__events">
              {group.events.map((event) => <ActivityDetailRow key={event.eventId} event={event} />)}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.article>
  )
}

function formatActivityCount(count: number): string {
  return `${count} ${count === 1 ? 'ação' : 'ações'}`
}

function formatFailureCount(count: number): string {
  return `${count} ${count === 1 ? 'falha' : 'falhas'}`
}

function ActivityDetailRow({ event }: { event: ConversationActivityEvent }) {
  return (
    <li className="activity-detail" data-state={event.state}>
      <span className="activity-detail__summary">{event.summary || event.type}</span>
      <span className="activity-detail__meta">
        {event.toolName && <code>{event.toolName}</code>}
        {event.label && event.label !== event.summary && <span className="activity-detail__label">{event.label}</span>}
        {event.errorCode && <span className="activity-detail__error">{event.errorCode}</span>}
        {event.occurredAt && <time dateTime={event.occurredAt}>{formatTime(event.occurredAt)}</time>}
      </span>
      {event.content && <p className="activity-detail__content">{event.content}</p>}
    </li>
  )
}

export function formatTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  return parsed.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
