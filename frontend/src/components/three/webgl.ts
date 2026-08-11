/**
 * Whether a decorative WebGL layer should run at all.
 *
 * The `WebGLRenderingContext` check comes first on purpose: calling `getContext`
 * in a jsdom test environment raises a "not implemented" error through the
 * virtual console, so probing capability that way turns every component test
 * into a wall of noise for a canvas that was never going to render.
 */
export function canRenderWebGL(): boolean {
  if (typeof window === 'undefined' || typeof document === 'undefined') return false
  if (typeof window.WebGLRenderingContext === 'undefined') return false
  try {
    const canvas = document.createElement('canvas')
    return Boolean(canvas.getContext('webgl2') ?? canvas.getContext('webgl'))
  } catch {
    return false
  }
}

export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** Decorative scenes run only when motion is welcome and the GPU path exists. */
export function canAnimateScene(): boolean {
  return !prefersReducedMotion() && canRenderWebGL()
}
