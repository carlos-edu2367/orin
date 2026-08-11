import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import type { Activity } from './activityTypes'

/**
 * Read directly instead of via `useReducedMotion` from `motion/react`: that hook
 * memoizes the preference once per process (see framer-motion's
 * `hasReducedMotionListener` singleton), so it cannot be reconfigured per test or
 * respond to a preference that changes after the first mount in the app lifetime.
 * The pulse only needs a point-in-time check when a new event arrives.
 */
function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

const categoryLabel: Record<Activity['category'], string> = {
  lifecycle: 'Transições da execução',
  tool: 'Ferramenta',
  delegation: 'Colaboração entre agents',
  resource: 'Efeito em recurso',
}

type ActivityGroupProps = {
  activity: Activity
  summary: string
  children?: ReactNode
}

/**
 * A compact, expandable audit row for one semantic Activity. Level 1 shows only
 * the category and the observed count; level 2 (on expansion) reveals the caller's
 * sanitized summary/detail. The glyph mirrors the agent core/ring motif so a Tool
 * activity visually reads as "this agent did something", not as a log line.
 */
export function ActivityGroup({ activity, summary, children }: ActivityGroupProps) {
  const [open, setOpen] = useState(false)
  const [pulsing, setPulsing] = useState(false)
  const contentId = useId()
  const mountedRef = useRef(false)
  const lastCountRef = useRef(activity.eventIds.length)

  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true
      lastCountRef.current = activity.eventIds.length
      return
    }
    const grew = activity.eventIds.length > lastCountRef.current
    lastCountRef.current = activity.eventIds.length
    if (!grew || prefersReducedMotion()) return

    setPulsing(true)
    const timeout = window.setTimeout(() => setPulsing(false), 420)
    return () => window.clearTimeout(timeout)
  }, [activity.eventIds.length])

  const count = activity.eventIds.length

  return (
    <div className={`activity-group${pulsing ? ' activity-group--pulse' : ''}`} data-category={activity.category}>
      <button
        type="button"
        className="activity-group__trigger"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="activity-group__glyph" aria-hidden="true" />
        <span className="activity-group__label">{categoryLabel[activity.category]}</span>
        <span className="activity-group__count">{count} {count === 1 ? 'ação observada' : 'ações observadas'}</span>
        <svg aria-hidden="true" viewBox="0 0 16 16" className={`activity-group__chevron${open ? ' is-open' : ''}`}>
          <path d="m4 6 4 4 4-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
        </svg>
      </button>
      {open && (
        <div id={contentId} className="activity-group__content">
          <p className="activity-group__summary">{summary}</p>
          {children}
        </div>
      )}
    </div>
  )
}
