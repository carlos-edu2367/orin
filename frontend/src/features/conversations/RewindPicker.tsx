import { useEffect, useMemo, useRef, useState } from 'react'
import type { FileHistoryItem } from '../../api/sessionTools'
import { useKeybindingHandlers } from '../keybindings/engine'

export type RewindMode = 'code' | 'conversation' | 'both'
export type RewindableMessage = { messageId: string; content: string; createdAt?: string | null }

/**
 * The per-message rewind picker (W7, `/rewind` or Esc Esc): choose one of the person's messages, then restore the
 * files as they were before it, go back in the conversation to it (a new conversation; the original stays), or both.
 */
export function RewindPicker({ messages, history, busy = false, error = null, initialMessageId = null, unbackable = [], unbackableFor = null, onRewind, onClose }: {
  messages: readonly RewindableMessage[]
  history: readonly FileHistoryItem[]
  busy?: boolean
  error?: string | null
  /** W7: the message to start on (a turn's "voltar a este ponto"); the newest otherwise. */
  initialMessageId?: string | null
  /** W7: after a blocked code rewind, the files whose only problem is their size (no undo copy is possible). */
  unbackable?: readonly string[]
  /** The message whose rewind named `unbackable`: the offer holds only for that message. */
  unbackableFor?: string | null
  onRewind: (messageId: string, mode: RewindMode, forceUnbacked?: readonly string[]) => void
  onClose: () => void
}) {
  const newestFirst = useMemo(() => [...messages].reverse(), [messages])
  const [selected, setSelected] = useState(() => Math.max(0, newestFirst.findIndex((item) => item.messageId === initialMessageId)))
  const [lastMode, setLastMode] = useState<RewindMode>('code')
  const listRef = useRef<HTMLOListElement>(null)
  const current = newestFirst[selected] ?? null
  // The files a code rewind restores: every file a message from the chosen one onwards changed.
  const changed = useMemo(() => {
    if (!current) return [] as string[]
    const order = messages.map((item) => item.messageId)
    const from = order.indexOf(current.messageId)
    const files = new Set<string>()
    for (const item of history) {
      if (item.rewoundAt) continue
      if (order.indexOf(item.messageId) >= from) item.files.forEach((file) => files.add(file))
    }
    return [...files].sort()
  }, [current, history, messages])

  useKeybindingHandlers('Select', {
    'select:next': () => setSelected((index) => Math.min(newestFirst.length - 1, index + 1)),
    'select:previous': () => setSelected((index) => Math.max(0, index - 1)),
    'select:cancel': () => onClose(),
  })
  useEffect(() => { listRef.current?.focus() }, [])

  return (
    <div className="command-info__backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="command-info rewind-picker" role="dialog" aria-modal="true" aria-labelledby="rewind-title">
        <header className="command-info__header">
          <h2 id="rewind-title">Voltar a uma mensagem</h2>
          <button type="button" className="ghost-button" onClick={onClose}>Fechar</button>
        </header>
        {newestFirst.length === 0 ? (
          <p className="rewind-picker__empty">Ainda não há mensagens para voltar.</p>
        ) : (
          <>
            <ol ref={listRef} className="rewind-picker__list" role="listbox" aria-label="Suas mensagens" tabIndex={0}
              aria-activedescendant={current ? `rewind-${current.messageId}` : undefined}>
              {newestFirst.map((item, index) => (
                <li key={item.messageId} id={`rewind-${item.messageId}`} role="option" aria-selected={index === selected}
                  className={`rewind-picker__item${index === selected ? ' is-selected' : ''}`}
                  onMouseDown={(event) => { event.preventDefault(); setSelected(index) }}>
                  <span className="rewind-picker__text">{item.content || '(mensagem sem texto)'}</span>
                </li>
              ))}
            </ol>
            <div className="rewind-picker__details" aria-live="polite">
              {changed.length === 0
                ? <p>Nenhum arquivo mudou depois desta mensagem.</p>
                : <p>{changed.length === 1 ? '1 arquivo mudou' : `${changed.length} arquivos mudaram`} depois desta mensagem: {changed.slice(0, 4).join(', ')}{changed.length > 4 ? '…' : ''}</p>}
              {error && <p className="rewind-picker__error" role="alert">{error}</p>}
              {unbackable.length > 0 && current && current.messageId === unbackableFor && (
                <div className="rewind-picker__unbacked">
                  <p>Estes arquivos ficaram grandes demais para guardar uma cópia; se você restaurar mesmo assim, o conteúdo
                    atual deles se perde e não dá para desfazer:</p>
                  <ul>{unbackable.map((path) => <li key={path}><code>{path}</code></li>)}</ul>
                  <button type="button" className="button w7-danger-button" disabled={busy}
                    onClick={() => onRewind(current.messageId, lastMode, unbackable)}>Restaurar mesmo assim</button>
                </div>
              )}
            </div>
            <div className="rewind-picker__actions">
              <button type="button" className="ghost-button rewind-picker__main" disabled={busy || !current || changed.length === 0}
                onClick={() => { if (current) { setLastMode('code'); onRewind(current.messageId, 'code') } }}>Restaurar o código</button>
              <button type="button" className="ghost-button" disabled={busy || !current}
                onClick={() => current && onRewind(current.messageId, 'conversation')}>Voltar a conversa</button>
              <button type="button" className="ghost-button" disabled={busy || !current || changed.length === 0}
                onClick={() => { if (current) { setLastMode('both'); onRewind(current.messageId, 'both') } }}>Os dois</button>
            </div>
            <p className="rewind-picker__note">Voltar a conversa abre uma conversa nova a partir desta mensagem; a original continua como está.</p>
          </>
        )}
      </section>
    </div>
  )
}
