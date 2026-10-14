import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useId, useState } from 'react'
import type { ActivityGroup, ConversationActivityEvent } from './activityTypes'
import { activityStateLabel, formatActivityCount } from './activitySummary'
import { StructuredDiffView, type WorkspaceFilePreviewHandler } from './WorkspaceFileCard'

type ActivityCardProps = {
  group: ActivityGroup
  conversationId?: string
  onPreview?: WorkspaceFilePreviewHandler
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
export function ActivityCard({ group, conversationId, onPreview }: ActivityCardProps) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const reduced = useReducedMotion()
  const firstTool = group.events.find((event) => event.kind === 'tool') ?? group.events[0]
  const glyph = GLYPHS[group.kind === 'tool' ? (firstTool.toolKind ?? 'lifecycle') : group.kind] ?? '◦'
  const running = group.state === 'waiting_tool' || group.state === 'working' || group.state === 'waiting_agent'
  const failedCalls = group.events.filter((event) => event.state === 'failed').length
  const liveStreamEvent = running
    ? [...group.events].reverse().find((event) => Boolean(event.outputTail))
    : undefined

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
        {/* A tool batch's label already states its own count ("N ações") once
            there is more than one real call; the artifact records folded into
            `group.count` would otherwise make this badge repeat, or overstate,
            that same number right next to it. */}
        {group.kind !== 'tool' && group.count > 1 && <span className="activity-card__count">{formatActivityCount(group.count)}</span>}
        {failedCalls > 0 && <span className="activity-card__failure">{formatFailureCount(failedCalls)}</span>}
        {group.agentName && <span className="activity-card__agent">{group.agentName}</span>}
        <span className="activity-card__state">{activityStateLabel(group.state)}</span>
        <span className={`activity-card__chevron${open ? ' is-open' : ''}`} aria-hidden="true">›</span>
      </button>
      {!open && liveStreamEvent?.outputTail && (
        <pre className="activity-detail__terminal-stream" aria-live="polite">{liveStreamEvent.outputTail}</pre>
      )}
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
              {group.events.map((event) => (
                <ActivityDetailRow key={event.eventId} event={event} conversationId={conversationId} onPreview={onPreview} />
              ))}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.article>
  )
}

function formatFailureCount(count: number): string {
  return `${count} ${count === 1 ? 'falha' : 'falhas'}`
}

function ActivityDetailRow({ event, conversationId, onPreview }: { event: ConversationActivityEvent; conversationId?: string; onPreview?: WorkspaceFilePreviewHandler }) {
  // A file this batch produced is still a file: previewing it from the
  // collapsed detail list is more useful than a plain, inert line of text.
  const previewable = event.kind === 'artifact' && event.path && conversationId && onPreview
  const hasStats = event.addedLines !== undefined || event.removedLines !== undefined
  const hasPatch = Boolean(event.structuredPatch && event.structuredPatch.length > 0)
  return (
    <li className="activity-detail" data-state={event.state}>
      {previewable ? (
        <button
          type="button"
          className="activity-detail__summary activity-detail__link"
          onClick={() => onPreview({ conversationId, path: event.path as string })}
        >
          {event.summary || event.type}
        </button>
      ) : (
        <span className="activity-detail__summary">{event.summary || event.type}</span>
      )}
      <span className="activity-detail__meta">
        {event.toolName && <code>{event.toolName}</code>}
        {event.label && event.label !== event.summary && <span className="activity-detail__label">{event.label}</span>}
        {hasStats && (
          <span className="activity-detail__diff-stats" aria-label={`Diff +${event.addedLines ?? 0} -${event.removedLines ?? 0}`}>
            <span className="workspace-diff__stat-add">+{event.addedLines ?? 0}</span>
            {' '}
            <span className="workspace-diff__stat-del">-{event.removedLines ?? 0}</span>
          </span>
        )}
        {event.errorCode && <span className="activity-detail__error">{event.errorCode}</span>}
        {event.occurredAt && <time dateTime={event.occurredAt}>{formatTime(event.occurredAt)}</time>}
      </span>
      {event.content && <p className="activity-detail__content">{event.content}</p>}
      {event.outputTail && (
        <pre className="activity-detail__terminal-stream" aria-live="polite">{event.outputTail}</pre>
      )}
      {hasPatch && event.structuredPatch && (
        <div className="activity-detail__diff">
          <StructuredDiffView hunks={event.structuredPatch} mode="unified" />
        </div>
      )}
    </li>
  )
}

export function formatTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  return parsed.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
