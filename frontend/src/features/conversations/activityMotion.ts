export const activityMotionTiming = {
  enterMs: 220,
  layoutMs: 180,
  pulseMs: 240,
} as const

export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function activityMotionProps(reducedMotion: boolean) {
  return reducedMotion
    ? { initial: false, animate: { opacity: 1 }, transition: { duration: 0 } }
    : { initial: { opacity: 0, y: 6 }, animate: { opacity: 1, y: 0 }, transition: { duration: activityMotionTiming.enterMs / 1000 } }
}
