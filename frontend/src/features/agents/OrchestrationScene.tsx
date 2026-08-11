import { Component, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Instance, Instances, Line } from '@react-three/drei'
import * as THREE from 'three'
import type { AgentEdge, AgentFact, AgentGraph, AgentNode } from './agentGraphProjection'
import { agentMotionTiming, agentVisualStateLabel, deriveAgentAccent, diffNewlyObservedEdges, prefersReducedMotion } from './agentMotion'
import { RAIL_NODE_LIMIT } from './AgentRail'
import { usePerformanceProfile } from './usePerformanceProfile'

type OrchestrationSceneProps = {
  /** The exact `AgentGraph` already projected for the 2D rail — never raw events. */
  graph: AgentGraph
}

/**
 * A second, 3D rendering of the same `AgentGraph` the rail already validated
 * (AGENT_VISUAL_LANGUAGE.md "Grafo": no relation is drawn here that the rail did
 * not already draw). Never mounts without at least one observed collaboration
 * edge, is lazy by nature of only appearing on deliberate expansion, and degrades
 * to the identical textual list — never a blank crash — whenever the performance
 * profile is not "full" or the WebGL canvas itself fails to mount.
 */
export function OrchestrationScene({ graph }: OrchestrationSceneProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const profile = usePerformanceProfile(containerRef)

  if (graph.edges.length === 0) return null

  const visibleNodes = graph.nodes.slice(0, RAIL_NODE_LIMIT)
  const overflowCount = graph.nodes.length - visibleNodes.length
  const visibleIds = new Set(visibleNodes.map((node) => node.agentId))
  const visibleEdges = graph.edges.filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to))

  return (
    <div className="orchestration-scene" ref={containerRef} role="group" aria-label="Cena 3D da orquestração observada">
      {profile === 'full' ? (
        <SceneErrorBoundary fallback={<SceneTextFallback nodes={visibleNodes} edges={visibleEdges} overflowCount={overflowCount} />}>
          <Suspense fallback={<SceneTextFallback nodes={visibleNodes} edges={visibleEdges} overflowCount={overflowCount} />}>
            <SceneCanvas nodes={visibleNodes} edges={visibleEdges} />
          </Suspense>
        </SceneErrorBoundary>
      ) : (
        <SceneTextFallback nodes={visibleNodes} edges={visibleEdges} overflowCount={overflowCount} />
      )}
    </div>
  )
}

function SceneCanvas({ nodes, edges }: { nodes: AgentNode[]; edges: AgentEdge[] }) {
  return (
    <Canvas
      frameloop="demand"
      dpr={[1, 2]}
      camera={{ position: [0, 1.4, 5.4], fov: 42 }}
      style={{ width: '100%', height: '100%' }}
    >
      <SceneContents nodes={nodes} edges={edges} />
    </Canvas>
  )
}

/** Deterministic layout only — an even ring by the already-sorted node order, never a physics/relationship inference. */
function useAgentPositions(nodes: AgentNode[]): Map<string, [number, number, number]> {
  return useMemo(() => {
    const radius = nodes.length <= 1 ? 0 : 2.1
    const positions = new Map<string, [number, number, number]>()
    nodes.forEach((node, index) => {
      const angle = (index / Math.max(nodes.length, 1)) * Math.PI * 2
      positions.set(node.agentId, [Math.cos(angle) * radius, 0, Math.sin(angle) * radius])
    })
    return positions
  }, [nodes])
}

function useObservedEdgePulses(edges: AgentEdge[]): AgentEdge[] {
  const seenEdgeIdsRef = useRef<Set<string>>(new Set())
  const mountedRef = useRef(false)
  const [pulses, setPulses] = useState<AgentEdge[]>([])

  useEffect(() => {
    const currentIds = new Set(edges.map((edge) => edge.id))
    if (!mountedRef.current) {
      mountedRef.current = true
      seenEdgeIdsRef.current = currentIds
      setPulses([])
      return
    }
    const newlyObserved = diffNewlyObservedEdges(edges, seenEdgeIdsRef.current)
    seenEdgeIdsRef.current = currentIds
    setPulses(newlyObserved.length > 0 && !prefersReducedMotion() ? newlyObserved : [])
  }, [edges])

  return pulses
}

function SceneContents({ nodes, edges }: { nodes: AgentNode[]; edges: AgentEdge[] }) {
  const positions = useAgentPositions(nodes)
  const freshEdges = useObservedEdgePulses(edges)

  return (
    <>
      <ambientLight intensity={0.7} />
      <pointLight position={[3, 4, 5]} intensity={22} color="#f4f0e9" />
      <AgentCores nodes={nodes} positions={positions} />
      <AgentRings nodes={nodes} positions={positions} />
      {edges.map((edge) => {
        const from = positions.get(edge.from)
        const to = positions.get(edge.to)
        if (!from || !to) return null
        return <ConnectionBeam key={edge.id} from={from} to={to} fact={edge.fact} />
      })}
      <EdgePulses edges={freshEdges} positions={positions} />
    </>
  )
}

/** Núcleo: one shared octahedron geometry/material instanced per node (AGENT_VISUAL_LANGUAGE.md "malha equivalente" to the 2D rotated-square core). */
function AgentCores({ nodes, positions }: { nodes: AgentNode[]; positions: Map<string, [number, number, number]> }) {
  return (
    <Instances limit={Math.max(nodes.length, 1)} range={nodes.length}>
      <octahedronGeometry args={[0.32, 0]} />
      <meshStandardMaterial roughness={0.32} metalness={0.15} />
      {nodes.map((node) => {
        const position = positions.get(node.agentId)
        if (!position) return null
        return (
          <Instance
            key={node.agentId}
            position={position}
            color={deriveAgentAccent(node.agentId)}
            scale={coreScale(node.visualState)}
          />
        )
      })}
    </Instances>
  )
}

