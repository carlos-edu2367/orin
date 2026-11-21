import type { ConflictResolution, MemoryReviewItem } from '../../api/memory'
import './memoryReview.css'

const SOURCE_KIND_LABELS: Record<string, string> = {
  user: 'Usuário',
  agent: 'Agente',
  tool: 'Ferramenta',
  web: 'Web',
  file: 'Arquivo',
}

const SCOPE_LABELS: Record<string, string> = {
  user: 'Usuário',
  project: 'Projeto',
  agent: 'Agente',
  friend: 'Friend',
}

function sourceLabel(sourceKind: string): string {
  return SOURCE_KIND_LABELS[sourceKind] ?? sourceKind
}

function scopeLabel(scopeType: string): string {
  return SCOPE_LABELS[scopeType] ?? scopeType
}

export type MemoryConflictCardProps = {
  item: MemoryReviewItem
  busy?: boolean
  onResolve: (
    memoryId: string,
    resolution: ConflictResolution,
    conflictingMemoryId?: string,
  ) => void | Promise<void>
  onOpenHistory?: (memoryId: string) => void
}

export function MemoryConflictCard({
  item,
  busy = false,
  onResolve,
  onOpenHistory,
}: MemoryConflictCardProps) {
  const existing = item.conflicting_memory

  return (
    <article
      className="memory-conflict-card"
      data-testid={`memory-conflict-card-${item.memory_id}`}
      aria-label="Conflito de memória"
    >
      <header className="memory-conflict-card__header">
        <div className="memory-review__chips">
          <h3 className="memory-conflict-card__title">Conflito</h3>
          <span className="memory-review__chip memory-review__chip--conflict">
            {scopeLabel(item.scope_type)}
          </span>
          {item.agent_key && (
            <span className="memory-review__chip">Agente: {item.agent_key}</span>
          )}
        </div>
        {onOpenHistory && (
          <button
            type="button"
            className="memory-review__btn"
            disabled={busy}
            onClick={() => onOpenHistory(item.memory_id)}
          >
            Histórico
          </button>
        )}
      </header>

      <div className="memory-conflict-card__columns">
        <section
          className="memory-conflict-card__pane"
          data-testid="conflict-existing-memory"
          aria-label="Memória ativa atual"
        >
          <span className="memory-conflict-card__pane-label">Memória ativa atual</span>
          <p className="memory-review__fact">
            {existing ? existing.fact : 'Memória anterior não encontrada.'}
          </p>
          {existing && (
            <span className="memory-conflict-card__meta">
              Origem: {sourceLabel(existing.source_kind)}
              {existing.agent_key ? ` · Agente: ${existing.agent_key}` : ''}
            </span>
          )}
        </section>

        <section
          className="memory-conflict-card__pane"
          data-testid="conflict-proposed-memory"
          aria-label="Nova proposta"
        >
          <span className="memory-conflict-card__pane-label">Nova proposta</span>
          <p className="memory-review__fact">{item.fact}</p>
          <span className="memory-conflict-card__meta">
            Origem: {sourceLabel(item.source_kind)}
            {item.agent_key ? ` · Agente: ${item.agent_key}` : ''}
          </span>
        </section>
      </div>

      <div className="memory-review__actions">
        <button
          type="button"
          className="memory-review__btn memory-review__btn--primary"
          data-testid="conflict-keep-new"
          disabled={busy}
          onClick={() => void onResolve(item.memory_id, 'keep_new', existing?.memory_id)}
        >
          Ficar com a nova
        </button>
        <button
          type="button"
          className="memory-review__btn"
          data-testid="conflict-keep-old"
          disabled={busy}
          onClick={() => void onResolve(item.memory_id, 'keep_old', existing?.memory_id)}
        >
          Ficar com a antiga
        </button>
        <button
          type="button"
          className="memory-review__btn"
          data-testid="conflict-keep-both"
          disabled={busy}
          onClick={() => void onResolve(item.memory_id, 'keep_both', existing?.memory_id)}
        >
          Manter as duas
        </button>
      </div>
    </article>
  )
}
