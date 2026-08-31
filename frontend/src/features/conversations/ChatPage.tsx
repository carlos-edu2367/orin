import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { createBrowserApiClient } from '../../api/client'
import {
  cancelConversation, getConversation, listConversations, sendConversationMessage, streamConversationEvents,
  type CodeModeChoice, type Conversation, type ConversationMessage,
} from '../../api/conversations'
import { ApiError } from '../../api/errors'
import { approveMcpServer } from '../../api/mcp'
import { approvePlugin, listPluginCommands, type PluginCommand } from '../../api/plugins'
import { listSkills, type SkillSummary } from '../../api/skills'
import {
  PROVIDER_NAMES, getVisionModelSetting, listProviderModels,
  type ProviderModel, type ProviderName, type VisionModelSetting,
} from '../../api/providers'
import { deleteUpload, uploadFile } from '../../api/uploads'
import { getCodeModeSettings } from '../../api/codeMode'
import { attachWorkspaceFolder, detachWorkspaceFolder, inspectWorkspaceFolder, type WorkspaceState } from '../../api/workspace'
import { CommandPalette } from '../../components/CommandPalette'
import { Brand } from '../../components/Brand'
import { ModelPicker } from '../../components/ModelPicker'
import { OverviewPanel } from '../overview/OverviewPanel'
import { WorkspaceNavigation } from '../projects/WorkspaceNavigation'
import { ActivityStream } from './ActivityStream'
import { AgentPulse, modeFromEvents } from './AgentPulse'
import { attachmentNotice } from './attachmentNotice'
import { Composer } from './Composer'
import { ContextIndicator } from './ContextIndicator'
import { MarkdownMessage } from './MarkdownMessage'
import { MessageCommandChip } from './MessageCommandChip'
import { MessageAttachments } from './MessageAttachments'
import { TurnTimeline } from './TurnTimeline'
import { WorkspaceFilePreview, type WorkspaceFileReference } from './WorkspaceFileCard'
import { WorkspaceFolderButton } from './WorkspaceFolderButton'
import { activityReducer, createActivityState } from './activityReducer'
import type { ConversationActivityEvent } from './activityTypes'
import type { UserQuestionAnswer } from './UserQuestionCard'
import { buildMessageTimelines } from './turnTimelineFold'
import { useComposerAttachments } from './useComposerAttachments'

const TOOL_CAPABILITY_NAMES = new Set(['tools', 'tool_use', 'function_calling'])
const MAX_MESSAGE_CHARACTERS = 16000

/** Mirrors the worker's own `_model_calls_tools`: an unrefreshed catalog must not silently disable tools. */
function modelCallsTools(capabilities: string[]): boolean {
  return capabilities.length === 0 || capabilities.some((item) => TOOL_CAPABILITY_NAMES.has(item.toLowerCase()))
}

/**
 * Mirrors `choose_vision_model` (`src/agentos/reading/selection.py`): what the
 * person chose, then the conversation's own provider, then a local Ollama,
 * then anything else. Only used to name the model in the composer's notice.
 */
function chooseVisionModel(candidates: ProviderModel[], turnProvider: string, override: VisionModelSetting | null): ProviderModel | null {
  if (candidates.length === 0) return null
  if (override?.provider && override.modelId) {
    const chosen = candidates.find((item) => item.provider === override.provider && item.model_id === override.modelId)
    if (chosen) return chosen
  }
  const sameProvider = candidates.find((item) => item.provider === turnProvider)
  if (sameProvider) return sameProvider
  const local = candidates.find((item) => item.provider === 'ollama')
  if (local) return local
  return candidates[0]
}

const MANAGED_WORKSPACE: WorkspaceState = { kind: 'managed', path: null, folderName: null, scope: 'chat', projectName: null }

const RUNNING_STATES = new Set(['queued', 'starting', 'running', 'streaming', 'cancelling'])

function asProviderName(value: string): ProviderName | null {
  return PROVIDER_NAMES.includes(value as ProviderName) ? value as ProviderName : null
}

