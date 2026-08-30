import type { ActivityGroup } from './activityTypes'

const STAGES = [
  ['planning', 'Planejar'], ['implementing', 'Implementar'], ['validating', 'Testar'], ['fixing', 'Corrigir'], ['completed', 'Entregar'],
] as const

export function CodeModeCard({ group }: { group: ActivityGroup }) {
  const latest = group.events[group.events.length - 1]
  const stage = latest.codeStage ?? 'planning'
  const index = Math.max(0, STAGES.findIndex(([id]) => id === stage))
  const terminal = stage === 'completed' || stage === 'completed_with_caveats'
  return <article className="code-mode-card" data-stage={stage} aria-label="Progresso do Modo Code">
    <div className="code-mode-card__heading"><span aria-hidden="true">&lt;/&gt;</span><div><strong>Modo Code</strong><p>{latest.summary || 'Preparando entrega verificável'}</p></div><span className="code-mode-card__state">{terminal ? 'Concluído' : stage === 'waiting_decision' || stage === 'waiting_approval' ? 'Aguardando você' : 'Em andamento'}</span></div>
    <ol className="code-mode-card__steps" aria-label="Etapas da entrega">
      {STAGES.map(([id, label], itemIndex) => <li key={id} data-state={itemIndex < index ? 'done' : itemIndex === index ? 'current' : 'next'}><span aria-hidden="true">{itemIndex < index ? '✓' : itemIndex + 1}</span>{label}</li>)}
    </ol>
    {stage === 'waiting_decision' && <p className="code-mode-card__notice">O Orin precisa de uma decisão para seguir com segurança.</p>}
    {stage === 'blocked' && <p className="code-mode-card__notice">A execução está bloqueada; consulte a atividade para ver a evidência.</p>}
  </article>
}
