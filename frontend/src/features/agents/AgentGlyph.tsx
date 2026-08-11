import { useId, type CSSProperties } from 'react'
import { motion } from 'motion/react'
import type { AgentFact, AgentNode } from './agentGraphProjection'
import { agentFactLabel, agentVisualStateLabel, deriveAgentAccent, prefersReducedMotion } from './agentMotion'

type AgentGlyphProps = {
  node: AgentNode
  expanded: boolean
  onToggle: () => void
  pulsing?: boolean
  lastFact?: AgentFact | null
}

/**
 * Compact per-agent glyph for the rail. Reuses the same core/ring/accent motif as
 * `.agent-orb`/`.activity-group__glyph` (AGENT_VISUAL_LANGUAGE.md): a rotated-square
 * núcleo plus an anel whose state communicates activity, and an accent hue that
 * differentiates one agent from another.
 */
export function AgentGlyph({ node, expanded, onToggle, pulsing = false, lastFact = null }: AgentGlyphProps) {
  const detailId = useId()
  const reducedMotion = prefersReducedMotion()
  const stateLabel = agentVisualStateLabel[node.visualState]
  const accent = deriveAgentAccent(node.agentId)
  const style = { '--agent-accent': accent } as CSSProperties

  return (
    <div className="agent-glyph" style={style} data-state={node.visualState}>
      <button
        type="button"
        className="agent-glyph__trigger"
        aria-expanded={expanded}
        aria-controls={detailId}
        aria-label={`${node.agentId} · ${stateLabel}`}
        onClick={onToggle}
      >
        <motion.span
          className={`agent-glyph__core${pulsing && !reducedMotion ? ' agent-glyph__core--pulse' : ''}`}
          aria-hidden="true"
          layoutId={reducedMotion ? undefined : `agent-core-${node.agentId}`}
        />
        <span className="agent-glyph__ring" aria-hidden="true" />
      </button>
      {expanded && (
        <div id={detailId} className="agent-glyph__detail" role="group" aria-label={`Detalhes de ${node.agentId}`}>
          <p className="agent-glyph__state">{stateLabel}</p>
          {node.executionId && <p className="agent-glyph__execution">Execution {node.executionId}</p>}
          {lastFact && <p className="agent-glyph__fact">{agentFactLabel(lastFact)}</p>}
        </div>
      )}
    </div>
  )
}
