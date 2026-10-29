import type { QueuedInput } from '../../api/sessionTools'

/**
 * Messages typed while a turn runs (W7). The running turn reads them between steps; whatever is left when it ends
 * becomes the next turn. "Enviar agora" stops the turn and sends that message at once.
 */
export function QueuePanel({ items, busy = false, onEdit, onRemove, onSendNow }: {
  items: readonly QueuedInput[]
  busy?: boolean
  onEdit?: (item: QueuedInput) => void
  onRemove: (queueId: string) => void
  onSendNow: (item: QueuedInput) => void
}) {
  if (items.length === 0) return null
  return (
    <section className="queue-panel" aria-label="Mensagens na fila" data-testid="compact-queue-bar">
      <header className="queue-panel__header">
        <span className="queue-panel__title">Na fila ({items.length})</span>
        <span className="queue-panel__hint">O agente lê estas mensagens entre um passo e outro.</span>
      </header>
      <ol className="queue-panel__list">
        {items.map((item) => (
          <li key={item.queueId} className="queue-panel__item">
            <p className="queue-panel__text">
              <svg
                className="queue-panel__clock"
                aria-hidden="true"
                viewBox="0 0 16 16"
                width="14"
                height="14"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="8" cy="8" r="6" />
                <path d="M8 4.6V8l2.3 1.5" />
              </svg>
              <span className="queue-panel__sequence-label">Na sequência:</span>{' '}
              <span className="queue-panel__item-content">{item.content}</span>
            </p>
            <div className="queue-panel__actions">
              <button
                type="button"
                className="ghost-button queue-panel__edit"
                data-testid={`queue-edit-button-${item.queueId}`}
                aria-label={`Editar da fila: ${item.content.slice(0, 60)}`}
                disabled={busy}
                onClick={() => { if (!busy) onEdit?.(item) }}
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 16 16"
                  width="13"
                  height="13"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M11.5 2.5a1.8 1.8 0 0 1 2.5 2.5L5.2 13.8 2 14.5l.7-3.2 8.8-8.8Z" />
                </svg>
              </button>
              <button type="button" className="ghost-button queue-panel__send-now" disabled={busy} onClick={() => { if (!busy) onSendNow(item) }}>Enviar agora</button>
              <button type="button" className="ghost-button queue-panel__remove" disabled={busy} onClick={() => { if (!busy) onRemove(item.queueId) }}
                aria-label={`Remover da fila: ${item.content.slice(0, 60)}`}>Remover</button>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}
