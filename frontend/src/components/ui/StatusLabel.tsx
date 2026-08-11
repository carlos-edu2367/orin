import { motion, useReducedMotion } from 'motion/react'
import type { ReactNode } from 'react'

type StatusTone = 'active' | 'waiting' | 'success' | 'danger' | 'muted'

type StatusLabelProps = {
  children: ReactNode
  tone?: StatusTone
  className?: string
}

const toneClass: Record<StatusTone, string> = {
  active: 'status-label--active',
  waiting: 'status-label--waiting',
  success: 'status-label--success',
  danger: 'status-label--danger',
  muted: 'status-label--muted',
}

export function StatusLabel({ children, tone = 'muted', className = '' }: StatusLabelProps) {
  const reducedMotion = useReducedMotion()

  return (
    <motion.span
      className={`status-label ${toneClass[tone]} ${className}`}
      role="status"
      initial={reducedMotion ? false : { opacity: 0.7 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2 }}
    >
      <span className="status-label__dot" aria-hidden="true" />
      {children}
    </motion.span>
  )
}
