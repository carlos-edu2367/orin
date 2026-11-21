import { useCallback, useEffect, useState, type ReactNode } from 'react'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import {
  approveMemory,
  listMemoryReview,
  rejectMemory,
  resolveMemoryConflict,
  restoreMemory,
  type ConflictResolution,
  type MemoryReviewCounts,
  type MemoryReviewItem,
} from '../../api/memory'
import { MemoryConflictCard } from './MemoryConflictCard'
import { MemoryHistoryDrawer } from './MemoryHistoryDrawer'
import './memoryReview.css'

export type MemoryLifecycleTab = 'active' | 'pending' | 'history'

const STATUS_LABELS: Record<string, string> = {
  active: 'Ativa',
  proposed: 'Proposta',
  quarantined: 'Quarentena',
  superseded: 'Superada',
  invalid: 'Invalidada',
  deleted: 'Esquecida',
}

const SOURCE_LABELS: Record<string, string> = {
  user: 'Usuário',
  agent: 'Agente',
  tool: 'Ferramenta',
  web: 'Web',
  file: 'Arquivo',
}

const EMPTY_COUNTS: MemoryReviewCounts = {
  active: 0,
  pending: 0,
  proposed: 0,
  quarantined: 0,
  conflicts: 0,
  history: 0,
}

export type MemoryReviewTabProps = {
  client: ApiClient
  projectId?: string
  initialTab?: MemoryLifecycleTab
  /** Optional custom view rendered for the "Ativas" tab when no agent filter is active. */
  activeContent?: ReactNode
  /** Called whenever a review mutation changes active memories (approve, resolve, restore). */
  onMutated?: () => void
}

