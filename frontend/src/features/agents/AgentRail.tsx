import { lazy, Suspense, useEffect, useId, useRef, useState } from 'react'
import { motion } from 'motion/react'
import { Disclosure } from '../../components/ui/Disclosure'
import type { ClientEvent } from '../../api/types'
import { AgentGlyph } from './AgentGlyph'
import { agentMotionTiming, diffNewlyObservedEdges, prefersReducedMotion } from './agentMotion'
import type { AgentEdge, AgentFact, AgentGraph } from './agentGraphProjection'

/** Shared with `OrchestrationScene` (AGENT_VISUAL_LANGUAGE.md "Grafo"): same source, same ceiling. */
export const RAIL_NODE_LIMIT = 12

/** Lazy: the R3F scene is a second, deliberately-opened representation, never a default render cost (FRONTEND_ARCHITECTURE.md "Performance"). */
const OrchestrationScene = lazy(() =>
  import('./OrchestrationScene').then((module) => ({ default: module.OrchestrationScene })),
)

const SCENE_LAYOUT_ID = 'orchestration-scene-launcher'

type AgentRailProps = {
  graph: AgentGraph
  /** Used only to resolve a source event's `occurred_at` for the level-3 inspector; never for content. */
  events?: ClientEvent[]
}

/**
 * Compact 2D collaboration rail (AGENT_VISUAL_LANGUAGE.md "Grafo"). Only renders
 * once at least one delegation/message/result fact has been observed — an isolated
 * child execution or an idle roster never promises collaboration on its own.
 */
export function AgentRail({ graph, events = [] }: AgentRailProps) {
  const overflowListId = useId()
  const scenePanelId = useId()
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [pulsingAgentIds, setPulsingAgentIds] = useState<ReadonlySet<string>>(new Set())
  const [sceneExpanded, setSceneExpanded] = useState(false)
  const seenEdgeIdsRef = useRef<Set<string>>(new Set())
  const mountedRef = useRef(false)

  useEffect(() => {
    const currentIds = new Set(graph.edges.map((edge) => edge.id))
    if (!mountedRef.current) {
      mountedRef.current = true
      seenEdgeIdsRef.current = currentIds
      return
    }
    const newlyObserved = diffNewlyObservedEdges(graph.edges, seenEdgeIdsRef.current)
    seenEdgeIdsRef.current = currentIds
    if (newlyObserved.length === 0 || prefersReducedMotion()) return

    const affected = new Set<string>()
    newlyObserved.forEach((edge) => {
      affected.add(edge.from)
      affected.add(edge.to)
    })
    setPulsingAgentIds(affected)
    const timeout = window.setTimeout(() => setPulsingAgentIds(new Set()), agentMotionTiming.connectPulseMs)
    return () => window.clearTimeout(timeout)
  }, [graph.edges])

  if (graph.edges.length === 0) return null

  const visibleNodes = graph.nodes.slice(0, RAIL_NODE_LIMIT)
  const overflowNodes = graph.nodes.slice(RAIL_NODE_LIMIT)
  const eventsById = new Map(events.map((event) => [event.event_id, event]))
  const lastFactByAgent = new Map<string, AgentFact>()
  graph.edges.forEach((edge) => {
    lastFactByAgent.set(edge.from, edge.fact)
    lastFactByAgent.set(edge.to, edge.fact)
  })

  return (
    <section className="agent-rail" aria-label="Colaboração entre agents observada">
      <p className="eyebrow">Colaboração observada</p>
      <div className={`agent-rail__glyphs${expandedId && expandedId !== OVERFLOW_KEY ? ' agent-rail__glyphs--detail-open' : ''}`} role="list">
        {visibleNodes.map((node) => (
          <div className="agent-rail__item" role="listitem" key={node.agentId}>
            <AgentGlyph
              node={node}
              expanded={expandedId === node.agentId}
              onToggle={() => setExpandedId((current) => (current === node.agentId ? null : node.agentId))}
              pulsing={pulsingAgentIds.has(node.agentId)}
              lastFact={lastFactByAgent.get(node.agentId) ?? null}
            />
          </div>
        ))}
        {overflowNodes.length > 0 && (
          <button
            type="button"
            className="agent-rail__overflow"
            aria-expanded={expandedId === OVERFLOW_KEY}
            aria-controls={overflowListId}
            aria-label={`Mais ${overflowNodes.length} ${overflowNodes.length === 1 ? 'agent participante' : 'agents participantes'}`}
            onClick={() => setExpandedId((current) => (current === OVERFLOW_KEY ? null : OVERFLOW_KEY))}
          >
            +{overflowNodes.length}
          </button>
        )}
      </div>
      {overflowNodes.length > 0 && expandedId === OVERFLOW_KEY && (
        <ul id={overflowListId} className="agent-rail__overflow-list" aria-label="Participantes adicionais">
          {overflowNodes.map((node) => (
            <li key={node.agentId}>{node.agentId}</li>
          ))}
        </ul>
      )}
      {!sceneExpanded && (
        <motion.button
          type="button"
          className="agent-rail__expand"
          aria-expanded={sceneExpanded}
          aria-controls={scenePanelId}
          layoutId={prefersReducedMotion() ? undefined : SCENE_LAYOUT_ID}
          onClick={() => setSceneExpanded(true)}
        >
          Expandir grafo <span aria-hidden="true">↗</span>
        </motion.button>
      )}
      {sceneExpanded && (
        <motion.div
          id={scenePanelId}
          className="agent-rail__scene-panel"
          layoutId={prefersReducedMotion() ? undefined : SCENE_LAYOUT_ID}
        >
          <button type="button" className="agent-rail__collapse" onClick={() => setSceneExpanded(false)}>
            Recolher grafo
          </button>
          <Suspense fallback={<p className="orchestration-scene__fallback-hint">Carregando cena…</p>}>
            <OrchestrationScene graph={graph} />
          </Suspense>
        </motion.div>
      )}
      <Disclosure label="Detalhes técnicos da colaboração" className="agent-rail__inspector">
        <ul className="agent-rail__edges">
          {graph.edges.map((edge) => (
            <EdgeRow key={edge.id} edge={edge} occurredAt={eventsById.get(edge.eventId)?.occurred_at ?? null} />
          ))}
        </ul>
      </Disclosure>
    </section>
  )
}

const OVERFLOW_KEY = '__overflow__'

function EdgeRow({ edge, occurredAt }: { edge: AgentEdge; occurredAt: string | null }) {
  return (
    <li className="agent-rail__edge">
      <span>{factCopy(edge.fact)}</span>
      <span>{edge.from} → {edge.to}</span>
      <span>{edge.eventId}</span>
      {occurredAt && <span>{occurredAt}</span>}
    </li>
  )
}

function factCopy(fact: AgentFact): string {
  switch (fact) {
    case 'delegation':
      return 'Delegação'
    case 'message':
      return 'Mensagem'
    case 'result':
      return 'Retorno'
  }
}
