import { motion, useReducedMotion } from 'motion/react'
import { useEffect, useMemo, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { getConversationOverview, type ConversationOverview, type TokenUsage } from '../../api/conversations'
import type { AgentEdge, AgentNode, ActivityState, ConversationActivityEvent } from '../conversations/activityTypes'
import { activityStateLabel, toolKindLabel } from '../conversations/activitySummary'
import { OrbitalScene } from './OrbitalScene'

type OverviewPanelProps = {
  conversationId: string
  client: ApiClient
  liveEvents: ConversationActivityEvent[]
  onClose: () => void
}

/**
 * The execution seen whole.
 *
 * The scene carries the shape of the work — who exists, who talked to whom, what
 * is moving right now — and everything numeric stays underneath it, revealed by
 * selecting a node rather than laid out as a wall of cards.
 */
export function OverviewPanel({ conversationId, client, liveEvents, onClose }: OverviewPanelProps) {
  const [overview, setOverview] = useState<ConversationOverview | null>(null)
  const [failed, setFailed] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const reduced = useReducedMotion()

  useEffect(() => {
    let cancelled = false
    const load = () => {
      getConversationOverview(client, conversationId)
        .then((value) => { if (!cancelled) { setOverview(value); setFailed(false) } })
        .catch(() => { if (!cancelled) setFailed(true) })
    }
    load()
    // The overview is a projection of the same log the chat streams, so it is
    // refreshed on a slow interval instead of holding a second live connection.
    const timer = window.setInterval(load, 4000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [client, conversationId, liveEvents.length])

  const nodes = useMemo<AgentNode[]>(() => (overview?.agents ?? []).map((agent) => ({
    agentId: agent.agent_id,
    name: agent.name,
    role: agent.role,
    parentAgentId: agent.parent_agent_id,
    state: normalizeState(agent.state),
    provider: agent.provider,
    modelId: agent.model_id,
    tokenUsage: agent.token_usage,
  })), [overview])

  const edges = useMemo<AgentEdge[]>(() => {
    const items: AgentEdge[] = []
    for (const agent of overview?.agents ?? []) {
      if (agent.parent_agent_id) items.push({ id: `link:${agent.agent_id}`, from: agent.parent_agent_id, to: agent.agent_id, fact: 'delegation' })
    }
    for (const message of overview?.messages ?? []) {
      items.push({ id: `msg:${message.event_id}`, from: message.from_agent_id, to: message.to_agent_id, fact: 'message' })
    }
    return items
  }, [overview])

  const selectedAgent = useMemo(() => nodes.find((node) => node.agentId === selected) ?? null, [nodes, selected])
  const selectedTools = useMemo(
    () => (overview?.tools ?? []).filter((tool) => !selected || tool.agent_id === selected),
    [overview, selected],
  )

  return (
    <motion.aside
      className="overview"
      aria-label="Visão geral da execução"
      initial={reduced ? false : { opacity: 0, x: 28 }}
      animate={{ opacity: 1, x: 0 }}
      exit={reduced ? { opacity: 0 } : { opacity: 0, x: 28 }}
      transition={{ duration: 0.26, ease: [0.22, 0.61, 0.36, 1] }}
    >
      <header className="overview__head">
        <div>
          <p className="eyebrow">visão geral</p>
          <h2>{overview?.title ?? 'Execução'}</h2>
        </div>
        <button type="button" className="ghost-button" onClick={onClose} aria-label="Fechar visão geral">✕</button>
      </header>

      {failed && !overview && <p className="overview__error" role="alert">Não foi possível carregar a visão geral.</p>}
      {!overview && !failed && <p className="overview__placeholder" role="status">Montando o mapa da execução…</p>}

      {overview && (
        <>
          <div className="overview__scene">
            <OrbitalScene nodes={nodes} edges={edges} onSelect={setSelected} selectedAgentId={selected} />
            <ul className="overview__legend" aria-label="Agentes desta execução">
              {nodes.map((node) => (
                <li key={node.agentId}>
                  <button
                    type="button"
                    className={node.agentId === selected ? 'overview__agent is-selected' : 'overview__agent'}
                    onClick={() => setSelected(node.agentId === selected ? null : node.agentId)}
                    aria-pressed={node.agentId === selected}
                  >
                    <span className="overview__agent-glyph" data-state={node.state} aria-hidden="true">
                      {node.parentAgentId ? '◇' : '◆'}
                    </span>
                    <span className="overview__agent-name">{node.name}</span>
                    <span className="overview__agent-state">{activityStateLabel(node.state)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <dl className="overview__stats">
            <div><dt>Estado</dt><dd>{activityStateLabel(normalizeState(overview.state))}</dd></div>
            <div><dt>Modelo</dt><dd title={overview.model_id}>{overview.model_id || '—'}</dd></div>
            <div><dt>Provider</dt><dd>{overview.provider || '—'}</dd></div>
            <div><dt>Tokens gastos</dt><dd>{formatTokenUsage(overview.token_usage)}</dd></div>
            <div><dt>Duração</dt><dd>{formatDuration(overview.duration_seconds)}</dd></div>
            <div><dt>Eventos</dt><dd>{overview.activity_count}</dd></div>
            <div><dt>Mensagens entre agentes</dt><dd>{overview.messages.length}</dd></div>
          </dl>

          {selectedAgent && (
            <section className="overview__agent-detail" aria-label={`Detalhes de ${selectedAgent.name}`}>
              <h3>Detalhes de {selectedAgent.name}</h3>
              <dl>
                <div><dt>Papel</dt><dd>{selectedAgent.role || '—'}</dd></div>
                <div><dt>Estado</dt><dd>{activityStateLabel(selectedAgent.state)}</dd></div>
                <div><dt>Provider</dt><dd>{selectedAgent.provider || '—'}</dd></div>
                <div><dt>Modelo</dt><dd title={selectedAgent.modelId}>{selectedAgent.modelId || '—'}</dd></div>
                <div><dt>Tokens de entrada</dt><dd>{formatTokenCount(selectedAgent.tokenUsage?.input_tokens ?? null)}</dd></div>
                <div><dt>Tokens de saída</dt><dd>{formatTokenCount(selectedAgent.tokenUsage?.output_tokens ?? null)}</dd></div>
                <div><dt>Total de tokens</dt><dd>{formatTokenUsage(selectedAgent.tokenUsage)}</dd></div>
              </dl>
            </section>
          )}

          <section className="overview__section">
            <h3>{selectedAgent ? `Ferramentas de ${selectedAgent.name}` : 'Ferramentas utilizadas'}</h3>
            {selectedTools.length === 0 && <p className="overview__empty">Nenhuma ferramenta usada até aqui.</p>}
            <ul className="overview__tools">
              {selectedTools.map((tool) => (
                <li key={`${tool.agent_id}:${tool.tool_name}`} data-failed={tool.failures > 0}>
                  <span className="overview__tool-name">{tool.tool_name}</span>
                  <span className="overview__tool-kind">{toolKindLabel(tool.kind)}</span>
                  <span className="overview__tool-count">{tool.count}×</span>
                  {tool.failures > 0 && <span className="overview__tool-failures">{tool.failures} falha{tool.failures > 1 ? 's' : ''}</span>}
                </li>
              ))}
            </ul>
          </section>

          {overview.messages.length > 0 && (
            <section className="overview__section">
              <h3>Conversa entre agentes</h3>
              <ol className="overview__messages">
                {overview.messages.slice(-8).map((message) => (
                  <li key={message.event_id}>
                    <span className="overview__message-route">
                      {nameFor(nodes, message.from_agent_id)} <span aria-hidden="true">→</span> {nameFor(nodes, message.to_agent_id)}
                    </span>
                    <p>{message.preview || 'Sem prévia disponível.'}</p>
                  </li>
                ))}
              </ol>
            </section>
          )}

          {overview.errors.length > 0 && (
            <section className="overview__section">
              <h3>Erros</h3>
              <ul className="overview__errors">
                {overview.errors.slice(-5).map((item) => (
                  <li key={item.event_id}><code>{item.code}</code> {item.summary}</li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </motion.aside>
  )
}

function nameFor(nodes: AgentNode[], agentId: string): string {
  return nodes.find((node) => node.agentId === agentId)?.name ?? 'Agente'
}

function normalizeState(state: string): ActivityState {
  switch (state) {
    case 'queued': return 'queued'
    case 'starting': case 'running': case 'streaming': case 'working': return 'working'
    case 'cancelling': return 'working'
    case 'failed': return 'failed'
    case 'cancelled': return 'cancelled'
    case 'completed': return 'completed'
    case 'idle': return 'queued'
    default: return 'unknown'
  }
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${Math.round(seconds - minutes * 60)}s`
}

function formatTokenUsage(usage: TokenUsage | AgentNode['tokenUsage'] | undefined): string {
  if (!usage?.usage_reported) return 'indisponível'
  return formatTokenCount(usage.total_tokens)
}

function formatTokenCount(tokens: number | null): string {
  return tokens === null ? 'indisponível' : `${new Intl.NumberFormat('pt-BR').format(tokens)} tokens`
}
