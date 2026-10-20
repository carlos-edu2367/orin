import type { QueuedInput } from '../../api/sessionTools'

/**
 * Messages typed while a turn runs (W7). The running turn reads them between steps; whatever is left when it ends
 * becomes the next turn. "Enviar agora" stops the turn and sends that message at once.
 */
export function QueuePanel({ items, busy = false, onRemove, onSendNow }: {
  items: readonly QueuedInput[]
  busy?: boolean
  onRemove: (queueId: string) => void
  onSendNow: (item: QueuedInput) => void
}) {
  if (items.length === 0) return null
  return (
    <section className="queue-panel" aria-label="Mensagens na fila">
      <header className="queue-panel__header">
        <span className="queue-panel__title">Na fila ({items.length})</span>
        <span className="queue-panel__hint">O agente lê estas mensagens entre um passo e outro.</span>
      </header>
      <ol className="queue-panel__list">
        {items.map((item) => (
          <li key={item.queueId} className="queue-panel__item">
            <p className="queue-panel__text">{item.content}</p>
            <div className="queue-panel__actions">
              <button type="button" className="ghost-button" disabled={busy} onClick={() => onSendNow(item)}>Enviar agora</button>
              <button type="button" className="ghost-button" disabled={busy} onClick={() => onRemove(item.queueId)}
                aria-label={`Remover da fila: ${item.content.slice(0, 60)}`}>Remover</button>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}