/** Anel: one shared torus geometry/material instanced per node, opacity mirroring the CSS state rules (AgentGlyph). */
function AgentRings({ nodes, positions }: { nodes: AgentNode[]; positions: Map<string, [number, number, number]> }) {
  return (
    <Instances limit={Math.max(nodes.length, 1)} range={nodes.length}>
      <torusGeometry args={[0.5, 0.028, 8, 32]} />
      <meshBasicMaterial transparent />
      {nodes.map((node) => {
        const position = positions.get(node.agentId)
        if (!position) return null
        return (
          <Instance
            key={node.agentId}
            position={position}
            rotation={[Math.PI / 2, 0, 0]}
            color={deriveAgentAccent(node.agentId)}
          />
        )
      })}
    </Instances>
  )
}

function coreScale(state: AgentNode['visualState']): number {
  switch (state) {
    case 'running':
      return 1.15
    case 'waiting':
      return 1.05
    case 'terminal':
      return 0.85
    default:
      return 1
  }
}

/** Conexão: a thin static beam between two node positions — relation confirmed, not a permanent channel. */
function ConnectionBeam({ from, to, fact }: { from: [number, number, number]; to: [number, number, number]; fact: AgentFact }) {
  return <Line points={[from, to]} color={fact === 'result' ? '#86d9a5' : '#c8ff6a'} transparent opacity={0.32} lineWidth={1} />
}

const PULSE_DURATION_MS = agentMotionTiming.connectPulseMs

/** Pulso: one shared instanced-sphere geometry animated purely via refs inside useFrame — never React state per frame. */
function EdgePulses({ edges, positions }: { edges: AgentEdge[]; positions: Map<string, [number, number, number]> }) {
  const meshRef = useRef<THREE.InstancedMesh>(null)
  const startedAtRef = useRef<number>(0)
  const dummy = useMemo(() => new THREE.Object3D(), [])
  const activeEdges = useMemo(
    () => edges.filter((edge) => positions.has(edge.from) && positions.has(edge.to)).slice(0, RAIL_NODE_LIMIT),
    [edges, positions],
  )

  useEffect(() => {
    if (activeEdges.length > 0) startedAtRef.current = performance.now()
  }, [activeEdges])

  useFrame(({ invalidate }) => {
    const mesh = meshRef.current
    if (!mesh) return
    const elapsed = performance.now() - startedAtRef.current
    const t = Math.min(1, elapsed / PULSE_DURATION_MS)
    for (let index = 0; index < activeEdges.length; index += 1) {
      const edge = activeEdges[index]
      const from = positions.get(edge.from)
      const to = positions.get(edge.to)
      if (!from || !to) continue
      if (t >= 1) {
        dummy.scale.setScalar(0)
      } else {
        dummy.position.set(
          from[0] + (to[0] - from[0]) * t,
          from[1] + (to[1] - from[1]) * t,
          from[2] + (to[2] - from[2]) * t,
        )
        dummy.scale.setScalar(1)
      }
      dummy.updateMatrix()
      mesh.setMatrixAt(index, dummy.matrix)
    }
    mesh.instanceMatrix.needsUpdate = true
    if (t < 1) invalidate()
  })

  if (activeEdges.length === 0) return null

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, activeEdges.length]}>
      <sphereGeometry args={[0.055, 8, 8]} />
      <meshBasicMaterial color="#c8ff6a" />
    </instancedMesh>
  )
}

function SceneTextFallback({ nodes, edges, overflowCount }: { nodes: AgentNode[]; edges: AgentEdge[]; overflowCount: number }) {
  return (
    <div className="orchestration-scene__fallback">
      <p className="orchestration-scene__fallback-hint">Vista 3D pausada; mostrando o mesmo grafo em texto.</p>
      <ul aria-label="Participantes da orquestração" className="orchestration-scene__fallback-list">
        {nodes.map((node) => (
          <li key={node.agentId}>
            <span>{node.agentId}</span>
            <span>{agentVisualStateLabel[node.visualState]}</span>
          </li>
        ))}
      </ul>
      {overflowCount > 0 && (
        <p className="orchestration-scene__fallback-overflow">
          {overflowCount} {overflowCount === 1 ? 'participante adicional' : 'participantes adicionais'} não exibidos.
        </p>
      )}
      <ul aria-label="Fatos de colaboração observados" className="orchestration-scene__fallback-facts">
        {edges.map((edge) => (
          <li key={edge.id}>
            {edge.from} → {edge.to} ({edge.fact})
          </li>
        ))}
      </ul>
    </div>
  )
}

type SceneErrorBoundaryProps = { children: ReactNode; fallback: ReactNode }
type SceneErrorBoundaryState = { hasError: boolean }

/** Never lets a WebGL/context failure crash the page: the same textual view the low-power profiles already use takes over. */
class SceneErrorBoundary extends Component<SceneErrorBoundaryProps, SceneErrorBoundaryState> {
  state: SceneErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): SceneErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(): void {
    // Intentionally silent: a WebGL/context failure here is an environment limit,
    // not an application error worth surfacing beyond the textual fallback.
  }

  render(): ReactNode {
    return this.state.hasError ? this.props.fallback : this.props.children
  }
}
