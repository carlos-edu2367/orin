import type { ActivityGroup } from './activityTypes'

const STAGES = [
  ['planning', 'Planejar'], ['implementing', 'Implementar'], ['validating', 'Testar'], ['fixing', 'Corrigir'], ['completed', 'Entregar'],
] as const

const PROVIDER_FAILURES = new Set(['PROVIDER_STREAM_FAILED', 'PROVIDER_STREAM_NOT_STARTED', 'PROVIDER_RETRY_EXHAUSTED'])

export function CodeModeCard({ group }: { group: ActivityGroup }) {
  const latest = group.events[group.events.length - 1]
  const stage = latest.codeStage ?? 'planning'
  const terminal = stage === 'completed' || stage === 'completed_with_caveats'
  const index = terminal ? STAGES.length - 1 : Math.max(0, STAGES.findIndex(([id]) => id === stage))
  const providerFailure = stage === 'blocked' && PROVIDER_FAILURES.has(latest.errorCode ?? '')
  const stateLabel = terminal ? 'Concluído' : stage === 'waiting_decision' || stage === 'waiting_approval'
    ? 'Aguardando você'
    : providerFailure ? 'Falha temporária'
      : stage === 'blocked' ? 'Bloqueado'
        : 'Em andamento'
  return <article className="code-mode-card" data-stage={stage} data-recoverable={providerFailure || undefined} aria-label="Progresso do Modo Code">
    <div className="code-mode-card__heading" aria-live="polite" aria-atomic="true"><span aria-hidden="true">&lt;/&gt;</span><div><strong>Modo Code</strong><p>{latest.summary || 'Preparando entrega verificável'}</p></div><span className="code-mode-card__state">{stateLabel}</span></div>
    <ol className="code-mode-card__steps" aria-label="Etapas da entrega">
      {STAGES.map(([id, label], itemIndex) => <li key={id} data-state={stage === 'blocked' ? 'next' : itemIndex < index ? 'done' : itemIndex === index ? 'current' : 'next'}><span aria-hidden="true">{itemIndex < index && stage !== 'blocked' ? '✓' : itemIndex + 1}</span>{label}</li>)}
    </ol>
    {stage === 'waiting_decision' && <p className="code-mode-card__notice">O Orin precisa de uma decisão para seguir com segurança.</p>}
    {providerFailure && <p className="code-mode-card__notice">Não foi possível alcançar o provedor depois de uma nova tentativa. Revise e reenvie a solicitação abaixo.</p>}
    {stage === 'blocked' && !providerFailure && <p className="code-mode-card__notice">A execução está bloqueada; consulte a atividade para ver a evidência.</p>}
  </article>
}
