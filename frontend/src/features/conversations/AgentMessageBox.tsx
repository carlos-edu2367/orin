import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import {
  listConversationAgentMessages,
  sendConversationAgentMessage,
  type AgentMessageState,
} from '../../api/conversations'
import { ApiError } from '../../api/errors'
import {
  hydrateAgentMessages,
  recordOptimisticAgentMessage,
  toVisualAgentMessageState,
  type AgentMessageItem,
  type AgentMessagesState,
} from './agentMessages'
import './agentMessages.css'

export type AgentMessageBoxProps = {
  conversationId: string
  agentId: string
  agentName?: string
  running?: boolean
  client?: ApiClient
  liveMessages?: readonly AgentMessageItem[]
  onMessageSent?: (item: { controlId: string; agentId: string; message: string }) => void
  onSendMessage?: (
    agentId: string,
    message: string,
  ) => Promise<{ control_id?: string; status?: string } | void>
  defaultOpen?: boolean
  compact?: boolean
}

const MERGE_RANK: Record<AgentMessageState, number> = {
  queued: 0,
  injected: 1,
  delivered: 2,
  forwarded: 2,
  expired: 2,
  stopped: 2,
}

function displayLabelForState(state: AgentMessageState): string {
  const visual = toVisualAgentMessageState(state)
  switch (visual) {
    case 'queued':
      return 'Na fila'
    case 'delivered':
      return 'Entregue'
    case 'forwarded':
      return 'Repassada ao Orin'
    case 'expired':
    case 'stopped':
      return 'Expirou'
  }
}

function badgeToneForState(state: AgentMessageState): 'queued' | 'delivered' | 'forwarded' | 'expired' {
  const visual = toVisualAgentMessageState(state)
  if (visual === 'stopped') return 'expired'
  return visual
}

