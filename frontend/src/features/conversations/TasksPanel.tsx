import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import type { ApiClient } from '../../api/client'
import { getConversationOverview, listAgentRuns, type AgentRunItem } from '../../api/conversations'
import { ApiError } from '../../api/errors'
import { listTasks, stopTask, type ShellTask } from '../../api/sessionTools'
import { useKeybindingHandlers } from '../keybindings/engine'
import { AgentMessageBox } from './AgentMessageBox'
import { AgentProgressRow } from './AgentProgressRow'
import type { AgentMessagesState } from './agentMessages'
import {
  hydrateAgentRuns,
  groupAgentTreeByPipelineRun,
  mergeAgentProgress,
  orderAgentTree,
  reconcileAgentProgress,
  type AgentProgressMap,
} from './agentTree'

/** `depth` is 0 for an agent the main agent started, 1 for one that agent started, and so on. */
export type BackgroundAgent = {
  agentId: string
  name: string
  role: string
  state: string
  parentAgentId: string | null
  depth: number
  /** Set when a pipeline run started this agent (0.13 WS-C11): such agents are listed together under the run. */
  pipelineRunId?: string | null
}

const SHELL_STATE: Record<ShellTask['state'], string> = { running: 'rodando', finished: 'terminado', unknown: 'estado desconhecido' }
const AGENT_STATE: Record<string, string> = {
  working: 'trabalhando',
  running: 'trabalhando',
  queued: 'na fila',
  stalled: 'travado',
  interrupted: 'interrompido',
  completed: 'concluído',
  failed: 'falhou',
  cancelled: 'cancelado',
  idle: 'parado',
}
const REFRESH_MS = 3000

/** Only a web address opens from the panel (a shell's detected URL is the command's own output). */
export function safeUrl(value: string | null): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}

function when(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
}

/**
 * `/tasks` (`/bashes`, W7): the session's background shells — with their output's tail and a stop button when the
 * process can be verified as the one that was started — and its subagents. It refreshes while open.
 */
