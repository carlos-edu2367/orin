import { useCallback, useEffect, useRef, useState } from 'react'
import { useCanvasScene, type SceneHandle } from './useCanvasScene'
import { canAnimateScene } from './webgl'
// Type-only: the runtime module is loaded lazily inside the factory, so this
// import contributes nothing to the bundle.
import type * as THREE from 'three'

type AmbientFieldProps = {
  /** Raises brightness and drift while a turn is running. */
  active?: boolean
  density?: number
}

const BASE_PARTICLES = 260
// Points stay outside this radius so the centre of the frame, where the headline
// and composer live, is left clean.
const CLEAR_RADIUS = 6.5

/**
 * The Home backdrop: a slow drift of points bound by faint links, reacting to the
 * cursor with a shallow parallax.
 *
 * Three.js is loaded lazily and only when it can actually be used, so a
 * reduced-motion visitor or a machine without WebGL pays nothing for it — the CSS
 * gradient underneath is the design in that case, not a degraded placeholder.
 */
export function AmbientField({ active = false, density = 1 }: AmbientFieldProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const pointer = useRef({ x: 0, y: 0 })
  const activeRef = useRef(active)
  // Capability does not change while the page is open, so it is resolved once
  // at mount instead of through a post-render state update.
  const [enabled] = useState(canAnimateScene)

  useEffect(() => { activeRef.current = active }, [active])

  useEffect(() => {
    if (!enabled) return
    const onPointerMove = (event: PointerEvent) => {
      pointer.current = {
        x: (event.clientX / window.innerWidth) * 2 - 1,
        y: (event.clientY / window.innerHeight) * 2 - 1,
      }
    }
    window.addEventListener('pointermove', onPointerMove, { passive: true })
    return () => window.removeEventListener('pointermove', onPointerMove)
  }, [enabled])

  const factory = useCallback((canvas: HTMLCanvasElement): SceneHandle | null => {
    let disposed = false
    let handle: SceneHandle | null = null
    const proxy: SceneHandle = {
      frame: (elapsed, delta) => handle?.frame(elapsed, delta),
      resize: (width, height, ratio) => handle?.resize(width, height, ratio),
      dispose: () => { disposed = true; handle?.dispose() },
    }
    let pendingSize: [number, number, number] | null = null
    const originalResize = proxy.resize
    proxy.resize = (width, height, ratio) => {
      pendingSize = [width, height, ratio]
      originalResize(width, height, ratio)
    }

    void import('three').then((three) => {
      if (disposed) return
      const renderer = new three.WebGLRenderer({ canvas, alpha: true, antialias: false, powerPreference: 'low-power' })
      renderer.setClearColor(0x000000, 0)
      const scene = new three.Scene()
      const camera = new three.PerspectiveCamera(58, 1, 0.1, 100)
      camera.position.set(0, 0, 12)

      const count = Math.max(90, Math.round(BASE_PARTICLES * density))
      const positions = new Float32Array(count * 3)
      const drift = new Float32Array(count * 3)
      for (let index = 0; index < count; index += 1) {
        // Points are pushed out of the middle of the frame: that is where the
        // headline and the composer sit, and a field drifting behind live text
        // fights it for attention no matter how dim it is.
        const angle = Math.random() * Math.PI * 2
        const radius = CLEAR_RADIUS + Math.random() * 9
        positions[index * 3] = Math.cos(angle) * radius * 1.35
        positions[index * 3 + 1] = Math.sin(angle) * radius * 0.72
        positions[index * 3 + 2] = (Math.random() - 0.5) * 10
        drift[index * 3] = (Math.random() - 0.5) * 0.05
        drift[index * 3 + 1] = (Math.random() - 0.5) * 0.04
        drift[index * 3 + 2] = (Math.random() - 0.5) * 0.03
      }
      const geometry = new three.BufferGeometry()
      geometry.setAttribute('position', new three.BufferAttribute(positions, 3))
      const material = new three.PointsMaterial({
        color: 0xc8ff6a, size: 0.05, transparent: true, opacity: 0.42,
        blending: three.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
      })
      const points = new three.Points(geometry, material)
      scene.add(points)

      // A second, dimmer shell of larger points reads as depth without another
      // draw call's worth of geometry detail.
      const haloGeometry = geometry.clone()
      const haloMaterial = new three.PointsMaterial({
        color: 0xf4f0e9, size: 0.018, transparent: true, opacity: 0.16,
        depthWrite: false, sizeAttenuation: true,
      })
      const halo = new three.Points(haloGeometry, haloMaterial)
      halo.scale.setScalar(1.35)
      scene.add(halo)


      handle = {
        frame: (elapsed) => {
          const intensity = activeRef.current ? 1 : 0.42
          const attribute = geometry.getAttribute('position') as THREE.BufferAttribute
          const array = attribute.array as Float32Array
          for (let index = 0; index < count; index += 1) {
            const offset = index * 3
            array[offset] += drift[offset] * intensity * 0.35
            array[offset + 1] += drift[offset + 1] * intensity * 0.35
            if (array[offset] > 13 || array[offset] < -13) drift[offset] *= -1
            if (array[offset + 1] > 8 || array[offset + 1] < -8) drift[offset + 1] *= -1
          }
          attribute.needsUpdate = true
          material.opacity = 0.26 + intensity * 0.2
          points.rotation.y = elapsed * 0.012
          halo.rotation.y = -elapsed * 0.008
          camera.position.x += (pointer.current.x * 1.15 - camera.position.x) * 0.035
          camera.position.y += (-pointer.current.y * 0.7 - camera.position.y) * 0.035
          camera.lookAt(0, 0, 0)
          renderer.render(scene, camera)
        },
        resize: (width, height, ratio) => {
          renderer.setPixelRatio(ratio)
          renderer.setSize(width, height, false)
          camera.aspect = width / Math.max(height, 1)
          camera.updateProjectionMatrix()
        },
        dispose: () => {
          geometry.dispose()
          haloGeometry.dispose()
          material.dispose()
          haloMaterial.dispose()
          renderer.dispose()
        },
      }
      if (pendingSize) handle.resize(...pendingSize)
    })

    return proxy
  }, [density])

  useCanvasScene(canvasRef, factory, { enabled })

  if (!enabled) return null
  return <canvas ref={canvasRef} className="ambient-field" aria-hidden="true" />
}