function codeModeNotification(type: string): { title: string; body: string } | null {
  if (type === 'code_mode.completed') return { title: 'Modo Code concluído', body: 'A entrega foi validada e está pronta para sua revisão.' }
  if (type === 'code_mode.completed_with_caveats') return { title: 'Modo Code concluído com ressalvas', body: 'A entrega está pronta, mas há uma limitação documentada.' }
  if (type === 'code_mode.blocked') return { title: 'Modo Code bloqueado', body: 'O Orin encontrou um bloqueio que precisa da sua decisão.' }
  if (type === 'code_mode.decision_required') return { title: 'Decisão necessária no Modo Code', body: 'O Orin precisa da sua confirmação para continuar.' }
  return null
}

/**
 * The conversation itself.
 *
 * Messages and the agent's activity share one column and one scroll, because
 * what the agent did is part of the answer, not a side panel. The live text is
 * assembled from the stream so a long reply appears as it is written, and the
 * durable snapshot reconciles it whenever the turn settles.
 */
export function ChatPage() {
  const { conversationId = '', projectId } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const client = useMemo(() => createBrowserApiClient(), [])
  const reduced = useReducedMotion()

  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [chats, setChats] = useState<Array<{ conversation_id: string; title: string; state: string }>>([])
  const [message, setMessage] = useState('')
  const [codeMode, setCodeMode] = useState<CodeModeChoice>('auto')
  const [codeSystemNotifications, setCodeSystemNotifications] = useState(false)
  const { attachments, onAttach, onRemoveAttachment, canSend: attachmentsReady, readyUploadIds, reset: resetAttachments } = useComposerAttachments(client)
  const [pendingUserMessage, setPendingUserMessage] = useState<ConversationMessage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadFailure, setLoadFailure] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [stopping, setStopping] = useState(false)
  // A successful cancel receipt is authoritative even while a stale snapshot
  // is in flight. Without this small local overlay, the server's short
  // cancelling window could put the pulse back into “Pensando” indefinitely.
  const [cancelledTurnIds, setCancelledTurnIds] = useState<Set<string>>(() => new Set())
  const [atBottom, setAtBottom] = useState(true)
  const [newActivityCount, setNewActivityCount] = useState(0)
  const [activity, dispatch] = useReducer(activityReducer, undefined, createActivityState)
  const [previewReference, setPreviewReference] = useState<WorkspaceFileReference | null>(null)
  const closePreview = useCallback(() => setPreviewReference(null), [])
  // What the composer's attachment notice needs to know: the turn's own model
  // capabilities, and — only to name it in the notice — every vision-capable
  // model this user has plus the explicit override, the same inputs
  // `VisionModelSetting` already reads.
  const [turnModelCatalog, setTurnModelCatalog] = useState<ProviderModel | null>(null)
  const [visionCandidates, setVisionCandidates] = useState<ProviderModel[]>([])
  const [pluginCommands, setPluginCommands] = useState<PluginCommand[]>([])
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [visionSetting, setVisionSetting] = useState<VisionModelSetting | null>(null)
  const [chatProvider, setChatProvider] = useState<ProviderName | null>(null)
  const [chatModelId, setChatModelId] = useState('')
  const [chatModels, setChatModels] = useState<ProviderModel[]>([])
  const [chatModelsLoading, setChatModelsLoading] = useState(false)
  const [chatModelsFailed, setChatModelsFailed] = useState(false)
  const [streamedByMessage, setStreamedByMessage] = useState<Map<string, string>>(() => new Map())
  const [oversizedInput, setOversizedInput] = useState<{ text: string; attachments: string[] } | null>(null)
  const [sendingOversizedInput, setSendingOversizedInput] = useState(false)

  const cursorRef = useRef('0')
  const scrollRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)
  const observedContentRef = useRef(new Set<string>())
  const observedConversationRef = useRef(conversationId)
  const streamedDeltaIdsRef = useRef(new Set<string>())
  const streamedConversationRef = useRef(conversationId)

  const showOverview = location.pathname.endsWith('/overview')
  const conversationPath = projectId === undefined
    ? `/chats/${encodeURIComponent(conversationId)}`
    : `/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(conversationId)}`

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
            if (codeSystemNotifications && document.visibilityState !== 'visible' && window.orinDesktop?.notifyCodeMode) {
              const notification = codeModeNotification(event.type)
              if (notification) void window.orinDesktop.notifyCodeMode(notification)
            }
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
      setLoadFailure(null)
      try {
        await loadSnapshot(true)
        void listConversations(client).then((list) => { if (!cancelled) setChats(list.items) }).catch(() => undefined)
        void reconnect()
      } catch (caught) {
        if (!cancelled) setLoadFailure(conversationLoadHeadline(caught))
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
  }, [client, codeSystemNotifications, conversationId, loadAttempt, loadSnapshot])

  useEffect(() => {
    const controller = new AbortController()
    void getCodeModeSettings(client, controller.signal).then((settings) => {
      if (!controller.signal.aborted) setCodeSystemNotifications(settings.system_notifications)
    }).catch(() => undefined)
    return () => controller.abort()
  }, [client])

  // Fetched once, best-effort: every vision-capable model this user has, and
  // the explicit override, so the composer's notice can name the model that
  // would actually do the reading. A failure here must never block the chat
  // itself — it only means the notice falls back to "no vision model".
  useEffect(() => {
    const controller = new AbortController()
    Promise.allSettled(PROVIDER_NAMES.map((provider) => listProviderModels(client, provider, controller.signal)))
      .then((results) => {
        if (controller.signal.aborted) return
        const items = results.flatMap((result) => (result.status === 'fulfilled' ? result.value : []))
        setVisionCandidates(items.filter((item) => item.input_modalities.includes('image')))
      })
    getVisionModelSetting(client, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setVisionSetting(value) })
      .catch(() => { if (!controller.signal.aborted) setVisionSetting({ provider: null, modelId: null, mode: 'automatic' }) })
    return () => controller.abort()
  }, [client])

  // Fetched once, best-effort: slash invocations are a convenience only. A
  // failure here must never block an ordinary conversation.
  useEffect(() => {
    const controller = new AbortController()
    Promise.allSettled([
      listPluginCommands(client, controller.signal),
      listSkills(client, { limit: 100 }, controller.signal),
    ]).then(([commands, availableSkills]) => {
      if (controller.signal.aborted) return
      setPluginCommands(commands.status === 'fulfilled' ? commands.value : [])
      setSkills(availableSkills.status === 'fulfilled' ? availableSkills.value.items : [])
    })
    return () => controller.abort()
  }, [client])

  // The turn's own model capabilities, refetched whenever the conversation's
  // model changes. Best-effort: an unrefreshed or unreachable catalog leaves
  // this null, and the notice logic below treats that as "unknown" the same
  // way the worker's own capability check does.
  const provider = conversation?.provider
  const modelId = conversation?.model_id
  const effectiveChatProvider = chatProvider ?? asProviderName(provider ?? '')
  const effectiveChatModelId = chatProvider ? chatModelId : modelId ?? ''

  // Opening another conversation resets the picker, and the conversation's own
  // selection is adopted as soon as it loads. Both are derivations of props,
  // so they happen during render: an effect would paint one frame of the
  // previous conversation's model before correcting itself, and that flicker
  // sits in the most-used screen in the product.
  const [selectionFor, setSelectionFor] = useState('')
  const [renderedConversationId, setRenderedConversationId] = useState(conversationId)
  if (conversationId !== renderedConversationId) {
    setRenderedConversationId(conversationId)
    setSelectionFor('')
    setChatProvider(null)
    setChatModelId('')
    setChatModels([])
  } else if (conversation && conversation.conversation_id === conversationId && selectionFor !== conversationId) {
    const initialProvider = asProviderName(conversation.provider)
    if (initialProvider) {
      setSelectionFor(conversationId)
      setChatProvider(initialProvider)
      setChatModelId(conversation.model_id)
    }
  }

  // The catalog fetch is a subscription: the busy flags are raised during
  // render alongside the provider change that causes them, and every later
  // update happens in a promise callback.
  const [modelsFor, setModelsFor] = useState<string | null>(null)
  if (effectiveChatProvider && modelsFor !== effectiveChatProvider) {
    setModelsFor(effectiveChatProvider)
    setChatModelsLoading(true)
    setChatModelsFailed(false)
  }

  useEffect(() => {
    if (!effectiveChatProvider) return
    const controller = new AbortController()
    listProviderModels(client, effectiveChatProvider, controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return
        setChatModels(items)
        setChatModelId((current) => items.some((item) => item.model_id === current)
          ? current
          : items.find((item) => item.is_favorite)?.model_id ?? items[0]?.model_id ?? '')
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setChatModels([])
          setChatModelsFailed(true)
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setChatModelsLoading(false)
      })
    return () => controller.abort()
  }, [client, effectiveChatProvider])

  const changeChatProvider = useCallback((next: ProviderName) => {
    setChatProvider(next)
    setChatModelId('')
    setChatModels([])
  }, [])

  const chatSelection = useMemo(
    () => effectiveChatProvider && effectiveChatModelId ? { provider: effectiveChatProvider, model_id: effectiveChatModelId } : undefined,
    [effectiveChatModelId, effectiveChatProvider],
  )

  const sendChatMessage = useCallback(
    (text: string, attachments: string[] = []) => sendConversationMessage(client, conversationId, text, attachments, chatSelection, codeMode),
    [chatSelection, client, codeMode, conversationId],
  )

  useEffect(() => {
    if (!provider || !modelId || !PROVIDER_NAMES.includes(provider as ProviderName)) return
    const controller = new AbortController()
    listProviderModels(client, provider as ProviderName, controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return
        setTurnModelCatalog(items.find((item) => item.model_id === modelId) ?? null)
      })
      .catch(() => { if (!controller.signal.aborted) setTurnModelCatalog(null) })
    return () => controller.abort()
  }, [client, provider, modelId])
  // A stale entry from the previous model must never be read as this one's
  // capabilities while the new model's own fetch is still in flight.
  const turnModel = turnModelCatalog?.model_id === modelId ? turnModelCatalog : null

  const hasVisualAttachment = attachments.some((item) => item.kind === 'image' || item.kind === 'pdf')
  const selectedChatModel = chatModels.find((item) => item.model_id === effectiveChatModelId)
  const composerModel = selectedChatModel?.provider === effectiveChatProvider ? selectedChatModel : turnModel
  const visionModel = chooseVisionModel(visionCandidates, effectiveChatProvider ?? provider ?? '', visionSetting)
  const notice = attachmentNotice({
    hasVisualAttachment,
    modelSeesImages: composerModel?.input_modalities.includes('image') ?? false,
    modelCallsTools: composerModel ? modelCallsTools(composerModel.capabilities) : true,
    visionModelName: visionModel?.model_id ?? null,
  })

  // Keep the streamed text separate from the bounded activity feed. The feed
  // intentionally retains only its last 500 events, but a long assistant reply
  // can contain more than 500 deltas before the durable snapshot catches up.
  useEffect(() => {
    if (streamedConversationRef.current !== conversationId) {
      streamedConversationRef.current = conversationId
      streamedDeltaIdsRef.current = new Set()
      setStreamedByMessage(new Map())
      return
    }
    const unseen = activity.events.filter((event) => (
      event.type === 'assistant.delta'
      && Boolean(event.messageId)
      && Boolean(event.content)
      && !streamedDeltaIdsRef.current.has(event.eventId)
    ))
    if (unseen.length === 0) return
    for (const event of unseen) streamedDeltaIdsRef.current.add(event.eventId)
    setStreamedByMessage((current) => {
      const next = new Map(current)
      for (const event of unseen) {
        const messageId = event.messageId as string
        next.set(messageId, (next.get(messageId) ?? '') + event.content)
      }
      return next
    })
  }, [activity.events, conversationId])

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
    () => activity.events.filter((event) => !claimedTurnIds.has(event.turnId ?? '') && !event.type.startsWith('context.')),
    [activity.events, claimedTurnIds],
  )
  const contextUsage = useMemo(
    () => [...activity.events].reverse().find((event) => event.contextUsage)?.contextUsage ?? conversation?.context_usage ?? null,
    [activity.events, conversation?.context_usage],
  )
  const openQuestionTurnIds = useMemo(
    () => new Set((conversation?.turns ?? []).filter((turn) => turn.state === 'waiting_user').map((turn) => turn.turn_id)),
    [conversation?.turns],
  )

  // A waiting turn is terminal from the worker's perspective and must keep
  // the question card and normal composer usable even if the conversation
  // snapshot briefly still reports the preceding running state.
  const running = RUNNING_STATES.has(conversation?.state ?? '')
    && (conversation?.turns ?? []).some((turn) => RUNNING_STATES.has(turn.state) && !cancelledTurnIds.has(turn.turn_id))
    && openQuestionTurnIds.size === 0
  const mode = useMemo(() => modeFromEvents(activity.events, conversation?.state ?? 'queued'), [activity.events, conversation?.state])

  // Track identities rather than render passes. Snapshot reconciliation can
  // repeat the same items after an SSE event, but it must not inflate the
  // return-to-latest action. A changed message body is new visible content even
  // when its durable message id already existed as an empty placeholder.
  useEffect(() => {
    const content = new Set([
      ...messages.map((item) => `message:${item.message_id}:${item.content}`),
      // A delta's visible representation is the assistant message above; it
      // must not increment the return action twice just because the same
      // update also exists in the append-only event log.
      ...activity.events.filter((event) => event.kind !== 'message').map((event) => `activity:${event.eventId}`),
    ])
    if (observedConversationRef.current !== conversationId) {
      observedConversationRef.current = conversationId
      observedContentRef.current = content
      pinnedRef.current = true
      setAtBottom(true)
      setNewActivityCount(0)
      return
    }

    let additions = 0
    for (const key of content) {
      if (!observedContentRef.current.has(key)) additions += 1
    }
    for (const key of content) observedContentRef.current.add(key)
    if (additions > 0 && !pinnedRef.current) {
      setNewActivityCount((current) => current + additions)
    }
  }, [activity.events, conversationId, messages])

  useLayoutEffect(() => {
    if (!pinnedRef.current) return
    const element = scrollRef.current
    if (!element) return
    // Run before paint. A passive effect leaves one frame with the previous
    // offset after each streamed delta/tool event, which is visible as a jump
    // and can race the browser's scroll anchoring. Pinned streaming updates
    // are intentionally immediate; smooth scrolling is reserved for the
    // explicit "Ir para o fim" action below.
    element.scrollTop = element.scrollHeight
  }, [messages, activity.events.length])

  const scrollToLatest = useCallback(() => {
    const element = scrollRef.current
    if (!element) return
    pinnedRef.current = true
    setAtBottom(true)
    setNewActivityCount(0)
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: element.scrollHeight, behavior: reduced ? 'auto' : 'smooth' })
    else element.scrollTop = element.scrollHeight
  }, [reduced])

  const submitQuestionAnswers = useCallback(async (event: ConversationActivityEvent, answers: UserQuestionAnswer[]) => {
    if (!event.questions || !event.turnId || !openQuestionTurnIds.has(event.turnId) || running) return
    const byId = new Map(event.questions.map((question) => [question.id, question]))
    const text = ['Respostas às perguntas do agente:']
    for (const answer of answers) {
      const question = byId.get(answer.id)
      if (!question) continue
      const labels = answer.selected.map((id) => question.options.find((option) => option.id === id)?.label).filter((label): label is string => Boolean(label))
      text.push(`\n${question.question}\n${labels.length ? `Seleção: ${labels.join(', ')}` : 'Seleção: nenhuma'}`)
      if (answer.note) text.push(`Observação: ${answer.note}`)
    }
    const response = text.join('\n')
    setError(null)
    setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: response, status: 'completed', retryable: false, attachments: [], command: null })
    try {
      await sendChatMessage(response)
      await loadSnapshot(false)
      void listConversations(client).then((value) => setChats(value.items)).catch(() => undefined)
    } catch (caught) {
      setPendingUserMessage(null)
      setError('Não foi possível enviar as respostas. Tente novamente.')
      throw caught
    }
  }, [client, loadSnapshot, openQuestionTurnIds, running, sendChatMessage])

  const submitMcpApproval = useCallback(async (event: ConversationActivityEvent, secrets: Record<string, string>) => {
    if (!event.mcpApproval || !event.turnId || !openQuestionTurnIds.has(event.turnId) || running) return
    const server = event.mcpApproval
    setError(null)
    try {
      await approveMcpServer(client, server.server_id, secrets)
    } catch (caught) {
      setError(`Não foi possível conectar ${server.display_name}. Tente novamente.`)
      throw caught
    }
    const response = `Conectei o servidor ${server.display_name}.`
    setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: response, status: 'completed', retryable: false, attachments: [], command: null })
    try {
      await sendChatMessage(response)
      await loadSnapshot(false)
      void listConversations(client).then((value) => setChats(value.items)).catch(() => undefined)
    } catch (caught) {
      setPendingUserMessage(null)
      setError('A conexão foi concluída, mas não foi possível avisar o agente. Tente novamente.')
      throw caught
    }
  }, [client, loadSnapshot, openQuestionTurnIds, running, sendChatMessage])

  const declineMcpApproval = useCallback(async (event: ConversationActivityEvent) => {
    if (!event.mcpApproval || !event.turnId || !openQuestionTurnIds.has(event.turnId) || running) return
    const response = `Não quero conectar o servidor ${event.mcpApproval.display_name} agora.`
    setError(null)
    setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: response, status: 'completed', retryable: false, attachments: [], command: null })
    try {
      await sendChatMessage(response)
      await loadSnapshot(false)
      void listConversations(client).then((value) => setChats(value.items)).catch(() => undefined)
    } catch (caught) {
      setPendingUserMessage(null)
      setError('Não foi possível registrar a recusa. Tente novamente.')
      throw caught
    }
  }, [client, loadSnapshot, openQuestionTurnIds, running, sendChatMessage])

  const submitPluginApproval = useCallback(async (event: ConversationActivityEvent) => {
    if (!event.pluginApproval || !event.turnId || !openQuestionTurnIds.has(event.turnId) || running) return
    await approvePlugin(client, event.pluginApproval.plugin_id)
    const response = `Instalei o plugin ${event.pluginApproval.display_name}.`
    setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: response, status: 'completed', retryable: false, attachments: [], command: null })
    await sendChatMessage(response)
    await loadSnapshot(false)
  }, [client, loadSnapshot, openQuestionTurnIds, running, sendChatMessage])

  const declinePluginApproval = useCallback(async (event: ConversationActivityEvent) => {
    if (!event.pluginApproval || !event.turnId || !openQuestionTurnIds.has(event.turnId) || running) return
    const response = `Não quero instalar o plugin ${event.pluginApproval.display_name} agora.`
    setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: response, status: 'completed', retryable: false, attachments: [], command: null })
    await sendChatMessage(response)
    await loadSnapshot(false)
  }, [loadSnapshot, openQuestionTurnIds, running, sendChatMessage])

  async function submit() {
    const text = message.trim()
    const readyUploads = readyUploadIds()
    if ((!text && readyUploads.length === 0) || running) return
    if (text.length > MAX_MESSAGE_CHARACTERS) {
      setOversizedInput({ text, attachments: readyUploads })
      return
    }
    setMessage('')
    setError(null)
    setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: text, status: 'completed', retryable: false, attachments: [], command: null })
    try {
      await sendChatMessage(text, readyUploads)
      resetAttachments()
      await loadSnapshot(false)
      void listConversations(client).then((value) => setChats(value.items)).catch(() => undefined)
    } catch {
      setMessage(text)
      setPendingUserMessage(null)
      setError('Não foi possível enviar. Tente novamente.')
    }
  }

  async function sendOversizedInputAsFile() {
    if (!oversizedInput || sendingOversizedInput || running) return
    setSendingOversizedInput(true)
    setError(null)
    const draft = oversizedInput
    let uploadedId: string | null = null
    let sent = false
    try {
      const uploaded = await uploadFile(client, new File([draft.text], 'mensagem.txt', { type: 'text/plain' }))
      uploadedId = uploaded.upload_id
      const prompt = 'Enviei o conteúdo extenso no arquivo mensagem.txt. Leia o arquivo anexado antes de responder.'
      setOversizedInput(null)
      setMessage('')
      setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: prompt, status: 'completed', retryable: false, attachments: [], command: null })
      await sendChatMessage(prompt, [...draft.attachments, uploaded.upload_id])
      sent = true
      resetAttachments()
      await loadSnapshot(false)
      void listConversations(client).then((value) => setChats(value.items)).catch(() => undefined)
    } catch {
      if (uploadedId && !sent) void deleteUpload(client, uploadedId).catch(() => undefined)
      if (!sent) {
        setOversizedInput(draft)
        setMessage(draft.text)
        setPendingUserMessage(null)
      }
      setError('Não foi possível enviar o input como arquivo .txt. Tente novamente.')
    } finally {
      setSendingOversizedInput(false)
    }
  }

  async function stop() {
    if (stopping) return
    setStopping(true)
    try {
      const receipt = await cancelConversation(client, conversationId)
      if (receipt.cancelling.length > 0) {
        const cancelled = new Set(receipt.cancelling)
        setCancelledTurnIds((current) => new Set([...current, ...cancelled]))
        setConversation((current) => current === null ? current : {
          ...current,
          state: 'cancelled',
          turns: current.turns.map((turn) => cancelled.has(turn.turn_id)
            ? { ...turn, state: 'cancelled', finished_at: turn.finished_at ?? new Date().toISOString() }
            : turn),
        })
      }
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
          {conversation?.turns.some((turn) => turn.scheduled_by_schedule_id) && <span className="chat__model">execuções agendadas</span>}
          {conversation && <span className="chat__model">{conversation.provider} · {conversation.model_id}</span>}
          <ContextIndicator usage={contextUsage} />
        </div>
        <div className="chat__bar-actions">
          <button
            type="button"
            className={showOverview ? 'ghost-button is-active' : 'ghost-button'}
            aria-pressed={showOverview}
            onClick={() => navigate(showOverview ? conversationPath : `${conversationPath}/overview`)}
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
          if (nextAtBottom) setNewActivityCount(0)
        }}>
          <div className="chat__thread">
            {!loading && loadFailure && (
              <section className="chat__load-error" role="alert">
                <h2>Não foi possível abrir esta conversa</h2>
                <p>{loadFailure}</p>
                <div>
                  <button type="button" className="ghost-button" onClick={() => { setLoading(true); setLoadAttempt((value) => value + 1) }}>Tentar novamente</button>
                  <Link className="ghost-button" to="/">Nova conversa</Link>
                </div>
              </section>
            )}
            {loading && <p className="chat__placeholder" role="status">Carregando conversa…</p>}
            {!loading && messages.length === 0 && <p className="chat__placeholder">Esta conversa ainda não tem mensagens.</p>}

            {!loadFailure && messages.map((item) => {
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
                    ? <TurnTimeline items={timeline} conversationId={conversationId} client={client} onPreview={setPreviewReference} openQuestionTurnIds={openQuestionTurnIds} onAnswer={submitQuestionAnswers} onMcpApprove={submitMcpApproval} onMcpDecline={declineMcpApproval} onPluginApprove={submitPluginApproval} onPluginDecline={declinePluginApproval} />
                    : item.role === 'assistant' && item.content
                      ? <MarkdownMessage content={item.content} conversationId={conversationId} client={client} onPreview={setPreviewReference} />
                      : item.command
                        ? <MessageCommandChip command={item.command} />
                        : <p>{item.content || placeholderFor(item)}</p>}
                  {item.role === 'user' && item.attachments.length > 0 && (
                    <MessageAttachments conversationId={conversationId} items={item.attachments} client={client} onPreview={setPreviewReference} />
                  )}
                  {item.retryable && <span className="bubble__retry">Você pode reenviar esta mensagem.</span>}
                </motion.article>
              )
            })}

            {!loadFailure && <ActivityStream events={unclaimedEvents} conversationId={conversationId} onPreview={setPreviewReference} openQuestionTurnIds={openQuestionTurnIds} onAnswer={submitQuestionAnswers} onMcpApprove={submitMcpApproval} onMcpDecline={declineMcpApproval} onPluginApprove={submitPluginApproval} onPluginDecline={declinePluginApproval} />}

            <AnimatePresence>
              {!loadFailure && running && (
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

        {newActivityCount > 0 && !atBottom && (
          <button type="button" className="chat__new-activity" onClick={scrollToLatest}>
            Ir para o fim · {newActivityCount} novas atividades
          </button>
        )}

        <AnimatePresence>
          {showOverview && conversation && (
            <OverviewPanel
              conversationId={conversationId}
              client={client}
              liveEvents={activity.events}
              onClose={() => navigate(conversationPath)}
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

      {!loadFailure && !showOverview && <footer className="chat__foot" data-testid="chat-composer" data-at-bottom={atBottom}>
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
          attachments={attachments}
          onAttach={onAttach}
          onRemoveAttachment={onRemoveAttachment}
          canSend={attachmentsReady}
          notice={notice}
          commands={pluginCommands}
          skills={skills}
          codeMode={codeMode}
          onCodeModeChange={setCodeMode}
          settings={
            <>
              {effectiveChatProvider && <ModelPicker
                providers={[...PROVIDER_NAMES]}
                provider={effectiveChatProvider}
                onProviderChange={changeChatProvider}
                models={chatModels}
                modelId={effectiveChatModelId}
                onModelChange={setChatModelId}
                loading={chatModelsLoading}
                failed={chatModelsFailed}
                disabled={running || stopping}
              />}
              <WorkspaceFolderButton
                state={conversation?.workspace ?? MANAGED_WORKSPACE}
                onInspect={(path) => inspectWorkspaceFolder(client, conversationId, path)}
                onAttach={(path, acknowledged) => attachWorkspaceFolder(client, conversationId, path, acknowledged)}
                onDetach={() => detachWorkspaceFolder(client, conversationId)}
                onChange={(next) => setConversation((current) => (current ? { ...current, workspace: next } : current))}
              />
            </>
          }
        />
      </footer>}
      {oversizedInput && (
        <div className="input-limit-dialog__backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !sendingOversizedInput) setOversizedInput(null) }}>
          <section className="input-limit-dialog" role="dialog" aria-modal="true" aria-labelledby="input-limit-title">
            <span className="eyebrow">INPUT LIMIT</span>
            <h2 id="input-limit-title">Seu input é muito extenso</h2>
            <p>Reduza a mensagem para até {MAX_MESSAGE_CHARACTERS.toLocaleString('pt-BR')} caracteres ou envie o conteúdo completo como um arquivo .txt.</p>
            <div className="input-limit-dialog__actions">
              <button type="button" className="ghost-button" disabled={sendingOversizedInput} onClick={() => setOversizedInput(null)}>Voltar e reduzir</button>
              <button type="button" className="button button--primary" disabled={sendingOversizedInput} onClick={() => void sendOversizedInputAsFile()}>{sendingOversizedInput ? 'Enviando arquivo…' : 'Enviar mesmo assim como arquivo .txt'}</button>
            </div>
          </section>
        </div>
      )}
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

function conversationLoadHeadline(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return 'Esta conversa não está disponível neste workspace.'
  if (error instanceof ApiError && (error.status === 0 || error.code === 'network_error')) return 'Não foi possível alcançar o backend local. Confirme se o Orin está em execução.'
  return 'A conversa não pôde ser carregada agora. Tente novamente.'
}

function terminalConversationState(event: ConversationActivityEvent): 'completed' | 'failed' | 'cancelled' | 'waiting_user' | null {
  if (event.type === 'turn.completed') return 'completed'
  if (event.type === 'turn.failed') return event.state === 'cancelled' ? 'cancelled' : 'failed'
  if (event.type === 'turn.waiting_user') return 'waiting_user'
  return null
}

function isResyncError(value: unknown): value is ApiError {
  if (!(value instanceof ApiError)) return false
  const code = value.code.toLowerCase().replaceAll('-', '_')
  return value.status === 409 || code === 'cursor_invalid' || code === 'invalid_cursor' || code === 'cursor_expired' || code === 'retention_expired' || code === 'sequence_gap'
}

export type { ConversationActivityEvent }
