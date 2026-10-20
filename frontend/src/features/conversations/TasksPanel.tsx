import { useCallback, useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { getConversationOverview } from '../../api/conversations'
import { ApiError } from '../../api/errors'
import { listTasks, stopTask, type ShellTask } from '../../api/sessionTools'
import { useKeybindingHandlers } from '../keybindings/engine'

export type BackgroundAgent = { agentId: string; name: string; role: string; state: string }

const SHELL_STATE: Record<ShellTask['state'], string> = { running: 'rodando', finished: 'terminado', unknown: 'estado desconhecido' }
const AGENT_STATE: Record<string, string> = {
  working: 'trabalhando', running: 'trabalhando', completed: 'concluído', failed: 'falhou', cancelled: 'cancelado', idle: 'parado',
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
export function TasksPanel({ client, conversationId, onClose }: { client: ApiClient; conversationId: string; onClose: () => void }) {
  const [shells, setShells] = useState<ShellTask[] | null>(null)
  const [agents, setAgents] = useState<BackgroundAgent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [stopping, setStopping] = useState<string | null>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  useKeybindingHandlers('Select', { 'select:cancel': () => onClose() })
  useEffect(() => { closeRef.current?.focus() }, [])

  const fetchAll = useCallback(
    () => Promise.allSettled([listTasks(client, conversationId), getConversationOverview(client, conversationId)]),
    [client, conversationId],
  )

  useEffect(() => {
    let cancelled = false
    const refresh = () => {
      fetchAll().then(([tasks, overview]) => {
        if (cancelled) return
        if (tasks.status === 'fulfilled') { setShells(tasks.value); setError(null) } else {
          setShells((current) => current ?? [])
          setError('Não foi possível ler os processos em segundo plano.')
        }
        if (overview.status === 'fulfilled') {
          setAgents(overview.value.agents.filter((agent) => agent.parent_agent_id !== null)
            .map((agent) => ({ agentId: agent.agent_id, name: agent.name, role: agent.role, state: agent.state })))
        }
      })
    }
    refresh()
    const timer = window.setInterval(refresh, REFRESH_MS)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [fetchAll])

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
                {agents.map((agent) => (
                  <li key={agent.agentId} className="tasks-panel__item">
                    <div className="tasks-panel__row">
                      <span className="tasks-panel__command">{agent.name}</span>
                      <span className="tasks-panel__state">{AGENT_STATE[agent.state] ?? agent.state}{agent.role ? ` · ${agent.role}` : ''}</span>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="tasks-panel__note">O Orin não tem colegas de equipe (teammates): o trabalho em paralelo é feito por subagentes, listados acima.</p>
        </div>
      </section>
    </div>
  )
}
