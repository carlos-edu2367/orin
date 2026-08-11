import type { AgentEdge, AgentFact, AgentNode } from './agentGraphProjection'

export const agentMotionTiming = {
  /** A→B / B→A connection pulse. Inside the 160–240ms feedback band (MOTION_SYSTEM.md). */
  connectPulseMs: 220,
} as const

/**
 * Read directly rather than via `useReducedMotion` from `motion/react`: that hook
 * memoizes the preference once per process (framer-motion's `hasReducedMotionListener`
 * singleton), so it cannot be reconfigured per test. Mirrors ActivityGroup's approach
 * from Fase 3 for the same reason.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

const factLabel: Record<AgentFact, string> = {
  delegation: 'Delegação observada',
  message: 'Mensagem observada',
  result: 'Retorno observado',
}

export function agentFactLabel(fact: AgentFact): string {
  return factLabel[fact]
}

/** Human label per `AgentVisualState`, shared by `AgentGlyph` (2D) and `OrchestrationScene` (3D). */
export const agentVisualStateLabel: Record<AgentNode['visualState'], string> = {
  idle: 'Ocioso',
  queued: 'Na fila',
  running: 'Trabalhando',
  waiting: 'Aguardando delegações',
  terminal: 'Concluído',
}

/**
 * Deterministic, content-free accent: a hash of the agentId, never of any
 * message/handoff data. The same formula backs the 2D glyph core/ring and the 3D
 * node mesh/ring so one agent reads as the same color in both views.
 */
export function deriveAgentAccent(agentId: string): string {
  let hash = 0
  for (let index = 0; index < agentId.length; index += 1) hash = (hash * 31 + agentId.charCodeAt(index)) >>> 0
  const hue = hash % 360
  return `hsl(${hue}, 78%, 64%)`
}

/**
 * Pure diff shared by the 2D rail and the 3D scene to detect which edges are newly
 * observed since a caller's own last check — the single fact-driven pulse trigger
 * rule from MOTION_SYSTEM.md, so neither view invents a second trigger. Each caller
 * keeps its own `seenEdgeIds`/mounted bookkeeping (each view has its own mount
 * lifecycle); only the trigger rule itself is shared.
 */
export function diffNewlyObservedEdges(edges: AgentEdge[], seenEdgeIds: ReadonlySet<string>): AgentEdge[] {
  return edges.filter((edge) => !seenEdgeIds.has(edge.id))
}
