import { useEffect, type RefObject } from 'react'

export type SceneHandle = {
  /** Advance the simulation and draw one frame. */
  frame: (elapsedSeconds: number, deltaSeconds: number) => void
  /** React to a new backing-store size, in CSS pixels. */
  resize: (width: number, height: number, pixelRatio: number) => void
  /** Release GPU resources. Always called exactly once. */
  dispose: () => void
}

export type SceneFactory = (canvas: HTMLCanvasElement) => SceneHandle | null

type Options = {
  /** Skip the render loop entirely, e.g. for prefers-reduced-motion. */
  enabled?: boolean
  /** Upper bound on devicePixelRatio; 2 is plenty for decorative fields. */
  maxPixelRatio?: number
}

/**
 * Own the whole lifetime of a canvas-backed scene: creation, resize, the
 * requestAnimationFrame loop, and disposal.
 *
 * The loop is suspended whenever the document is hidden or the canvas leaves the
 * viewport, so a decorative background never spends GPU while nobody can see it —
 * the single most common way a "subtle" WebGL layer ends up pinning a fan.
 */
export function useCanvasScene(
  canvasRef: RefObject<HTMLCanvasElement | null>,
  factory: SceneFactory,
  { enabled = true, maxPixelRatio = 2 }: Options = {},
): void {
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !enabled) return

    let scene: SceneHandle | null
    try {
      scene = factory(canvas)
    } catch {
      scene = null
    }
    if (!scene) return
    const active = scene

    let frameId = 0
    let running = false
    let visible = document.visibilityState !== 'hidden'
    let intersecting = true
    let last = performance.now()
    let elapsed = 0

    const applySize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, maxPixelRatio)
      const width = canvas.clientWidth || canvas.parentElement?.clientWidth || 1
      const height = canvas.clientHeight || canvas.parentElement?.clientHeight || 1
      active.resize(width, height, ratio)
    }

    const tick = (now: number) => {
      const delta = Math.min((now - last) / 1000, 0.1)
      last = now
      elapsed += delta
      active.frame(elapsed, delta)
      frameId = requestAnimationFrame(tick)
    }

    const start = () => {
      if (running) return
      running = true
      last = performance.now()
      frameId = requestAnimationFrame(tick)
    }

    const stop = () => {
      if (!running) return
      running = false
      cancelAnimationFrame(frameId)
    }

    const sync = () => {
      if (visible && intersecting) start()
      else stop()
    }

    const onVisibility = () => {
      visible = document.visibilityState !== 'hidden'
      sync()
    }

    const observer = typeof IntersectionObserver === 'function'
      ? new IntersectionObserver((entries) => {
        intersecting = entries.at(-1)?.isIntersecting ?? true
        sync()
      })
      : null
    observer?.observe(canvas)

    const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(applySize) : null
    resizeObserver?.observe(canvas.parentElement ?? canvas)

    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('resize', applySize)
    applySize()
    sync()

    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('resize', applySize)
      observer?.disconnect()
      resizeObserver?.disconnect()
      active.dispose()
    }
  }, [canvasRef, factory, enabled, maxPixelRatio])
}