function formatDeliveredTime(value: string | null): string {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  return parsed.toLocaleTimeString('pt-BR', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function mergeTwoItems(base: AgentMessageItem, incoming: AgentMessageItem): AgentMessageItem {
  const keepBase = MERGE_RANK[base.state] > MERGE_RANK[incoming.state]
  const nextState = keepBase ? base.state : incoming.state
  return {
    controlId: incoming.controlId,
    agentId: incoming.agentId || base.agentId,
    senderKind: incoming.senderKind || base.senderKind,
    senderAgentId: incoming.senderAgentId ?? base.senderAgentId,
    message: incoming.message || base.message,
    preview: incoming.preview || base.preview,
    state: nextState,
    visualState: toVisualAgentMessageState(nextState),
    createdAt: base.createdAt ?? incoming.createdAt,
    deliveredAt:
      nextState === 'delivered'
        ? incoming.deliveredAt ?? base.deliveredAt
        : base.deliveredAt ?? incoming.deliveredAt,
  }
}

export function AgentMessageBox({
  conversationId,
  agentId,
  agentName,
  running = true,
  client,
  liveMessages,
  onMessageSent,
  onSendMessage,
  defaultOpen = false,
  compact = false,
}: AgentMessageBoxProps) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [open, setOpen] = useState(defaultOpen)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [localState, setLocalState] = useState<AgentMessagesState>({})

  const labelName = (agentName ?? '').trim() || 'subagente'

  useEffect(() => {
    if (!conversationId || !agentId) return
    let cancelled = false
    listConversationAgentMessages(apiClient, conversationId, agentId)
      .then((response) => {
        if (cancelled) return
        setLocalState((prev) => hydrateAgentMessages(prev, agentId, response.messages))
      })
      .catch(() => {
        // Listing failure must not crash the card or panel; live SSE events still populate.
      })
    return () => {
      cancelled = true
    }
  }, [apiClient, conversationId, agentId])

  const messages = useMemo(() => {
    const localItems = localState[agentId] ?? []
    const combined = new Map<string, AgentMessageItem>()
    const order: string[] = []
    for (const item of localItems) {
      combined.set(item.controlId, item)
      order.push(item.controlId)
    }
    for (const item of liveMessages ?? []) {
      const existing = combined.get(item.controlId)
      if (!existing) {
        combined.set(item.controlId, item)
        order.push(item.controlId)
      } else {
        combined.set(item.controlId, mergeTwoItems(existing, item))
      }
    }
    return order
      .map((id) => combined.get(id))
      .filter((item): item is AgentMessageItem => Boolean(item))
  }, [localState, agentId, liveMessages])

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const clean = draft.trim()
    if (!clean || sending || !running) return
    setSending(true)
    setError(null)
    try {
      const result = onSendMessage
        ? await onSendMessage(agentId, clean)
        : await sendConversationAgentMessage(apiClient, conversationId, agentId, clean)
      const controlId =
        result && typeof result.control_id === 'string' && result.control_id
          ? result.control_id
          : `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      setLocalState((prev) =>
        recordOptimisticAgentMessage(prev, {
          controlId,
          agentId,
          message: clean,
          senderKind: 'user',
        }),
      )
      onMessageSent?.({ controlId, agentId, message: clean })
      setDraft('')
      setOpen(false)
    } catch (caught: unknown) {
      if (
        caught instanceof ApiError &&
        caught.status === 409 &&
        caught.code.toUpperCase() === 'AGENT_NOT_RUNNING'
      ) {
        setError(`${labelName} já terminou.`)
      } else {
        setError('Não foi possível enviar a mensagem ao subagente.')
      }
    } finally {
      setSending(false)
    }
  }

  if (!running && messages.length === 0 && !error) {
    return null
  }

  return (
    <div
      className={compact ? 'agent-msg-box agent-msg-box--compact' : 'agent-msg-box'}
      data-testid={`agent-message-box-${agentId}`}
      onClick={(event) => event.stopPropagation()}
    >
      {running && !open && (
        <div className="agent-msg-box__actions">
          <button
            type="button"
            className="agent-msg-box__trigger"
            data-testid={`agent-message-open-${agentId}`}
            onClick={() => {
              setOpen(true)
              setError(null)
            }}
          >
            Mandar mensagem
          </button>
        </div>
      )}

      {running && open && (
        <form className="agent-msg-box__form" onSubmit={(event) => void handleSubmit(event)}>
          <textarea
            className="agent-msg-box__input"
            aria-label={`Mensagem para ${labelName}`}
            placeholder={`Envie uma orientação para ${labelName}...`}
            rows={2}
            value={draft}
            disabled={sending}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault()
                setOpen(false)
                return
              }
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void handleSubmit(event)
              }
            }}
          />
          <div className="agent-msg-box__form-buttons">
            <button
              type="button"
              className="agent-msg-box__btn"
              disabled={sending}
              onClick={() => setOpen(false)}
            >
              Cancelar
            </button>
            <button
              type="submit"
              className="agent-msg-box__btn agent-msg-box__btn--primary"
              disabled={sending || !draft.trim()}
            >
              {sending ? 'Enviando...' : 'Enviar'}
            </button>
          </div>
        </form>
      )}

      {error && (
        <p className="agent-msg-box__error" role="alert">
          {error}
        </p>
      )}

      {messages.length > 0 && (
        <ul className="agent-msg-box__list" aria-label={`Mensagens para ${labelName}`}>
          {messages.map((item) => {
            const stateLabel = displayLabelForState(item.state)
            const tone = badgeToneForState(item.state)
            const deliveredClock = item.state === 'delivered' ? formatDeliveredTime(item.deliveredAt) : ''
            return (
              <li key={item.controlId} className="agent-msg-box__item">
                <div className="agent-msg-box__item-head">
                  <span className="agent-msg-box__sender">
                    {item.senderKind === 'parent' ? 'Orin' : 'Você'}
                  </span>
                  <span className="agent-msg-box__status-wrap">
                    <span
                      className={`agent-msg-box__badge agent-msg-box__badge--${tone}`}
                      data-testid={`agent-message-status-${item.controlId}`}
                    >
                      {stateLabel}
                    </span>
                    {deliveredClock && (
                      <time
                        className="agent-msg-box__time"
                        dateTime={item.deliveredAt ?? undefined}
                        data-testid={`agent-message-delivered-time-${item.controlId}`}
                      >
                        {deliveredClock}
                      </time>
                    )}
                  </span>
                </div>
                <p className="agent-msg-box__text">{item.message || item.preview}</p>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