export function TasksPanel({
  client,
  conversationId,
  onClose,
  agentMessages,
  onAgentMessageSent,
  agentProgress: liveAgentProgress,
}: {
  client: ApiClient
  conversationId: string
  onClose: () => void
  agentMessages?: AgentMessagesState
  onAgentMessageSent?: (item: { controlId: string; agentId: string; message: string }) => void
  agentProgress?: AgentProgressMap
}) {
  const [shells, setShells] = useState<ShellTask[] | null>(null)
  const [agents, setAgents] = useState<BackgroundAgent[]>([])
  const [runProgress, setRunProgress] = useState<AgentProgressMap>({})
  const [error, setError] = useState<string | null>(null)
  const [stopping, setStopping] = useState<string | null>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  useKeybindingHandlers('Select', { 'select:cancel': () => onClose() })
  useEffect(() => { closeRef.current?.focus() }, [])

  const fetchAll = useCallback(
    () =>
      Promise.allSettled([
        listTasks(client, conversationId),
        getConversationOverview(client, conversationId),
        listAgentRuns(client, conversationId),
      ]),
    [client, conversationId],
  )

  const refresh = useCallback(() => {
    let active = true
    void fetchAll().then(([tasks, overview, runsResult]) => {
      if (!active) return
      if (tasks.status === 'fulfilled') { setShells(tasks.value); setError(null) } else {
        setShells((current) => current ?? [])
        setError('Não foi possível ler os processos em segundo plano.')
      }
      const fetchedRuns: AgentRunItem[] = runsResult.status === 'fulfilled' ? runsResult.value.runs : []
      if (runsResult.status === 'fulfilled') {
        setRunProgress(hydrateAgentRuns(fetchedRuns))
      }
      const overviewAgents = overview.status === 'fulfilled'
        ? overview.value.agents.filter((agent) => agent.parent_agent_id != null)
        : []
      const byId = new Map<string, { agent_id: string; name: string; role: string; state: string; parent_agent_id: string | null; pipeline_run_id?: string | null }>()
      for (const agent of overviewAgents) {
        byId.set(agent.agent_id, { ...agent })
      }
      for (const run of fetchedRuns) {
        const existing = byId.get(run.agent_id)
        if (existing) {
          existing.state = run.stalled ? 'stalled' : run.state
          existing.pipeline_run_id = run.pipeline_run_id
        } else {
          byId.set(run.agent_id, {
            agent_id: run.agent_id,
            name: run.subagent_type || run.agent_id,
            role: run.subagent_type,
            state: run.stalled ? 'stalled' : run.state,
            parent_agent_id: run.parent_agent_id,
            pipeline_run_id: run.pipeline_run_id,
          })
        }
      }
      if (overview.status === 'fulfilled' || runsResult.status === 'fulfilled') {
        const combined = Array.from(byId.values())
        setAgents(
          orderAgentTree(combined, (agent) => agent.agent_id, (agent) => agent.parent_agent_id).map(({ item: agent, depth }) => ({
            agentId: agent.agent_id,
            name: agent.name,
            role: agent.role,
            state: agent.state,
            parentAgentId: agent.parent_agent_id,
            depth,
            pipelineRunId: agent.pipeline_run_id ?? null,
          })),
        )
      }
    })
    return () => { active = false }
  }, [fetchAll])

  useEffect(() => {
    const cancelFirst = refresh()
    const timer = window.setInterval(() => { refresh() }, REFRESH_MS)
    return () => { cancelFirst(); window.clearInterval(timer) }
  }, [refresh])

  const effectiveProgress = useMemo<AgentProgressMap>(() => {
    const merged: AgentProgressMap = { ...runProgress }
    if (liveAgentProgress) {
      for (const [id, data] of Object.entries(liveAgentProgress)) {
        const reconciled = reconcileAgentProgress(merged[id], data)
        if (reconciled) merged[id] = reconciled
      }
    }
    return merged
  }, [runProgress, liveAgentProgress])

  const handleAgentResumed = useCallback((agentId: string) => {
    setRunProgress((prev) => ({
      ...prev,
      [agentId]: mergeAgentProgress(prev[agentId], {
        agentId,
        state: 'queued',
        interrupted: false,
        stalled: false,
      }),
    }))
    setAgents((prev) => prev.map((agent) => (agent.agentId === agentId ? { ...agent, state: 'queued' } : agent)))
  }, [])

  async function stop(task: ShellTask) {
    setStopping(task.taskId)
    try {
      const result = await stopTask(client, conversationId, task.taskId)
      setShells(result.tasks)
      setError(result.stopped ? null : 'O processo já não era o mesmo que foi iniciado; nada foi parado.')
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 404 ? 'Parar processos não está disponível neste Orin.' : 'Não foi possível parar o processo.')
    } finally {
      setStopping(null)
    }
  }

  const empty = shells !== null && shells.length === 0 && agents.length === 0
  const agentSections = useMemo(
    () => groupAgentTreeByPipelineRun(agents.map((agent) => ({ item: agent, depth: agent.depth })), (agent) => agent.pipelineRunId),
    [agents],
  )
  return (
    <div className="command-info__backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="command-info tasks-panel" role="dialog" aria-modal="true" aria-labelledby="tasks-title">
        <header className="command-info__header">
          <h2 id="tasks-title">Tarefas em segundo plano</h2>
          <button ref={closeRef} type="button" className="ghost-button" onClick={onClose}>Fechar</button>
        </header>
        <div className="command-info__body" role="region" aria-label="Processos e agentes" tabIndex={0}>
          {shells === null && <p className="tasks-panel__note">Carregando…</p>}
          {error && <p className="tasks-panel__error" role="alert">{error}</p>}
          {empty && <p className="tasks-panel__note">Nenhum processo ou agente em segundo plano nesta sessão.</p>}
          {shells !== null && shells.length > 0 && (
            <>
              <h3>Processos</h3>
              <ul className="tasks-panel__list">
                {shells.map((task) => {
                  const url = safeUrl(task.url)
                  return (
                    <li key={task.taskId} className={`tasks-panel__item tasks-panel__item--${task.state}`}>
                      <div className="tasks-panel__row">
                        <code className="tasks-panel__command">{task.command || task.description || task.taskId}</code>
                        <span className="tasks-panel__state">{SHELL_STATE[task.state]}{when(task.startedAt) ? ` · ${when(task.startedAt)}` : ''}</span>
                        {task.stoppable && (
                          <button type="button" className="ghost-button" disabled={stopping !== null} onClick={() => void stop(task)}>
                            {stopping === task.taskId ? 'Parando…' : 'Parar'}
                          </button>
                        )}
                      </div>
                      {task.description && task.command && <p className="tasks-panel__description">{task.description}</p>}
                      {url && <a className="tasks-panel__url" href={url} target="_blank" rel="noopener noreferrer">{url}</a>}
                      {task.logTail && (
                        <details className="tasks-panel__log">
                          <summary>Saída</summary>
                          <pre tabIndex={0}>{task.logTail}</pre>
                        </details>
                      )}
                    </li>
                  )
                })}
              </ul>
            </>
          )}
          {agents.length > 0 && (
            <>
              <h3>Agentes</h3>
              <ul className="tasks-panel__list">
                {agentSections.flatMap((section) => [
                  ...(section.runId ? [(
                    <li key={`run-${section.runId}`} className="tasks-panel__run" data-testid="tasks-panel-pipeline-run">
                      Pipeline <code>{section.runId}</code>
                    </li>
                  )] : []),
                  ...section.entries.map(({ item: agent }) => {
                  const progress = effectiveProgress[agent.agentId]
                  const displayState: string = progress?.stalled ? 'stalled' : progress?.state ?? agent.state
                  return (
                    <li
                      key={agent.agentId}
                      className={agent.depth > 0 ? 'tasks-panel__item tasks-panel__item--nested' : 'tasks-panel__item'}
                      style={{ '--agent-depth': agent.depth } as CSSProperties}
                      data-depth={agent.depth}
                    >
                      <div className="tasks-panel__row">
                        <span className="tasks-panel__command">{agent.name}</span>
                        <span className="tasks-panel__state">{AGENT_STATE[displayState] ?? displayState}{agent.role ? ` · ${agent.role}` : ''}</span>
                      </div>
                      {progress && (
                        <AgentProgressRow
                          progress={progress}
                          conversationId={conversationId}
                          client={client}
                          onResumed={handleAgentResumed}
                        />
                      )}
                      <AgentMessageBox
                        conversationId={conversationId}
                        agentId={agent.agentId}
                        agentName={agent.name}
                        running={displayState === 'running' || displayState === 'working' || displayState === 'stalled'}
                        client={client}
                        liveMessages={agentMessages?.[agent.agentId]}
                        onMessageSent={onAgentMessageSent}
                        compact
                      />
                    </li>
                  )
                  }),
                ])}
              </ul>
            </>
          )}
          <p className="tasks-panel__note">O Orin não tem colegas de equipe (teammates): o trabalho em paralelo é feito por subagentes, listados acima.</p>
        </div>
      </section>
    </div>
  )
}
