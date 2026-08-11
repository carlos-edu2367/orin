import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { createBrowserApiClient } from '../../api/client'
import {
  cancelConversation, getConversation, listConversations, sendConversationMessage, streamConversationEvents,
  type Conversation, type ConversationMessage,
} from '../../api/conversations'
import { ApiError } from '../../api/errors'
import { CommandPalette } from '../../components/CommandPalette'
import { Brand } from '../../components/Brand'
import { OverviewPanel } from '../overview/OverviewPanel'
import { WorkspaceNavigation } from '../projects/WorkspaceNavigation'
import { ActivityStream } from './ActivityStream'
import { AgentPulse, modeFromEvents } from './AgentPulse'
import { Composer } from './Composer'
import { MarkdownMessage } from './MarkdownMessage'
import { TurnTimeline } from './TurnTimeline'
import { WorkspaceFilePreview, type WorkspaceFileReference } from './WorkspaceFileCard'
import { activityReducer, createActivityState } from './activityReducer'
import type { ConversationActivityEvent } from './activityTypes'
import { buildMessageTimelines } from './turnTimelineFold'

const RUNNING_STATES = new Set(['queued', 'starting', 'running', 'streaming', 'cancelling'])

/**
 * The conversation itself.
 *
 * Messages and the agent's activity share one column and one scroll, because
 * what the agent did is part of the answer, not a side panel. The live text is
 * assembled from the stream so a long reply appears as it is written, and the
 * durable snapshot reconciles it whenever the turn settles.
 */
