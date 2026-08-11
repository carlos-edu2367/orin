import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useCanvasScene, type SceneHandle } from '../../components/three/useCanvasScene'
import { canAnimateScene } from '../../components/three/webgl'
// Type-only: the runtime module is loaded lazily inside the factory, so this
// import contributes nothing to the bundle.
import type * as THREE from 'three'
import type { AgentEdge, AgentNode } from '../conversations/activityTypes'

export type OrbitalSceneProps = {
  nodes: AgentNode[]
  edges: AgentEdge[]
  onSelect?: (agentId: string | null) => void
  selectedAgentId?: string | null
}

const STATE_COLORS: Record<string, number> = {
  working: 0xc8ff6a,
  waiting_agent: 0x8fd0ff,
  waiting_tool: 0xffd166,
  completed: 0x9caa8f,
  failed: 0xff8a7a,
  cancelled: 0x7d8a72,
  idle: 0x6f7a68,
}

/**
 * The execution as a small orbital system: the main agent at the centre, its
 * subagents in orbit, and a pulse travelling the link each time work crosses it.
 *
 * The scene is decorative on top of an authoritative list; every node and edge is
 * also rendered as text beside it, so a browser without WebGL, or a visitor who
 * asked for reduced motion, still gets the whole picture.
 */
export function OrbitalScene({ nodes, edges, onSelect, selectedAgentId }: OrbitalSceneProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [canAnimate] = useState(canAnimateScene)
  const enabled = canAnimate && nodes.length > 0
  const dataRef = useRef({ nodes, edges, selectedAgentId })

  useEffect(() => { dataRef.current = { nodes, edges, selectedAgentId } }, [nodes, edges, selectedAgentId])

  const signature = useMemo(() => nodes.map((node) => `${node.agentId}:${node.state}`).join('|'), [nodes])

  const factory = useCallback((canvas: HTMLCanvasElement): SceneHandle | null => {
    // The scene reads live data through `dataRef`, so `signature` is what tells
    // this factory to rebuild when the roster or an agent state changes. It is
    // read here to make that dependency real rather than merely declared.
    const builtFor = signature
    let disposed = false
    let handle: SceneHandle | null = null
    let pendingSize: [number, number, number] | null = null
    const proxy: SceneHandle = {
      frame: (elapsed, delta) => handle?.frame(elapsed, delta),
      resize: (width, height, ratio) => {
        pendingSize = [width, height, ratio]
        handle?.resize(width, height, ratio)
      },
      dispose: () => { disposed = true; handle?.dispose() },
    }

    void import('three').then((three) => {
      if (disposed || builtFor !== signature) return
      const renderer = new three.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'low-power' })
      renderer.setClearColor(0x000000, 0)
      const scene = new three.Scene()
      const camera = new three.PerspectiveCamera(46, 1, 0.1, 100)
      // A lone main agent should fill the frame; a wide roster needs the room.
      const roster = Math.max(dataRef.current.nodes.length, 1)
      camera.position.set(0, roster > 1 ? 1.6 : 0.4, roster > 1 ? 8.2 : 4.2)
      camera.lookAt(0, 0, 0)

      const disposables: Array<{ dispose: () => void }> = []
      const track = <T extends { dispose: () => void }>(value: T): T => { disposables.push(value); return value }

      const group = new three.Group()
      scene.add(group)

      const { nodes: currentNodes, edges: currentEdges } = dataRef.current
      const root = currentNodes.find((node) => !node.parentAgentId) ?? currentNodes[0]
      const children = currentNodes.filter((node) => node.agentId !== root?.agentId)
      const layout = new Map<string, THREE.Vector3>()
      if (root) layout.set(root.agentId, new three.Vector3(0, 0, 0))
      children.forEach((node, index) => {
        const angle = (index / Math.max(children.length, 1)) * Math.PI * 2
        const radius = 3.1
        layout.set(node.agentId, new three.Vector3(Math.cos(angle) * radius, Math.sin(angle) * 1.15, Math.sin(angle) * radius * 0.4))
      })

      // One faint ellipse: the orbit the children sit on, or — when the main
      // agent is working alone — a horizon that keeps the frame from reading as
      // an empty box.
      const ringRadius = children.length > 0 ? 3.02 : 1.32
      const ringGeometry = track(new three.RingGeometry(ringRadius, ringRadius + 0.04, 96))
      const ringMaterial = track(new three.MeshBasicMaterial({ color: 0xc8ff6a, transparent: true, opacity: children.length > 0 ? 0.11 : 0.16, side: three.DoubleSide }))
      const ring = new three.Mesh(ringGeometry, ringMaterial)
      ring.rotation.x = Math.PI / 2.35
      ring.scale.set(1, 0.42, 1)
      group.add(ring)

      const nodeMeshes: Array<{ agentId: string; mesh: THREE.Mesh; halo: THREE.Mesh; state: string }> = []
      for (const node of currentNodes) {
        const position = layout.get(node.agentId)
        if (!position) continue
        const isRoot = node.agentId === root?.agentId
        const geometry = track(isRoot ? new three.IcosahedronGeometry(0.52, 1) : new three.OctahedronGeometry(0.3, 0))
        const color = STATE_COLORS[node.state] ?? STATE_COLORS.idle
        const material = track(new three.MeshBasicMaterial({ color, wireframe: true, transparent: true, opacity: 0.95 }))
        const mesh = new three.Mesh(geometry, material)
        mesh.position.copy(position)
        mesh.userData.agentId = node.agentId
        group.add(mesh)

        const haloGeometry = track(new three.SphereGeometry(isRoot ? 0.78 : 0.46, 16, 12))
        const haloMaterial = track(new three.MeshBasicMaterial({ color, transparent: true, opacity: 0.07, depthWrite: false }))
        const halo = new three.Mesh(haloGeometry, haloMaterial)
        halo.position.copy(position)
        group.add(halo)
        nodeMeshes.push({ agentId: node.agentId, mesh, halo, state: node.state })
      }

      const pulses: Array<{ from: THREE.Vector3; to: THREE.Vector3; mesh: THREE.Mesh; offset: number }> = []
      for (const edge of currentEdges) {
        const from = layout.get(edge.from)
        const to = layout.get(edge.to)
        if (!from || !to) continue
        const lineGeometry = track(new three.BufferGeometry().setFromPoints([from, to]))
        const lineMaterial = track(new three.LineBasicMaterial({ color: 0xc8ff6a, transparent: true, opacity: edge.fact === 'message' ? 0.3 : 0.16 }))
        group.add(new three.Line(lineGeometry, lineMaterial))
        const pulseGeometry = track(new three.SphereGeometry(0.062, 10, 8))
        const pulseMaterial = track(new three.MeshBasicMaterial({ color: 0xd4ff8b, transparent: true, opacity: 0.9 }))
        const pulse = new three.Mesh(pulseGeometry, pulseMaterial)
        group.add(pulse)
        pulses.push({ from, to, mesh: pulse, offset: pulses.length * 0.27 })
      }

      const raycaster = new three.Raycaster()
      const pointerVector = new three.Vector2()
      const onClick = (event: MouseEvent) => {
        const rect = canvas.getBoundingClientRect()
        pointerVector.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
        pointerVector.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
        raycaster.setFromCamera(pointerVector, camera)
        const hit = raycaster.intersectObjects(nodeMeshes.map((item) => item.mesh))[0]
        onSelect?.(hit ? String(hit.object.userData.agentId) : null)
      }
      canvas.addEventListener('click', onClick)

      handle = {
        frame: (elapsed) => {
          const selected = dataRef.current.selectedAgentId
          group.rotation.y = Math.sin(elapsed * 0.11) * 0.16
          for (const item of nodeMeshes) {
            const busy = item.state === 'working' || item.state === 'waiting_tool' || item.state === 'waiting_agent'
            item.mesh.rotation.x = elapsed * (busy ? 0.55 : 0.14)
            item.mesh.rotation.y = elapsed * (busy ? 0.7 : 0.18)
            const beat = busy ? 1 + Math.sin(elapsed * 3.1) * 0.09 : 1
            item.mesh.scale.setScalar(beat * (item.agentId === selected ? 1.25 : 1))
            const haloMaterial = item.halo.material as THREE.MeshBasicMaterial
            haloMaterial.opacity = (busy ? 0.1 + Math.sin(elapsed * 2.2) * 0.05 : 0.05) + (item.agentId === selected ? 0.1 : 0)
          }
          for (const pulse of pulses) {
            const progress = ((elapsed * 0.42 + pulse.offset) % 1)
            pulse.mesh.position.lerpVectors(pulse.from, pulse.to, progress)
            ;(pulse.mesh.material as THREE.MeshBasicMaterial).opacity = Math.sin(progress * Math.PI) * 0.85
          }
          renderer.render(scene, camera)
        },
        resize: (width, height, ratio) => {
          renderer.setPixelRatio(ratio)
          renderer.setSize(width, height, false)
          camera.aspect = width / Math.max(height, 1)
          camera.updateProjectionMatrix()
        },
        dispose: () => {
          canvas.removeEventListener('click', onClick)
          disposables.forEach((item) => item.dispose())
          renderer.dispose()
        },
      }
      if (pendingSize) handle.resize(...pendingSize)
    })

    return proxy
  }, [signature, onSelect])

  useCanvasScene(canvasRef, factory, { enabled, maxPixelRatio: 2 })

  if (!enabled) return null
  return <canvas ref={canvasRef} className="orbital-scene__canvas" aria-hidden="true" />
}
