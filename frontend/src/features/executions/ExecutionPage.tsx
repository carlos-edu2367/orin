import { useMemo } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { Disclosure } from '../../components/ui/Disclosure'
import { StatusLabel } from '../../components/ui/StatusLabel'
import { Brand } from '../../components/Brand'
import type { ClientEvent, ExecutionControlAction, ExecutionView } from '../../api/types'
import type { RealtimeConnectionState } from '../realtime/realtimeStore'
import { normalizeActivities } from '../activities/activityNormalizer'
import { ToolActivityGroup } from '../activities/ToolActivityGroup'
import { projectAgentGraph } from '../agents/agentGraphProjection'
import { AgentRail } from '../agents/AgentRail'
import { ExecutionControls } from './ExecutionControls'
import { ExecutionInputComposer } from './ExecutionInputComposer'
import { projectExecution } from './executionProjection'
import { isTerminalState, toVisualStatus } from './executionStatus'

type ExecutionPageProps = {
  execution: ExecutionView
  /** Authorized ClientEvents observed for this execution; absent outside a live binding. */
  events?: ClientEvent[]
  realtime?: {
    connection: RealtimeConnectionState
    appliedEventCount: number
  }
  controlPending?: boolean
  onControl?: (action: ExecutionControlAction) => void
  inputPending?: boolean
  onInput?: (inputRef: string) => void
}