export function ChatPage() {
  const { conversationId = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const client = useMemo(() => createBrowserApiClient(), [])
  const reduced = useReducedMotion()

  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [chats, setChats] = useState<Array<{ conversation_id: string; title: string; state: string }>>([])
  const [message, setMessage] = useState('')
  const [pendingUserMessage, setPendingUserMessage] = useState<ConversationMessage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [stopping, setStopping] = useState(false)
  const [atBottom, setAtBottom] = useState(true)
  const [activity, dispatch] = useReducer(activityReducer, undefined, createActivityState)
  const [previewReference, setPreviewReference] = useState<WorkspaceFileReference | null>(null)
  const closePreview = useCallback(() => setPreviewReference(null), [])

  const cursorRef = useRef('0')
  const scrollRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)
  const showOverview = location.pathname.endsWith('/overview')

  const loadSnapshot = useCallback(async (resetActivity: boolean) => {
    const next = await getConversation(client, conversationId)
    setConversation(next)
    if (resetActivity) {
      cursorRef.current = next.activity_cursor
      dispatch({ type: 'snapshot', events: next.activities, cursor: next.activity_cursor })
    }
    return next
  }, [client, conversationId])

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    let attempts = 0
    const controller = new AbortController()

    const reconnect = async (): Promise<void> => {
      if (cancelled) return
      dispatch({ type: 'connection', connection: 'connecting' })
      try {
        await streamConversationEvents(client, conversationId, cursorRef.current, {
          onEvent: (event) => {
            cursorRef.current = event.cursor
            dispatch({ type: 'event', event })
            const terminalState = terminalConversationState(event)
            if (terminalState) {
              setConversation((current) => current === null ? current : {
                ...current,
                state: terminalState,
                turns: current.turns.map((turn) => turn.turn_id === event.turnId
                  ? { ...turn, state: terminalState, finished_at: turn.finished_at ?? event.occurredAt ?? null }
                  : turn),
              })
              void loadSnapshot(false).catch(() => undefined)
            }
          },
          onCursor: (cursor) => {
            cursorRef.current = cursor
            dispatch({ type: 'cursor', cursor })
          },
        }, controller.signal)
        if (cancelled) return
        attempts = 0
        dispatch({ type: 'connection', connection: 'live' })
        await loadSnapshot(false)
        timer = window.setTimeout(() => { void reconnect() }, 250)
      } catch (caught) {
        if (cancelled || controller.signal.aborted) return
        if (isResyncError(caught)) {
          dispatch({ type: 'resync' })
          cursorRef.current = '0'
          try { await loadSnapshot(true) } catch { setError('Não foi possível ressincronizar esta conversa.') }
          timer = window.setTimeout(() => { void reconnect() }, 250)
          return
        }
        attempts = Math.min(attempts + 1, 5)
        dispatch({ type: 'connection', connection: 'degraded' })
        try { await loadSnapshot(false) } catch { /* the retry below is the recovery */ }
        timer = window.setTimeout(() => { void reconnect() }, Math.min(5000, 400 * 2 ** attempts))
      }
    }

    const start = async () => {
      try {
        await loadSnapshot(true)
        const list = await listConversations(client)
        if (!cancelled) setChats(list.items)
        void reconnect()
      } catch {
        if (!cancelled) setError('Não foi possível carregar esta conversa.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void start()

    return () => {
      cancelled = true
      controller.abort()
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [client, conversationId, loadSnapshot])

  // Streamed text belongs to the assistant message the deltas name; the durable
  // snapshot wins as soon as it catches up, so a reload shows the same text.
  const streamedByMessage = useMemo(() => {
    const map = new Map<string, string>()
    for (const event of activity.events) {
      if (event.type !== 'assistant.delta' || !event.messageId || !event.content) continue
      map.set(event.messageId, (map.get(event.messageId) ?? '') + event.content)
    }
    return map
  }, [activity.events])

  const messages = useMemo(() => {
    const base = (conversation?.messages ?? []).map((item) => {
      if (item.role !== 'assistant' || item.content) return item
      const streamed = streamedByMessage.get(item.message_id)
      return streamed ? { ...item, content: streamed } : item
    })
    // The optimistic echo is dropped as soon as the durable snapshot contains
    // the same text, so a re-render can never show the message twice.
    const alreadyDurable = pendingUserMessage !== null
      && base.some((item) => item.role === 'user' && item.content === pendingUserMessage.content)
    return pendingUserMessage && !alreadyDurable ? [...base, pendingUserMessage] : base
  }, [conversation?.messages, streamedByMessage, pendingUserMessage])

  // Each assistant message gets its own text/action timeline, folded from the
  // turn it belongs to; a turn no message could claim (still running before
  // its first delta, or older than the 500-event window) stays visible
  // through the flat stream below instead of disappearing.
  const { timelines: timelinesByMessage, claimedTurnIds } = useMemo(
    () => buildMessageTimelines(messages, activity.events),
    [messages, activity.events],
  )
  const unclaimedEvents = useMemo(
    () => activity.events.filter((event) => !claimedTurnIds.has(event.turnId ?? '')),
    [activity.events, claimedTurnIds],
  )

  const running = RUNNING_STATES.has(conversation?.state ?? '')
  const mode = useMemo(() => modeFromEvents(activity.events, conversation?.state ?? 'queued'), [activity.events, conversation?.state])

  useEffect(() => {
    if (!pinnedRef.current) return
    const element = scrollRef.current
    if (!element) return
    // jsdom has no scrollTo; assigning scrollTop is the portable fallback.
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: element.scrollHeight, behavior: reduced ? 'auto' : 'smooth' })
    else element.scrollTop = element.scrollHeight
  }, [messages, activity.events.length, reduced])

  async function submit() {
    const text = message.trim()
    if (!text || running) return
    setMessage('')
    setError(null)
    setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: text, status: 'completed', retryable: false })
    try {
      await sendConversationMessage(client, conversationId, text)
      await loadSnapshot(false)
      void listConversations(client).then((value) => setChats(value.items)).catch(() => undefined)
    } catch {
      setMessage(text)
      setPendingUserMessage(null)
      setError('Não foi possível enviar. Tente novamente.')
    }
  }

  async function stop() {
    if (stopping) return
    setStopping(true)
    try {
      await cancelConversation(client, conversationId)
      await loadSnapshot(false)
    } catch {
      setError('Não foi possível parar a execução.')
    } finally {
      setStopping(false)
    }
  }

  return (
    <main className="chat">
      <header className="chat__bar">
        <Brand to="/" />
        <div className="chat__title">
          <h1>{conversation?.title ?? 'Conversa'}</h1>
          {conversation && <span className="chat__model">{conversation.provider} · {conversation.model_id}</span>}
        </div>
        <div className="chat__bar-actions">
          <button
            type="button"
            className={showOverview ? 'ghost-button is-active' : 'ghost-button'}
            aria-pressed={showOverview}
            onClick={() => navigate(showOverview ? `/chats/${conversationId}` : `/chats/${conversationId}/overview`)}
          >
            Visão geral
          </button>
          <CommandPalette conversations={chats} />
          <Link className="ghost-button" to="/settings" aria-label="Abrir Settings">Settings</Link>
        </div>
      </header>

      <aside className="workspace-navigation">
        <WorkspaceNavigation client={client} onChatsChange={setChats} />
      </aside>

      <div className="chat__body">
        <div className="chat__scroll" ref={scrollRef} onScroll={(event) => {
          const element = event.currentTarget
          const nextAtBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 120
          pinnedRef.current = nextAtBottom
          setAtBottom((current) => current === nextAtBottom ? current : nextAtBottom)
        }}>
          <div className="chat__thread">
            {loading && <p className="chat__placeholder" role="status">Carregando conversa…</p>}
            {!loading && messages.length === 0 && <p className="chat__placeholder">Esta conversa ainda não tem mensagens.</p>}

            {messages.map((item) => {
              const timeline = item.role === 'assistant' ? timelinesByMessage.get(item.message_id) : undefined
              return (
                <motion.article
                  key={item.message_id}
                  className={`bubble bubble--${item.role}`}
                  initial={reduced ? false : { opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.24, ease: [0.22, 0.61, 0.36, 1] }}
                >
                  {timeline
                    ? <TurnTimeline items={timeline} conversationId={conversationId} client={client} onPreview={setPreviewReference} />
                    : item.role === 'assistant' && item.content
                      ? <MarkdownMessage content={item.content} conversationId={conversationId} client={client} onPreview={setPreviewReference} />
                      : <p>{item.content || placeholderFor(item)}</p>}
                  {item.retryable && <span className="bubble__retry">Você pode reenviar esta mensagem.</span>}
                </motion.article>
              )
            })}

            <ActivityStream events={unclaimedEvents} />

            <AnimatePresence>
              {running && (
                <motion.div
                  key="pulse"
                  initial={reduced ? false : { opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <AgentPulse mode={mode} state={activity.events.at(-1)?.state ?? 'working'} />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>

        <AnimatePresence>
          {showOverview && conversation && (
            <OverviewPanel
              conversationId={conversationId}
              client={client}
              liveEvents={activity.events}
              onClose={() => navigate(`/chats/${conversationId}`)}
            />
          )}
        </AnimatePresence>
      </div>

      {previewReference && (
        <WorkspaceFilePreview
          reference={previewReference}
          client={client}
          onClose={closePreview}
        />
      )}

      <footer className="chat__foot" data-testid="chat-composer" data-at-bottom={atBottom}>
        {activity.connection === 'degraded' && (
          <p className="chat__connection" role="status">Atualizações em tempo real indisponíveis; tentando reconectar.</p>
        )}
        <Composer
          value={message}
          onChange={setMessage}
          onSubmit={() => void submit()}
          onStop={() => void stop()}
          running={running}
          error={error}
          hint={stopping ? 'parando…' : undefined}
          placeholder="Continue a conversa…"
        />
      </footer>
    </main>
  )
}

function placeholderFor(message: ConversationMessage): string {
  if (message.role === 'user') return 'Mensagem enviada.'
  switch (message.status) {
    case 'queued': return 'Na fila…'
    case 'streaming': return 'Trabalhando…'
    case 'failed': return 'Não foi possível concluir esta resposta.'
    case 'cancelled': return 'Execução cancelada por você.'
    default: return 'Sem texto nesta resposta.'
  }
}

function terminalConversationState(event: ConversationActivityEvent): 'completed' | 'failed' | 'cancelled' | null {
  if (event.type === 'turn.completed') return 'completed'
  if (event.type === 'turn.failed') return event.state === 'cancelled' ? 'cancelled' : 'failed'
  return null
}

function isResyncError(value: unknown): value is ApiError {
  if (!(value instanceof ApiError)) return false
  const code = value.code.toLowerCase().replaceAll('-', '_')
  return value.status === 409 || code === 'cursor_invalid' || code === 'invalid_cursor' || code === 'cursor_expired' || code === 'retention_expired' || code === 'sequence_gap'
}

export type { ConversationActivityEvent }
