import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useId, useState } from 'react'
import type { ActivityGroup, ConversationActivityEvent } from './activityTypes'
import { activityStateLabel, formatActivityCount, formatElapsed, splitActionSummary, toolBatchOverview, toolCalls } from './activitySummary'
import type { WorkspaceFilePreviewHandler } from './WorkspaceFileCard'

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

  const isTool = group.kind === 'tool'
  const elapsed = useElapsedSeconds(isTool && running, group.events[0]?.occurredAt)
  const title = isTool && !running ? toolBatchOverview(group) : group.label

  return (
    <motion.article
      className={`activity-card${isTool ? ' activity-card--tool' : ''}`}
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
        {isTool && running ? <ActivityDots /> : <span className={`activity-card__glyph${running ? ' is-running' : ''}`} aria-hidden="true">{glyph}</span>}
        <span className="activity-card__label">{title}</span>
        {/* A tool batch's title already states its own count ("executou 4
            comandos"); the artifact records folded into `group.count` would
            otherwise make a badge repeat, or overstate, that same number. */}
        {!isTool && group.count > 1 && <span className="activity-card__count">{formatActivityCount(group.count)}</span>}
        {failedCalls > 0 && <span className="activity-card__failure">{formatFailureCount(failedCalls)}</span>}
        {group.agentName && <span className="activity-card__agent">{group.agentName}</span>}
        {isTool && running && elapsed !== null && <span className="activity-card__elapsed">{formatElapsed(elapsed)}</span>}
        {!isTool && <span className="activity-card__state">{activityStateLabel(group.state)}</span>}
        <span className={`activity-card__chevron${open ? ' is-open' : ''}`} aria-hidden="true">{isTool ? '⌄' : '›'}</span>
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
              {(isTool ? toolCalls(group) : group.events).map((event) => (
                <ActivityDetailRow key={event.eventId} event={event} conversationId={conversationId} onPreview={onPreview} folded={isTool ? artifactsFor(group, event) : []} />
              ))}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.article>
  )
}

/** The `artifact.created` records that directly follow a call: files that call produced. */
function artifactsFor(group: ActivityGroup, call: ConversationActivityEvent): ConversationActivityEvent[] {
  const at = group.events.indexOf(call)
  const produced: ConversationActivityEvent[] = []
  for (let index = at + 1; index < group.events.length && group.events[index].kind === 'artifact'; index += 1) produced.push(group.events[index])
  return produced
}

/** Whole seconds since `startIso`, ticking once a second only while `active`. */
function useElapsedSeconds(active: boolean, startIso?: string): number | null {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  if (!startIso) return null
  const start = new Date(startIso).getTime()
  if (Number.isNaN(start)) return null
  return (now - start) / 1000
}

/** The four-dot mark that stands in for a spinner while a batch is running. */
function ActivityDots() {
  return (
    <span className="activity-dots" aria-hidden="true">
      <i /><i /><i /><i />
    </span>
  )
}

function formatFailureCount(count: number): string {
  return `${count} ${count === 1 ? 'falha' : 'falhas'}`
}

function ActivityDetailRow({ event, conversationId, onPreview, folded = [] }: { event: ConversationActivityEvent; conversationId?: string; onPreview?: WorkspaceFilePreviewHandler; folded?: ConversationActivityEvent[] }) {
  // A file this batch produced is still a file: previewing it from the
  // collapsed detail list is more useful than a plain, inert line of text.
  const previewable = event.kind === 'artifact' && event.path && conversationId && onPreview
  const { verb, target } = event.kind === 'tool' ? splitActionSummary(event.summary || event.type) : { verb: '', target: event.summary || event.type }
  const failed = event.state === 'failed'
  const summary = (
    <>
      {verb && <span className="activity-detail__verb">{verb} </span>}
      <span className="activity-detail__target">{target}</span>
    </>
  )
  return (
    <li className="activity-detail" data-state={event.state}>
      <div className="activity-detail__line">
        {previewable ? (
          <button
            type="button"
            className="activity-detail__summary activity-detail__link"
            onClick={() => onPreview({ conversationId, path: event.path as string })}
          >
            {event.summary || event.type}
          </button>
        ) : (
          <span className="activity-detail__summary">{summary}</span>
        )}
        {failed && <span className="activity-detail__badge">Falhou</span>}
        {event.state === 'waiting_tool' && <span className="activity-detail__badge is-running">Em execução</span>}
      </div>
      <span className="activity-detail__meta">
        {event.toolName && <code>{event.toolName}</code>}
        {event.label && event.label !== event.summary && <span className="activity-detail__label">{event.label}</span>}
        {event.errorCode && <span className="activity-detail__error">{event.errorCode}</span>}
        {event.occurredAt && <time dateTime={event.occurredAt}>{formatTime(event.occurredAt)}</time>}
      </span>
      {event.content && <p className="activity-detail__content">{event.content}</p>}
      {folded.map((file) => (
        <ActivityDetailRow key={file.eventId} event={file} conversationId={conversationId} onPreview={onPreview} />
      ))}
    </li>
  )
}

export function formatTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  return parsed.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