export function ExecutionPage({ execution, events, realtime, controlPending = false, onControl, inputPending = false, onInput }: ExecutionPageProps) {
  const reducedMotion = useReducedMotion()
  const status = toVisualStatus(execution.state)
  const tone = execution.state === 'FAILED' ? 'danger' : execution.state === 'COMPLETED' ? 'success' : execution.state === 'WAITING_USER' ? 'waiting' : execution.state === 'CANCELLED' || execution.state === 'PAUSED' ? 'muted' : 'active'
  // Level 1 only surfaces Tool activity: the public stream authorizes no delegation
  // or resource projection yet, so those categories stay reserved, unobserved facts.
  const toolActivities = useMemo(() => normalizeActivities(events ?? []).filter((activity) => activity.category === 'tool'), [events])
  const eventsByGroup = useMemo(() => {
    const byId = new Map((events ?? []).map((event) => [event.event_id, event]))
    return toolActivities.map((activity) => ({
      activity,
      events: activity.eventIds.map((eventId) => byId.get(eventId)).filter((event): event is ClientEvent => event !== undefined),
    }))
  }, [events, toolActivities])
  // Only this execution is known here; a delegated child's own state is unavailable
  // until a delegation graph query/stream exists (BACKEND_DISCOVERY.md), so its node
  // falls back to the projection's own "idle" default rather than a guessed state.
  const knownExecutions = useMemo(() => ({ [execution.execution_id]: projectExecution(execution) }), [execution])
  const agentGraph = useMemo(() => projectAgentGraph(events ?? [], knownExecutions), [events, knownExecutions])

  return (
    <main className="app-shell execution-shell">
      <header className="topbar">
        <Brand href="/" />
        <span className="topbar__context">Execution <span aria-hidden="true">/</span> {execution.execution_id}</span>
        <a className="topbar__back" href="/">Voltar ao início</a>
      </header>

      <section className="execution-layout" aria-labelledby="execution-title">
        <div className="execution-main">
          <div className="execution-heading">
            <div>
              <p className="eyebrow">AGENT / ORBIT</p>
              <h1 id="execution-title">Uma tarefa em andamento</h1>
            </div>
            <StatusLabel tone={tone}>{status}</StatusLabel>
          </div>

          <div className="conversation-canvas">
            <motion.div
              className="agent-orb agent-orb--small"
              aria-hidden="true"
              initial={reducedMotion ? false : { scale: 0.96, opacity: 0.72 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ duration: 0.35 }}
            >
              <span className="agent-orb__core" />
              <span className="agent-orb__ring" />
            </motion.div>
            <div className="activity-block">
              <div className="activity-block__meta">
                <span>Atividade observada</span>
                <span className="activity-block__version">versão {execution.state_version}</span>
              </div>
              <p>{activityCopy(execution.state)}</p>
              {!isTerminalState(execution.state) && <span className="activity-block__hint">A interface será atualizada quando houver uma nova transição.</span>}
            </div>
          </div>

          {eventsByGroup.length > 0 && (
            <div className="activity-groups" aria-label="Atividade semântica observada">
              {eventsByGroup.map(({ activity, events: groupEvents }) => (
                <ToolActivityGroup key={activity.id} activity={activity} events={groupEvents} />
              ))}
            </div>
          )}

          <AgentRail graph={agentGraph} events={events ?? []} />

          {execution.result?.display_text && (
            <div className="result-block">
              <p className="eyebrow">RESULTADO AUTORIZADO</p>
              <p>{execution.result.display_text}</p>
            </div>
          )}

          {execution.failure && (
            <div className="error-notice" role="alert">
              <span className="error-notice__icon" aria-hidden="true">!</span>
              <div>
                <strong>Não foi possível concluir esta execução.</strong>
                <p>O estado final é exibido sem expor detalhes internos. Código: {execution.failure.code}.</p>
              </div>
            </div>
          )}

          {execution.state === 'WAITING_USER' && onInput && (
            <ExecutionInputComposer pending={inputPending} onSubmit={onInput} />
          )}

          <footer className="execution-footer">
            <ExecutionControls state={execution.state} pending={controlPending} onAction={onControl} />
            {realtime ? (
              <div className="realtime-summary">
                <span className="execution-footer__note" role="status" aria-live="polite" data-testid="realtime-state">
                  {connectionCopy(realtime.connection)}
                </span>
                <span className="execution-footer__note" data-testid="activity-count">
                  {realtime.appliedEventCount} {realtime.appliedEventCount === 1 ? 'atualização aplicada' : 'atualizações aplicadas'}
                </span>
              </div>
            ) : (
              <span className="execution-footer__note">Fixture local · sem conexão com o backend</span>
            )}
          </footer>
        </div>

        <aside className="execution-inspector" aria-label="Inspector da execução">
          <Disclosure label="Abrir inspector">
            <div className="inspector-panel">
              <p className="eyebrow">Detalhes técnicos</p>
              <dl>
                <div><dt>Execution ID</dt><dd>{execution.execution_id}</dd></div>
                <div><dt>Estado persistido</dt><dd>{execution.state}</dd></div>
                <div><dt>Versão</dt><dd>{execution.state_version}</dd></div>
                {events && events.length > 0 && (
                  <div><dt>Eventos aplicados</dt><dd>{events.length}</dd></div>
                )}
              </dl>
            </div>
          </Disclosure>
        </aside>
      </section>
    </main>
  )
}

function activityCopy(state: ExecutionView['state']): string {
  switch (state) {
    case 'WAITING_USER':
      return 'A execução está aguardando uma decisão sua para continuar.'
    case 'WAITING_TOOL':
      return 'Uma ferramenta foi solicitada; aguardando a próxima transição observável.'
    case 'PAUSED':
      return 'A execução está pausada e pode ser retomada.'
    case 'COMPLETED':
      return 'A execução foi concluída.'
    case 'FAILED':
      return 'A execução terminou com falha.'
    case 'CANCELLED':
      return 'A execução foi cancelada.'
    case 'QUEUED':
    case 'STARTING':
      return 'A execução foi aceita e está sendo preparada.'
    case 'RUNNING':
      return 'O agent está trabalhando nesta tarefa.'
  }
}

function connectionCopy(connection: RealtimeConnectionState): string {
  switch (connection) {
    case 'idle':
    case 'connecting':
      return 'Conectando atualizações'
    case 'live':
      return 'Atualizações em tempo real ativas'
    case 'recovering':
      return 'Recuperando conexão'
    case 'resyncing':
      return 'Ressincronizando dados'
    case 'degraded':
      return 'Atualizações em tempo real indisponíveis'
  }
}
