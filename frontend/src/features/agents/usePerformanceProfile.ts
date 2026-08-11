import { useEffect, useRef, useState, type RefObject } from 'react'

export type PerformanceProfile = 'full' | 'reduced' | 'static'

/**
 * A devicePixelRatio above 3 is treated as a signal of a high-density phone panel
 * rather than a desktop/Retina display (typical desktop/laptop Retina displays sit
 * at 2; values above 3 are seen mostly on phones), which combined with a typically
 * weaker mobile GPU makes "full" instancing/particle cost a poor default. Documented
 * decision (IMPLEMENTATION_PLAN.md, Fase 5 "Decisões locais").
 */
const MAX_FULL_DEVICE_PIXEL_RATIO = 3

/**
 * Pure decision table for the R3F scene's fidelity. `reducedMotion` always wins
 * (MOTION_SYSTEM.md "Acessibilidade e fallback": remove partículas/pulsos/layout
 * morph, preserve text). Otherwise an invisible canvas (hidden tab or out of
 * viewport) downgrades to "reduced" so a paused/frozen scene never spends GPU while
 * nobody can see it; a visible canvas on a likely low-power device also downgrades.
 */
export function getPerformanceProfile(input: {
  reducedMotion: boolean
  visible: boolean
  devicePixelRatio: number
}): PerformanceProfile {
  if (input.reducedMotion) return 'static'
  if (!input.visible) return 'reduced'
  if (input.devicePixelRatio > MAX_FULL_DEVICE_PIXEL_RATIO) return 'reduced'
  return 'full'
}

function readReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function readDevicePixelRatio(): number {
  if (typeof window === 'undefined' || typeof window.devicePixelRatio !== 'number') return 1
  return window.devicePixelRatio
}

function readTabVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState !== 'hidden'
}

/**
 * Live performance profile for a canvas target: reduced-motion preference, document
 * tab visibility and the target element's own viewport intersection. Recomputed on
 * every signal change (no polling) so `OrchestrationScene` can pause/resume exactly
 * per FRONTEND_ARCHITECTURE.md "Performance" (pausar canvas em aba oculta, viewport
 * fora da tela e reduced motion).
 */
export function usePerformanceProfile(targetRef: RefObject<Element | null>): PerformanceProfile {
  const inViewportRef = useRef(false)
  const [profile, setProfile] = useState<PerformanceProfile>('reduced')

  useEffect(() => {
    function recompute(): void {
      setProfile(
        getPerformanceProfile({
          reducedMotion: readReducedMotion(),
          visible: readTabVisible() && inViewportRef.current,
          devicePixelRatio: readDevicePixelRatio(),
        }),
      )
    }

    const media = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)')
      : null
    media?.addEventListener('change', recompute)
    document.addEventListener('visibilitychange', recompute)

    let observer: IntersectionObserver | undefined
    const element = targetRef.current
    if (element && typeof IntersectionObserver === 'function') {
      observer = new IntersectionObserver((entries) => {
        inViewportRef.current = entries.at(-1)?.isIntersecting ?? false
        recompute()
      })
      observer.observe(element)
    } else {
      // No observer support, or the target has not mounted yet: without a signal
      // either way, default to visible so a capable browser is not stuck at
      // "reduced" forever. A real target's own IntersectionObserver, once mounted,
      // is authoritative from then on.
      inViewportRef.current = true
    }

    recompute()

    return () => {
      media?.removeEventListener('change', recompute)
      document.removeEventListener('visibilitychange', recompute)
      observer?.disconnect()
    }
  }, [targetRef])

  return profile
}
