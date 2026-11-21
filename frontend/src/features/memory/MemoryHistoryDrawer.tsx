import { useEffect, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import {
  getMemoryHistory,
  restoreMemory,
  type MemoryHistoryResponse,
} from '../../api/memory'
import './memoryReview.css'

const EVENT_LABELS: Record<string, string> = {
  created: 'Criada',
  updated: 'Atualizada',
  proposed: 'Proposta',
  promoted: 'Promovida',
  quarantined: 'Em quarentena',
  approved: 'Aprovada',
  rejected: 'Rejeitada',
  conflict: 'Conflito detectado',
  superseded: 'Superada',
  forgotten: 'Esquecida',
  restored: 'Restaurada',
  expired: 'Expirada',
}

function eventLabel(event: string): string {
  return EVENT_LABELS[event] ?? event
}

function formatDate(iso: string | null): string {
  if (!iso) return ''
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return iso
  return parsed.toLocaleString('pt-BR')
}

export type MemoryHistoryDrawerProps = {
  client: ApiClient
  memoryId: string | null
  onClose: () => void
  onRestored?: () => void
}

export function MemoryHistoryDrawer({
  client,
  memoryId,
  onClose,
  onRestored,
}: MemoryHistoryDrawerProps) {
  const [history, setHistory] = useState<MemoryHistoryResponse | null>(null)
  const [loadedMemoryId, setLoadedMemoryId] = useState<string | null>(null)
  const [restoring, setRestoring] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loading = Boolean(memoryId) && loadedMemoryId !== memoryId

  useEffect(() => {
    if (!memoryId) return
    let active = true
    getMemoryHistory(client, memoryId)
      .then((res) => {
        if (!active) return
        setHistory(res)
        setError(null)
      })
      .catch(() => {
        if (!active) return
        setHistory(null)
        setError('Não foi possível carregar o histórico desta memória.')
      })
      .finally(() => {
        if (active) setLoadedMemoryId(memoryId)
      })
    return () => {
      active = false
    }
  }, [client, memoryId])

  if (!memoryId) return null

  const activeHistory = history && history.memory_id === memoryId ? history : null
  const memory = activeHistory?.memory ?? null
  const isErasedTombstone =
    memory !== null && memory.status === 'deleted' && memory.fact.trim() === ''

  async function handleRestore() {
    if (!memoryId) return
    setRestoring(true)
    setError(null)
    try {
      await restoreMemory(client, memoryId)
      const refreshed = await getMemoryHistory(client, memoryId)
      setHistory(refreshed)
      onRestored?.()
    } catch (caught: unknown) {
      if (caught instanceof ApiError && caught.message) {
        setError(caught.message)
      } else {
        setError('Não foi possível restaurar esta memória.')
      }
    } finally {
      setRestoring(false)
    }
  }

  return (
    <div
      className="memory-history-drawer__backdrop"
      data-testid="memory-history-backdrop"
      onClick={onClose}
    >
      <aside
        className="memory-history-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Histórico da memória"
        data-testid="memory-history-drawer"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="memory-history-drawer__head">
          <h2 style={{ margin: 0, fontSize: '1.05rem' }}>Histórico da memória</h2>
          <button
            type="button"
            className="memory-review__btn"
            data-testid="memory-history-close"
            onClick={onClose}
          >
            Fechar
          </button>
        </header>

        {loading && <p className="code-settings__muted">Carregando histórico...</p>}
        {error && (
          <p role="alert" data-testid="memory-history-error">
            {error}
          </p>
        )}

        {memory && (
          <section className="memory-review__card" data-testid="memory-history-summary">
            <div className="memory-review__chips">
              <span className="memory-review__chip">Estado: {memory.status}</span>
              <span className="memory-review__chip">Escopo: {memory.scope_type}</span>
              {memory.agent_key && (
                <span className="memory-review__chip">Agente: {memory.agent_key}</span>
              )}
            </div>
            <p
              className={`memory-review__fact${isErasedTombstone ? ' memory-review__fact--erased' : ''}`}
            >
              {isErasedTombstone
                ? 'Texto apagado permanentemente ao esquecer esta memória.'
                : memory.fact}
            </p>

            {memory.can_restore ? (
              <div className="memory-review__actions">
                <button
                  type="button"
                  className="memory-review__btn memory-review__btn--primary"
                  data-testid="memory-history-restore"
                  disabled={restoring}
                  onClick={() => void handleRestore()}
                >
                  {restoring ? 'Restaurando...' : 'Restaurar'}
                </button>
              </div>
            ) : isErasedTombstone ? (
              <p className="memory-history-drawer__event-meta" data-testid="memory-history-erased-note">
                Memórias esquecidas têm o texto apagado (tombstone) e não podem ser restauradas.
              </p>
            ) : null}
          </section>
        )}

        {activeHistory && (
          <ol className="memory-history-drawer__timeline" data-testid="memory-history-events">
            {activeHistory.events.length === 0 ? (
              <li className="memory-history-drawer__event">
                Nenhum evento registrado para esta memória.
              </li>
            ) : (
              activeHistory.events.map((ev) => (
                <li
                  key={ev.id}
                  className="memory-history-drawer__event"
                  data-testid={`memory-history-event-${ev.event}`}
                >
                  <div className="memory-history-drawer__event-head">
                    <span>{eventLabel(ev.event)}</span>
                    <span className="memory-history-drawer__event-meta">{ev.event}</span>
                  </div>
                  <div className="memory-history-drawer__event-meta">
                    Por: {ev.actor}
                    {ev.created_at ? ` · ${formatDate(ev.created_at)}` : ''}
                  </div>
                  {ev.event !== 'forgotten' && ev.old_text && (
                    <div>
                      <strong>Antes:</strong> {ev.old_text}
                    </div>
                  )}
                  {ev.event !== 'forgotten' && ev.new_text && (
                    <div>
                      <strong>Depois:</strong> {ev.new_text}
                    </div>
                  )}
                  {ev.related_id && (
                    <div className="memory-history-drawer__event-meta">
                      Relacionada: <code>{ev.related_id}</code>
                    </div>
                  )}
                </li>
              ))
            )}
          </ol>
        )}
      </aside>
    </div>
  )
}
