import type { ActivityGroup } from './activityTypes'

const STAGES = [
  ['planning', 'Planejar'], ['implementing', 'Implementar'], ['validating', 'Testar'], ['fixing', 'Corrigir'], ['completed', 'Entregar'],
] as const

const PROVIDER_FAILURES = new Set(['PROVIDER_STREAM_FAILED', 'PROVIDER_STREAM_NOT_STARTED', 'PROVIDER_RETRY_EXHAUSTED'])

export function CodeModeCard({ group }: { group: ActivityGroup }) {
  const latest = group.events[group.events.length - 1]
  const stageEvent = [...group.events].reverse().find((event) => Boolean(event.codeStage))
  const stage = stageEvent?.codeStage ?? latest.codeStage ?? 'planning'
  const todos = [...group.events].reverse().find((event) => Boolean(event.todos && event.todos.length > 0))?.todos
  const activeTodo = todos?.find((todo) => todo.status === 'in_progress')
  const terminal = stage === 'completed' || stage === 'completed_with_caveats'
  const cancelled = group.state === 'cancelled' || latest.errorCode === 'TURN_CANCELLED'
  const index = terminal ? STAGES.length - 1 : Math.max(0, STAGES.findIndex(([id]) => id === stage))
  const providerFailure = stage === 'blocked' && PROVIDER_FAILURES.has(latest.errorCode ?? '')
  const stateLabel = terminal ? 'Concluído'
    : cancelled ? 'Cancelada'
    : stage === 'waiting_decision' || stage === 'waiting_approval'
      ? 'Aguardando você'
      : providerFailure ? 'Falha temporária'
        : stage === 'blocked' ? 'Bloqueado'
          : activeTodo?.activeForm || 'Em andamento'
  return <article className="code-mode-card" data-stage={cancelled ? 'cancelled' : stage} data-recoverable={providerFailure || undefined} aria-label="Progresso do Modo Code">
    <div className="code-mode-card__heading" aria-live="polite" aria-atomic="true"><span aria-hidden="true">&lt;/&gt;</span><div><strong>Modo Code</strong><p>{cancelled ? 'Execução interrompida pelo usuário' : (latest.summary || 'Preparando entrega verificável')}</p></div><span className="code-mode-card__state">{stateLabel}</span></div>
    <ol className="code-mode-card__steps" aria-label="Etapas da entrega">
      {STAGES.map(([id, label], itemIndex) => <li key={id} data-state={stage === 'blocked' || cancelled ? 'next' : itemIndex < index ? 'done' : itemIndex === index ? 'current' : 'next'}><span aria-hidden="true">{itemIndex < index && stage !== 'blocked' && !cancelled ? '✓' : itemIndex + 1}</span>{label}</li>)}
    </ol>
    {todos && todos.length > 0 && (
      <ul className="code-mode-card__todos" aria-label="Lista de tarefas">
        {todos.map((todo) => (
          <li key={todo.id} className="code-mode-card__todo" data-todo-status={todo.status}>
            <span className="code-mode-card__todo-icon" aria-hidden="true">
              {todo.status === 'completed' ? '✓' : todo.status === 'in_progress' ? '⟳' : '○'}
            </span>
            <span className="code-mode-card__todo-text">{todo.content}</span>
            {todo.status === 'in_progress' && todo.activeForm && todo.activeForm !== todo.content && (
              <span className="code-mode-card__todo-active">{todo.activeForm}</span>
            )}
          </li>
        ))}
      </ul>
    )}
    {stage === 'waiting_decision' && <p className="code-mode-card__notice">O Orin precisa de uma decisão para seguir com segurança.</p>}
    {providerFailure && <p className="code-mode-card__notice">Não foi possível alcançar o provedor depois de uma nova tentativa. Revise e reenvie a solicitação abaixo.</p>}
    {stage === 'blocked' && !providerFailure && <p className="code-mode-card__notice">A execução está bloqueada; consulte a atividade para ver a evidência.</p>}
  </article>
}