export function MemoryReviewTab({
  client,
  projectId,
  initialTab = 'active',
  activeContent,
  onMutated,
}: MemoryReviewTabProps) {
  const [tab, setTab] = useState<MemoryLifecycleTab>(initialTab)
  const [agentKey, setAgentKey] = useState<string>('')
  const [items, setItems] = useState<MemoryReviewItem[]>([])
  const [counts, setCounts] = useState<MemoryReviewCounts>(EMPTY_COUNTS)
  const [agentKeys, setAgentKeys] = useState<string[]>([])
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [historyMemoryId, setHistoryMemoryId] = useState<string | null>(null)

  const requestKey = `${projectId ?? ''}::${agentKey}::${tab}`
  const loading = loadedKey !== requestKey

  const load = useCallback(async () => {
    try {
      const result = await listMemoryReview(client, {
        projectId,
        agentKey: agentKey || undefined,
        status: tab,
      })
      setItems(result.items)
      setCounts(result.counts)
      setAgentKeys(result.agent_keys)
      setError(null)
    } catch {
      setError('Não foi possível carregar as memórias desta aba.')
    } finally {
      setLoadedKey(requestKey)
    }
  }, [client, projectId, agentKey, tab, requestKey])

  useEffect(() => {
    let active = true
    listMemoryReview(client, {
      projectId,
      agentKey: agentKey || undefined,
      status: tab,
    })
      .then((result) => {
        if (!active) return
        setItems(result.items)
        setCounts(result.counts)
        setAgentKeys(result.agent_keys)
        setError(null)
      })
      .catch(() => {
        if (!active) return
        setError('Não foi possível carregar as memórias desta aba.')
      })
      .finally(() => {
        if (active) setLoadedKey(requestKey)
      })
    return () => {
      active = false
    }
  }, [client, projectId, agentKey, tab, requestKey])

  const handleApprove = async (memoryId: string) => {
    setBusyId(memoryId)
    setError(null)
    setStatusMessage(null)
    try {
      await approveMemory(client, memoryId)
      setStatusMessage('Memória aprovada e ativada.')
      await load()
      onMutated?.()
    } catch (caught: unknown) {
      setError(
        caught instanceof ApiError && caught.message
          ? caught.message
          : 'Não foi possível aprovar a memória.',
      )
    } finally {
      setBusyId(null)
    }
  }

  const handleResolveConflict = async (
    memoryId: string,
    resolution: ConflictResolution,
    conflictingMemoryId?: string,
  ) => {
    setBusyId(memoryId)
    setError(null)
    setStatusMessage(null)
    try {
      await resolveMemoryConflict(client, memoryId, resolution, conflictingMemoryId)
      const label =
        resolution === 'keep_new'
          ? 'Conflito resolvido: mantida a memória nova.'
          : resolution === 'keep_old'
            ? 'Conflito resolvido: mantida a memória antiga.'
            : 'Conflito resolvido: ambas as memórias foram mantidas ativas.'
      setStatusMessage(label)
      await load()
      onMutated?.()
    } catch (caught: unknown) {
      setError(
        caught instanceof ApiError && caught.message
          ? caught.message
          : 'Não foi possível resolver o conflito.',
      )
    } finally {
      setBusyId(null)
    }
  }

  const handleReject = async (memoryId: string) => {
    setBusyId(memoryId)
    setError(null)
    setStatusMessage(null)
    try {
      await rejectMemory(client, memoryId)
      setStatusMessage('Memória rejeitada.')
      await load()
      onMutated?.()
    } catch (caught: unknown) {
      setError(
        caught instanceof ApiError && caught.message
          ? caught.message
          : 'Não foi possível rejeitar a memória.',
      )
    } finally {
      setBusyId(null)
    }
  }

  const handleRestore = async (memoryId: string) => {
    setBusyId(memoryId)
    setError(null)
    setStatusMessage(null)
    try {
      await restoreMemory(client, memoryId)
      setStatusMessage('Memória restaurada para o estado ativo.')
      await load()
      onMutated?.()
    } catch (caught: unknown) {
      setError(
        caught instanceof ApiError && caught.message
          ? caught.message
          : 'Não foi possível restaurar a memória.',
      )
    } finally {
      setBusyId(null)
    }
  }

  const useCustomActiveSlot = tab === 'active' && Boolean(activeContent) && !agentKey

  return (
    <div className="memory-review" data-testid="memory-review-container">
      <div className="memory-review__toolbar">
        <div
          className="memory-review__tabs"
          role="tablist"
          aria-label="Estado da memória"
        >
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'active'}
            className={`memory-review__tab${tab === 'active' ? ' is-active' : ''}`}
            data-testid="memory-lifecycle-tab-active"
            onClick={() => setTab('active')}
          >
            <span>Ativas</span>
            {counts.active > 0 && (
              <span className="memory-review__badge">{counts.active}</span>
            )}
          </button>

          <button
            type="button"
            role="tab"
            aria-selected={tab === 'pending'}
            className={`memory-review__tab${tab === 'pending' ? ' is-active' : ''}`}
            data-testid="memory-lifecycle-tab-pending"
            onClick={() => setTab('pending')}
          >
            <span>Para revisar</span>
            {counts.pending > 0 && (
              <span className="memory-review__badge" data-testid="memory-pending-count">
                {counts.pending}
              </span>
            )}
          </button>

          <button
            type="button"
            role="tab"
            aria-selected={tab === 'history'}
            className={`memory-review__tab${tab === 'history' ? ' is-active' : ''}`}
            data-testid="memory-lifecycle-tab-history"
            onClick={() => setTab('history')}
          >
            <span>Histórico</span>
            {counts.history > 0 && (
              <span className="memory-review__badge">{counts.history}</span>
            )}
          </button>
        </div>

        <label className="memory-review__agent-filter">
          <span>Agente</span>
          <select
            aria-label="Filtrar por agente"
            data-testid="memory-agent-filter"
            value={agentKey}
            onChange={(event) => setAgentKey(event.target.value)}
          >
            <option value="">Todos os agentes</option>
            {agentKeys.map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </select>
        </label>
      </div>

      {statusMessage && (
        <p role="status" data-testid="memory-review-status">
          {statusMessage}
        </p>
      )}
      {error && (
        <p role="alert" data-testid="memory-review-error">
          {error}
        </p>
      )}

      {useCustomActiveSlot ? (
        activeContent
      ) : loading && items.length === 0 ? (
        <p className="code-settings__muted" data-testid="memory-review-loading">
          Carregando memórias...
        </p>
      ) : items.length === 0 ? (
        <p className="memory-empty-state" data-testid="memory-review-empty">
          {tab === 'pending'
            ? 'Nenhuma memória aguardando revisão.'
            : tab === 'history'
              ? 'Nenhuma memória arquivada, superada ou esquecida.'
              : 'Nenhuma memória ativa para o filtro selecionado.'}
        </p>
      ) : (
        <div className="memory-review__list" data-testid="memory-review-list">
          {items.map((item) => {
            const isBusy = busyId === item.memory_id
            if (tab === 'pending' && item.review_kind === 'conflict' && item.conflicting_memory) {
              return (
                <MemoryConflictCard
                  key={item.memory_id}
                  item={item}
                  busy={isBusy}
                  onResolve={handleResolveConflict}
                  onOpenHistory={setHistoryMemoryId}
                />
              )
            }

            const isErasedTombstone =
              item.status === 'deleted' && item.fact.trim() === ''

            return (
              <article
                key={item.memory_id}
                className="memory-review__card"
                data-testid={`memory-review-item-${item.memory_id}`}
              >
                <div className="memory-review__card-head">
                  <div className="memory-review__chips">
                    <span
                      className={`memory-review__chip${
                        item.status === 'quarantined' ? ' memory-review__chip--quarantined' : ''
                      }`}
                    >
                      {STATUS_LABELS[item.status] ?? item.status}
                    </span>
                    <span className="memory-review__chip">
                      Origem: {SOURCE_LABELS[item.source_kind] ?? item.source_kind}
                    </span>
                    {item.agent_key && (
                      <span className="memory-review__chip">Agente: {item.agent_key}</span>
                    )}
                  </div>
                </div>

                <p
                  className={`memory-review__fact${
                    isErasedTombstone ? ' memory-review__fact--erased' : ''
                  }`}
                >
                  {isErasedTombstone
                    ? 'Memória esquecida (texto apagado para preservar privacidade).'
                    : item.fact}
                </p>

                <div className="memory-review__actions">
                  {tab === 'pending' && (
                    <>
                      <button
                        type="button"
                        className="memory-review__btn memory-review__btn--primary"
                        data-testid={`memory-approve-${item.memory_id}`}
                        disabled={isBusy}
                        onClick={() => void handleApprove(item.memory_id)}
                      >
                        Aprovar
                      </button>
                      <button
                        type="button"
                        className="memory-review__btn memory-review__btn--danger"
                        data-testid={`memory-reject-${item.memory_id}`}
                        disabled={isBusy}
                        onClick={() => void handleReject(item.memory_id)}
                      >
                        Rejeitar
                      </button>
                    </>
                  )}

                  {tab === 'history' && item.can_restore && (
                    <button
                      type="button"
                      className="memory-review__btn memory-review__btn--primary"
                      data-testid={`memory-restore-${item.memory_id}`}
                      disabled={isBusy}
                      onClick={() => void handleRestore(item.memory_id)}
                    >
                      Restaurar
                    </button>
                  )}

                  <button
                    type="button"
                    className="memory-review__btn"
                    data-testid={`memory-history-btn-${item.memory_id}`}
                    disabled={isBusy}
                    onClick={() => setHistoryMemoryId(item.memory_id)}
                  >
                    Histórico
                  </button>
                </div>
              </article>
            )
          })}
        </div>
      )}

      <MemoryHistoryDrawer
        client={client}
        memoryId={historyMemoryId}
        onClose={() => setHistoryMemoryId(null)}
        onRestored={() => {
          void load()
          onMutated?.()
        }}
      />
    </div>
  )
}
