import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { createBrowserApiClient } from '../../api/client'
import {
  cancelConversation, createConversation, getConversation, listConversations, listOlderConversationActivities, moveConversationToProject, sendConversationMessage, streamConversationEvents, submitToolPermission, updateConversationPermissionMode,
  type Conversation, type ConversationMessage, type ConversationMode,
  getFriendPersona, updateFriendPersona, syncFriendSoul, listFriendMemories, deleteFriendMemory,
  type FriendPersonaState, type FriendMemoryEntry,
} from '../../api/conversations'
import { ApiError } from '../../api/errors'
import { approveMcpServer } from '../../api/mcp'
import { createProject, listProjects, type Project } from '../../api/projects'
import { approvePlugin, listPluginCommands, type PluginCommand } from '../../api/plugins'
import { listSkills, type SkillSummary } from '../../api/skills'
import {
  PROVIDER_NAMES, getVisionModelSetting, listProviderModels,
  type ProviderModel, type ProviderName, type VisionModelSetting,
} from '../../api/providers'
import { deleteUpload, uploadFile } from '../../api/uploads'
import {
  getCodeModeSettings, listWorkspaceFiles, manageConversationWorktree, normalizeCodePermissionMode,
  type CodePermissionMode,
} from '../../api/codeMode'
import { attachWorkspaceFolder, detachWorkspaceFolder, inspectWorkspaceFolder, type WorkspaceState } from '../../api/workspace'
import { CommandPalette } from '../../components/CommandPalette'
import { ModelPicker } from '../../components/ModelPicker'
import { OrinMascot } from '../../components/OrinMascot'
import {
  enqueueMessage, forkConversation, getFileHistory, listCommandCatalog, listQueue, removeQueued, renameConversation, rewindCode,
  rewindMessage, runShell, TOO_LARGE_TO_BACK_UP, type CatalogCommand, type FileHistoryItem, type QueuedInput,
} from '../../api/sessionTools'
import { QueuePanel } from './QueuePanel'
import { RewindPicker, type RewindMode } from './RewindPicker'
import {
  CodeWorkbenchPanel,
  type CodeWorkbenchFileItem,
  type CodeWorkbenchTerminalEntry,
  type CodeWorkbenchTab,
} from './CodeWorkbenchPanel'
import { CommandInfoDialog } from '../commands/CommandInfoDialog'
import { useChatCommands, type InfoPanel } from '../commands/useChatCommands'
import { OverviewPanel } from '../overview/OverviewPanel'
import { WorkspaceNavigation } from '../projects/WorkspaceNavigation'
import { PreferencesModal } from '../settings/PreferencesModal'
import { ActivityStream } from './ActivityStream'
import { AgentPulse, modeFromEvents } from './AgentPulse'
import { attachmentNotice } from './attachmentNotice'
import {
  CodePermissionDock,
  type CodePermissionDockDecision,
  type DockToolPermissionRequest,
} from './CodePermissionDock'
import type { CodeSessionProposalDecision } from './CodeSessionProposalCard'
import { Composer } from './Composer'
import { FriendSoulDrawer } from './FriendSoulDrawer'
import { LiveVoiceQuickSettings } from './LiveVoiceQuickSettings'
import { LiveVoiceStage, type LiveVoiceTranscriptItem } from './LiveVoiceStage'
import { CodeSessionLiveCard } from './CodeSessionLiveCard'
import { ContextIndicator } from './ContextIndicator'
import { MarkdownMessage } from './MarkdownMessage'
import { MessageCommandChip } from './MessageCommandChip'
import { MessageAttachments } from './MessageAttachments'
import { PushNotificationToast, isFreshNotice, type PushNotice } from './PushNotificationToast'
import { StatusLineBar } from './StatusLineBar'
import { TasksPanel } from './TasksPanel'
import { TeamActivityView } from './TeamActivityView'
import { TranscriptView } from './TranscriptView'
import { useKeybindingHandlers } from '../keybindings/engine'
import { useTheme } from '../../app/ThemeContext'
import { notifySystem, windowIsAway } from '../notifications/systemNotify'
import { TurnTimeline } from './TurnTimeline'
import { WorkspaceFilePreview, type WorkspaceFileReference } from './WorkspaceFileCard'
import { activityReducer, createActivityState } from './activityReducer'
import { formatTurnWorkDuration } from './activitySummary'
import type { ConversationActivityEvent, StructuredPatchHunk, ToolPermissionDecision } from './activityTypes'
import type { UserQuestionAnswer } from './UserQuestionCard'
import { buildMessageTimelines, resolveTurnId } from './turnTimelineFold'
import { useComposerAttachments } from './useComposerAttachments'

const TOOL_CAPABILITY_NAMES = new Set(['tools', 'tool_use', 'function_calling'])
const MAX_MESSAGE_CHARACTERS = 16000
const EMPTY_TURN_IDS: ReadonlySet<string> = new Set()

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
  const submitInFlight = useRef(false) // W7-R13
  const composerTextRef = useRef('') // W7-R9: what the composer holds when an async send fails
  useEffect(() => { composerTextRef.current = message }, [message])
  const [codePermissionMode, setCodePermissionMode] = useState<CodePermissionMode>('ask_permissions')
  const [codeSystemNotifications, setCodeSystemNotifications] = useState(false)
  const [pushNotice, setPushNotice] = useState<PushNotice | null>(null)
  const shownNoticesRef = useRef(new Set<string>())
  const dismissPushNotice = useCallback(() => setPushNotice(null), [])
  const [workspaceDrawerOpen, setWorkspaceDrawerOpen] = useState(false)
  const [drawerFiles, setDrawerFiles] = useState<string[]>([])
  const [drawerSearchQuery, setDrawerSearchQuery] = useState('')
  const [decidedPlanKeys, setDecidedPlanKeys] = useState<Set<string>>(() => new Set())
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null)
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
  // W7: the folder's own skills and commands, the answer panel of /help and /status, and command notices.
  const [catalogCommands, setCatalogCommands] = useState<CatalogCommand[]>([])
  const [infoPanel, setInfoPanel] = useState<InfoPanel | null>(null)
  const [commandNotice, setCommandNotice] = useState<string | null>(null)
  const [modelPickerSignal, setModelPickerSignal] = useState(0)
  // W7 Batch D: the message queue, and the rewind picker with the file history it shows.
  const [queue, setQueue] = useState<QueuedInput[]>([])
  const [queueBusy, setQueueBusy] = useState(false)
  const [rewindState, setRewindState] = useState<{
    history: FileHistoryItem[]; busy: boolean; error: string | null
    initialMessageId?: string | null; unbackable?: string[]; unbackableFor?: string | null
  } | null>(null)
  const theme = useTheme()
  // W7 Batch D2: the background tasks panel and the detailed transcript (ctrl+o).
  const [tasksOpen, setTasksOpen] = useState(false)
  const [transcriptOpen, setTranscriptOpen] = useState(false)
  // W7: ctrl+t (desktop) hides or shows the agent's task list above the composer.
  const [todosHidden, setTodosHidden] = useState(false)
  useKeybindingHandlers('Global', {
    'app:toggleTranscript': () => setTranscriptOpen((open) => !open),
    'app:toggleTodos': () => setTodosHidden((hidden) => !hidden),
  })
  const [preferencesOpen, setPreferencesOpen] = useState(false)
  const [friendSoulOpen, setFriendSoulOpen] = useState(false)
  const [friendPersona, setFriendPersona] = useState<FriendPersonaState | null>(null)
  const [friendMemories, setFriendMemories] = useState<FriendMemoryEntry[]>([])
  const [liveVoiceOpen, setLiveVoiceOpen] = useState(false)
  const [liveVoiceSettingsOpen, setLiveVoiceSettingsOpen] = useState(false)
  const pendingWakeUtteranceRef = useRef<string | null>(null)
  const [visionSetting, setVisionSetting] = useState<VisionModelSetting | null>(null)
  const [chatProvider, setChatProvider] = useState<ProviderName | null>(null)
  const [chatModelId, setChatModelId] = useState('')
  const [chatModels, setChatModels] = useState<ProviderModel[]>([])
  const [chatModelsLoading, setChatModelsLoading] = useState(false)
  const [chatModelsFailed, setChatModelsFailed] = useState(false)
  const [streamedByMessage, setStreamedByMessage] = useState<Map<string, string>>(() => new Map())
  const [oversizedInput, setOversizedInput] = useState<{ text: string; attachments: string[] } | null>(null)
  const [sendingOversizedInput, setSendingOversizedInput] = useState(false)
  const [hasOlderActivities, setHasOlderActivities] = useState(false)
  const [oldestActivityId, setOldestActivityId] = useState<number | null>(null)
  const [loadingOlderActivities, setLoadingOlderActivities] = useState(false)

  const cursorRef = useRef('0')
  const scrollRef = useRef<HTMLDivElement>(null)
  const threadRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)
  const loadingOlderRef = useRef(false)
  const observedContentRef = useRef(new Set<string>())
  const observedConversationRef = useRef(conversationId)
  const streamedDeltaIdsRef = useRef(new Set<string>())
  const streamedConversationRef = useRef(conversationId)
  const userChangedPermissionRef = useRef(false)
  useEffect(() => {
    userChangedPermissionRef.current = false
  }, [conversationId])

  const conversationMode: ConversationMode = useMemo(() => {
    if (conversation?.mode === 'code') return 'code'
    if (conversation?.mode === 'friend') return 'friend'
    if (conversation?.mode === 'chat') return 'chat'
    return conversation?.turns.some((turn) => turn.code_mode === 'code') ? 'code' : 'chat'
  }, [conversation?.mode, conversation?.turns])
  // The event stream reads the mode through a ref, so a mode change does not reconnect it.
  const conversationModeRef = useRef(conversationMode)
  useEffect(() => { conversationModeRef.current = conversationMode }, [conversationMode])

  const showOverview = location.pathname.endsWith('/overview')
  const conversationPath = projectId === undefined
    ? `/chats/${encodeURIComponent(conversationId)}`
    : `/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(conversationId)}`

  const loadSnapshot = useCallback(async (resetActivity: boolean) => {
    const next = await getConversation(client, conversationId)
    setConversation(next)
    if (!userChangedPermissionRef.current) {
      const resolvedPerm = next.permission_mode ?? (next.mode === 'code' ? 'accept_edits' : 'ask_permissions')
      setCodePermissionMode(normalizeCodePermissionMode(resolvedPerm))
    }
    if (resetActivity) {
      cursorRef.current = next.activity_cursor
      setHasOlderActivities(Boolean(next.has_older_activities))
      setOldestActivityId(next.oldest_activity_id ?? null)
      setLoadingOlderActivities(false)
      loadingOlderRef.current = false
      dispatch({ type: 'snapshot', events: next.activities, cursor: next.activity_cursor })
    }
    return next
  }, [client, conversationId])

  const loadOlderActivities = useCallback(async () => {
    if (!hasOlderActivities || oldestActivityId === null || loadingOlderRef.current) return
    loadingOlderRef.current = true
    setLoadingOlderActivities(true)
    const scrollEl = scrollRef.current
    const prevScrollHeight = scrollEl?.scrollHeight ?? 0
    const prevScrollTop = scrollEl?.scrollTop ?? 0
    try {
      const page = await listOlderConversationActivities(client, conversationId, oldestActivityId, 150)
      if (page.activities.length === 0 || !page.has_older_activities || page.oldest_activity_id === oldestActivityId) {
        if (page.activities.length > 0) {
          for (const item of page.activities) {
            observedContentRef.current.add(`activity:${item.eventId}`)
          }
          pinnedRef.current = false
          dispatch({ type: 'prepend_older', events: page.activities })
        }
        setHasOlderActivities(false)
        setOldestActivityId(null)
      } else {
        for (const item of page.activities) {
          observedContentRef.current.add(`activity:${item.eventId}`)
        }
        pinnedRef.current = false
        dispatch({ type: 'prepend_older', events: page.activities })
        setHasOlderActivities(true)
        setOldestActivityId(page.oldest_activity_id)
      }
      requestAnimationFrame(() => {
        const el = scrollRef.current
        if (el && prevScrollHeight > 0) {
          const delta = el.scrollHeight - prevScrollHeight
          if (delta > 0) {
            el.scrollTop = prevScrollTop + delta
          }
        }
      })
    } catch {
      // Ignore transient pagination errors so the user can retry by scrolling/clicking
    } finally {
      loadingOlderRef.current = false
      setLoadingOlderActivities(false)
    }
  }, [client, conversationId, hasOlderActivities, oldestActivityId])

  useEffect(() => {
    if (loading || !hasOlderActivities || loadingOlderActivities || loadingOlderRef.current) return
    const el = scrollRef.current
    if (el && el.scrollTop < 250 && !pinnedRef.current) {
      void loadOlderActivities()
    }
  }, [hasOlderActivities, loadOlderActivities, loading, loadingOlderActivities, oldestActivityId])

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
            if (!streamedDeltaIdsRef.current.has(event.eventId)) {
              if (event.type === 'assistant.delta' && event.messageId && event.content) {
                streamedDeltaIdsRef.current.add(event.eventId)
                const msgId = event.messageId
                const deltaContent = event.content
                setStreamedByMessage((current) => {
                  const next = new Map(current)
                  next.set(msgId, (next.get(msgId) ?? '') + deltaContent)
                  return next
                })
              } else if (event.type === 'message.reset' && event.messageId) {
                streamedDeltaIdsRef.current.add(event.eventId)
                const msgId = event.messageId
                setStreamedByMessage((current) => {
                  if (!current.has(msgId)) return current
                  const next = new Map(current)
                  next.delete(msgId)
                  return next
                })
              }
            }
            dispatch({ type: 'event', event })
            if (codeSystemNotifications && windowIsAway() && !event.parentAgentId) {
              // W7: Code mode's own moments, a chat turn that finished, and a turn waiting for the person.
              const notification = codeModeNotification(event.type)
                ?? (event.type === 'turn.completed' && conversationModeRef.current !== 'code' ? { title: 'Orin respondeu', body: 'A resposta está pronta.' } : null)
                ?? (event.type === 'turn.waiting_user' ? { title: 'O Orin está esperando você', body: 'Há uma pergunta para você responder na conversa.' } : null)
              if (notification) notifySystem(notification)
            }
            // PushNotification: a system notification while the window is hidden, otherwise a toast. Only a
            // fresh event pops up, once; the timeline keeps the record either way.
            if (event.type === 'notification.pushed' && event.notificationMessage && isFreshNotice(event.occurredAt)
              && !shownNoticesRef.current.has(event.eventId)) {
              shownNoticesRef.current.add(event.eventId)
              const body = event.notificationMessage
              if (!(codeSystemNotifications && windowIsAway() && notifySystem({ title: 'Orin', body }))) {
                setPushNotice({ id: event.eventId, message: body })
              }
            }
            const terminalState = !event.parentAgentId ? terminalConversationState(event) : null
            if (terminalState) {
              setConversation((current) => current === null ? current : {
                ...current,
                state: terminalState,
                turns: current.turns.map((turn) => turn.turn_id === event.turnId
                  ? { ...turn, state: terminalState, finished_at: turn.finished_at ?? event.occurredAt ?? null }
                  : turn),
              })
              void loadSnapshot(false).catch(() => undefined)
              window.dispatchEvent(new Event('orin:conversations-updated'))
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
          streamedDeltaIdsRef.current = new Set()
          setStreamedByMessage(new Map())
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
      if (!controller.signal.aborted) {
        setCodeSystemNotifications(settings.system_notifications)
      }
    }).catch(() => undefined)
    return () => controller.abort()
  }, [client])

  const changeCodePermissionMode = useCallback((next: CodePermissionMode) => {
    const previous = codePermissionMode
    userChangedPermissionRef.current = true
    setCodePermissionMode(next)
    setConversation((current) => (current ? { ...current, permission_mode: next } : current))
    const targetId = conversation?.conversation_id ?? conversationId
    if (targetId) {
      void updateConversationPermissionMode(client, targetId, next).catch(() => {
        setCodePermissionMode(previous)
        setConversation((current) => (current ? { ...current, permission_mode: previous } : current))
        setError('Não foi possível alterar o modo de permissão desta conversa.')
      })
    }
  }, [client, codePermissionMode, conversation?.conversation_id, conversationId])

  const fetchWorkspaceFiles = useCallback(async (query = '', limit = 100): Promise<string[]> => {
    if (!conversationId) return []
    try {
      return await listWorkspaceFiles(client, conversationId, query, limit)
    } catch (caught) {
      if (!(caught instanceof TypeError)) throw caught
      return await client.request({
        path: `/v1/conversations/${encodeURIComponent(conversationId)}/workspace-files`,
        query: { ...(query ? { query } : {}), limit },
        parse: (value) => {
          if (!value || typeof value !== 'object' || Array.isArray(value)) return []
          const data = value as Record<string, unknown>
          const raw = Array.isArray(data.items) ? data.items : Array.isArray(data.files) ? data.files : []
          return raw.filter((item): item is string => typeof item === 'string')
        },
      })
    }
  }, [client, conversationId])

  const toggleWorkspaceDrawer = useCallback(() => {
    setWorkspaceDrawerOpen((prev) => {
      const next = !prev
      if (next && conversationId) {
        void fetchWorkspaceFiles(drawerSearchQuery, 100)
          .then((files) => setDrawerFiles(files))
          .catch(() => setDrawerFiles([]))
      }
      return next
    })
  }, [conversationId, drawerSearchQuery, fetchWorkspaceFiles])

  const handleDrawerSearchChange = useCallback(async (query: string) => {
    setDrawerSearchQuery(query)
    if (!conversationId) return
    try {
      const files = await fetchWorkspaceFiles(query, 100)
      setDrawerFiles(files)
    } catch {
      setDrawerFiles([])
    }
  }, [conversationId, fetchWorkspaceFiles])

  const [worktreeIsolated, setWorktreeIsolated] = useState(false)
  const [worktreeBranch, setWorktreeBranch] = useState<string | null>(null)

  const searchWorkspaceFiles = useCallback(
    (query: string) => fetchWorkspaceFiles(query, 20),
    [fetchWorkspaceFiles],
  )

  const toggleWorktreeIsolation = useCallback((nextIsolated: boolean) => {
    setWorktreeIsolated(nextIsolated)
    void manageConversationWorktree(client, conversationId, {
      action: nextIsolated ? 'enter' : 'exit',
      enabled: nextIsolated,
    }).then((response) => {
      setWorktreeIsolated(response.active)
      setWorktreeBranch(response.worktree?.branch ?? null)
    }).catch(() => {
      setWorktreeIsolated(!nextIsolated)
    })
  }, [client, conversationId])


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

  // W7: the skills and commands of this conversation's folder, for the `/` picker. Best-effort, like the above.
  useEffect(() => {
    if (!conversationId) return
    let alive = true
    listCommandCatalog(client, conversationId)
      .then((value) => { if (alive) setCatalogCommands(value.commands) })
      .catch(() => { if (alive) setCatalogCommands([]) })
    return () => { alive = false }
  }, [client, conversationId])

  // A conversation opened by a rewind or /branch starts with the chosen message in the composer.
  const [prefillApplied, setPrefillApplied] = useState<string | null>(null)
  const prefill = (location.state as { prefill?: unknown } | null)?.prefill
  if (typeof prefill === 'string' && prefill && prefillApplied !== conversationId) {
    setPrefillApplied(conversationId)
    setMessage(prefill)
  }

  const refreshQueue = useCallback(async () => {
    if (!conversationId) return
    try {
      setQueue(await listQueue(client, conversationId))
    } catch {
      // The queue is a convenience: a failed refresh keeps the last list.
    }
  }, [client, conversationId])

  useEffect(() => {
    if (!commandNotice) return
    const timer = setTimeout(() => setCommandNotice(null), 5000)
    return () => clearTimeout(timer)
  }, [commandNotice])

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
  const [teamViewOpen, setTeamViewOpen] = useState(false)
  const [teamPaused, setTeamPaused] = useState(false)
  const [workbenchOpen, setWorkbenchOpen] = useState(false)
  const [workbenchTab, setWorkbenchTab] = useState<CodeWorkbenchTab>('preview')
  const [selectedWorkbenchFile, setSelectedWorkbenchFile] = useState<string | null>(null)
  const [changedFilesExpanded, setChangedFilesExpanded] = useState(false)
  const [selectionFor, setSelectionFor] = useState('')
  const [renderedConversationId, setRenderedConversationId] = useState(conversationId)
  if (conversationId !== renderedConversationId) {
    setRenderedConversationId(conversationId)
    setSelectionFor('')
    setChatProvider(null)
    setChatModelId('')
    setChatModels([])
    setPendingUserMessage(null)
    setStopping(false)
    setError(null)
    setOversizedInput(null)
    setTeamViewOpen(false)
    setTeamPaused(false)
    setWorkbenchOpen(false)
    setWorkbenchTab('preview')
    setSelectedWorkbenchFile(null)
    setChangedFilesExpanded(false)
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
    (text: string, attachments: string[] = [], liveModeOverride?: boolean) =>
      sendConversationMessage(
        client,
        conversationId,
        text,
        attachments,
        chatSelection,
        conversationMode === 'code' ? 'code' : 'auto',
        undefined,
        undefined,
        Boolean(liveModeOverride ?? liveVoiceOpen),
      ),
    [chatSelection, client, conversationId, conversationMode, liveVoiceOpen],
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
    const turnToMessageId = new Map<string, string>()
    for (const event of activity.events) {
      if (event.turnId && event.messageId) {
        turnToMessageId.set(event.turnId, event.messageId)
      }
    }
    const unseen = activity.events.filter((event) => {
      if (streamedDeltaIdsRef.current.has(event.eventId)) return false
      if (event.type === 'assistant.delta' && Boolean(event.messageId) && Boolean(event.content)) {
        return true
      }
      if (event.type === 'message.reset') {
        return Boolean(event.messageId || (event.turnId && turnToMessageId.has(event.turnId)))
      }
      return false
    })
    if (unseen.length === 0) return
    for (const event of unseen) streamedDeltaIdsRef.current.add(event.eventId)
    setStreamedByMessage((current) => {
      const next = new Map(current)
      for (const event of unseen) {
        if (event.type === 'message.reset') {
          const messageId = event.messageId ?? (event.turnId ? turnToMessageId.get(event.turnId) : undefined)
          if (messageId) {
            next.delete(messageId)
          }
          continue
        }
        const messageId = event.messageId as string
        next.set(messageId, (next.get(messageId) ?? '') + event.content)
      }
      return next
    })
  }, [activity.events, conversationId])

  const messages = useMemo(() => {
    const base = (conversation?.messages ?? []).map((item) => {
      if (item.role !== 'assistant') return item
      const streamed = streamedByMessage.get(item.message_id)
      if (streamed && streamed.length > (item.content?.length ?? 0)) {
        return { ...item, content: streamed }
      }
      return item
    })
    // The optimistic echo is dropped as soon as the durable snapshot contains
    // the same text, so a re-render can never show the message twice.
    const alreadyDurable = pendingUserMessage !== null
      && base.some((item) => item.role === 'user' && item.content.trim() === pendingUserMessage.content.trim())
    return pendingUserMessage && !alreadyDurable ? [...base, pendingUserMessage] : base
  }, [conversation?.messages, streamedByMessage, pendingUserMessage])

  if (
    pendingUserMessage !== null &&
    (conversation?.messages ?? []).some(
      (item) => item.role === 'user' && item.content.trim() === pendingUserMessage.content.trim(),
    )
  ) {
    setPendingUserMessage(null)
  }

  const [resolvedPermissionDecisions, setResolvedPermissionDecisions] = useState<Map<string, 'allowed' | 'denied'>>(() => new Map())

  const consolidatedEvents = useMemo(() => {
    const lastTerminalLifecycleByTurn = new Map<string, string>()
    const cancelledTurns = new Set<string>(cancelledTurnIds)
    for (const event of activity.events) {
      if (event.parentAgentId) continue
      const term = terminalConversationState(event)
      if (term === 'cancelled' && event.turnId) {
        cancelledTurns.add(event.turnId)
      }
      if (event.kind === 'lifecycle' && event.turnId && (term === 'completed' || term === 'failed' || term === 'cancelled')) {
        lastTerminalLifecycleByTurn.set(event.turnId, event.eventId)
      }
    }
    return activity.events
      .filter((event) => {
        // Speech events feed the Live voice, not the visible timeline.
        if (event.type.startsWith('speak.')) return false
        if (event.kind === 'lifecycle' && Boolean(event.parentAgentId)) return false
        if ((event.type === 'delegation.failed' || event.type === 'DELEGATION_FAILED') && event.turnId && cancelledTurns.has(event.turnId)) {
          return false
        }
        const term = terminalConversationState(event)
        if (event.kind === 'lifecycle' && event.turnId && (term === 'completed' || term === 'failed' || term === 'cancelled')) {
          const lastId = lastTerminalLifecycleByTurn.get(event.turnId)
          if (lastId && lastId !== event.eventId) return false
        }
        return true
      })
      .map((event): ConversationActivityEvent => {
        const reqId = event.toolPermission?.requestId
        const resolvedState = reqId ? resolvedPermissionDecisions.get(reqId) : undefined
        if (!resolvedState || !event.toolPermission) return event
        return {
          ...event,
          state: resolvedState === 'allowed' ? 'completed' : 'failed',
          toolPermission: {
            ...event.toolPermission,
            state: resolvedState,
          },
        }
      })
  }, [activity.events, cancelledTurnIds, resolvedPermissionDecisions])

  // Each assistant message gets its own text/action timeline, folded from the
  // turn it belongs to; a turn no message could claim (still running before
  // its first delta, or older than the 500-event window) stays visible
  // through the flat stream below instead of disappearing.
  const { timelines: timelinesByMessage, claimedTurnIds } = useMemo(
    () => buildMessageTimelines(messages, consolidatedEvents),
    [messages, consolidatedEvents],
  )
  const hasTerminalPlaceholderMessage = useMemo(
    () => messages.some((m) => m.role === 'assistant' && !m.content.trim() && (m.status === 'cancelled' || m.status === 'failed' || m.status === 'completed')),
    [messages],
  )
  const unclaimedEvents = useMemo(
    () => consolidatedEvents.filter((event) => {
      if (claimedTurnIds.has(event.turnId ?? '') || event.type.startsWith('context.')) return false
      const term = terminalConversationState(event)
      if (hasTerminalPlaceholderMessage && event.kind === 'lifecycle' && (term === 'cancelled' || term === 'failed' || term === 'completed')) {
        return false
      }
      return true
    }),
    [consolidatedEvents, claimedTurnIds, hasTerminalPlaceholderMessage],
  )
  const contextUsage = useMemo(
    () => [...activity.events].reverse().find((event) => event.contextUsage)?.contextUsage ?? conversation?.context_usage ?? null,
    [activity.events, conversation?.context_usage],
  )
  const openQuestionTurnIds = useMemo(() => {
    const terminalTurnIds = new Set(
      (conversation?.turns ?? [])
        .filter((turn) => turn.state === 'completed' || turn.state === 'failed' || turn.state === 'cancelled' || cancelledTurnIds.has(turn.turn_id))
        .map((turn) => turn.turn_id),
    )
    const ids = new Set(
      (conversation?.turns ?? [])
        .filter((turn) => turn.state === 'waiting_user' && !terminalTurnIds.has(turn.turn_id))
        .map((turn) => turn.turn_id),
    )
    for (const event of activity.events) {
      if (!event.turnId || terminalTurnIds.has(event.turnId) || decidedPlanKeys.has(event.turnId) || decidedPlanKeys.has(event.eventId)) continue
      const permReq = event.toolPermission
      const isResolvedPerm = Boolean(
        permReq?.requestId &&
          (resolvedPermissionDecisions.has(permReq.requestId) ||
            permReq.state === 'allowed' ||
            permReq.state === 'denied'),
      )
      if (
        !isResolvedPerm && (
          event.type === 'turn.waiting_user'
          || event.type === 'code_mode.blocked'
          || event.type === 'code_mode.plan_ready'
          || event.type === 'code_mode.decision_required'
          || event.type === 'tool.permission_required'
          || Boolean(event.toolPermission)
          || Boolean(event.planApproval)
          || Boolean(event.mcpApproval)
          || Boolean(event.pluginApproval)
          || Boolean(event.questions)
          || Boolean((event as { codeSessionProposal?: unknown }).codeSessionProposal)
          || event.tool === 'exit_plan_mode'
          || event.toolName === 'exit_plan_mode'
        )
      ) {
        ids.add(event.turnId)
      }
      if (
        event.type === 'turn.completed'
        || event.type === 'TURN_COMPLETED'
        || event.type === 'code_mode.completed'
        || event.type === 'code_mode.completed_with_caveats'
        || event.type === 'turn.failed'
        || event.type === 'TURN_FAILED'
        || event.type === 'turn.cancelled'
        || event.type === 'TURN_CANCELLED'
        || event.type === 'execution.canceled'
        || event.type === 'EXECUTION_CANCELED'
      ) {
        ids.delete(event.turnId)
      }
    }
    for (const key of decidedPlanKeys) {
      ids.delete(key)
    }
    return ids
  }, [activity.events, cancelledTurnIds, conversation?.turns, decidedPlanKeys, resolvedPermissionDecisions])

  const pendingToolPermissions = useMemo<DockToolPermissionRequest[]>(() => {
    const map = new Map<string, DockToolPermissionRequest>()
    const rawPendingFromState = (activity as { pendingPermissions?: DockToolPermissionRequest[] }).pendingPermissions
    if (Array.isArray(rawPendingFromState)) {
      for (const req of rawPendingFromState) {
        if (
          req &&
          req.requestId &&
          (req.state ?? 'pending') === 'pending' &&
          req.toolName !== 'exit_plan_mode' &&
          !req.planApproval &&
          !resolvedPermissionDecisions.has(req.requestId)
        ) {
          map.set(req.requestId, req)
        }
      }
    }
    for (const event of activity.events) {
      const req = event.toolPermission as DockToolPermissionRequest | undefined
      if (!req || !req.requestId) continue
      if (req.toolName === 'exit_plan_mode' || req.planApproval || event.planApproval) continue
      const turnId = req.turnId ?? event.turnId
      const isSettled =
        resolvedPermissionDecisions.has(req.requestId) ||
        req.state === 'allowed' ||
        req.state === 'denied' ||
        event.state === 'completed' ||
        event.state === 'failed' ||
        event.state === 'cancelled' ||
        (turnId ? cancelledTurnIds.has(turnId) || !openQuestionTurnIds.has(turnId) : false)
      if (isSettled) {
        map.delete(req.requestId)
      } else {
        map.set(req.requestId, { ...req, turnId })
      }
    }
    return Array.from(map.values())
  }, [activity, cancelledTurnIds, openQuestionTurnIds, resolvedPermissionDecisions])

  const activeTurn = useMemo(
    () =>
      [...(conversation?.turns ?? [])]
        .reverse()
        .find((turn) => !cancelledTurnIds.has(turn.turn_id)) ?? null,
    [cancelledTurnIds, conversation?.turns],
  )

  const hasTerminalEventForActiveTurn = useMemo(() => {
    if (!activeTurn) return false
    return activity.events.some(
      (event) =>
        !event.parentAgentId
        && (!event.turnId || event.turnId === activeTurn.turn_id)
        && terminalConversationState(event) !== null,
    )
  }, [activeTurn, activity.events])

  const lastRootEvent = useMemo(
    () => [...activity.events].reverse().find((event) => !event.parentAgentId) ?? null,
    [activity.events],
  )
  const lastActivityTurnId = lastRootEvent?.turnId
  const isActivityTerminal =
    (activity.status === 'completed'
      || activity.status === 'failed'
      || activity.status === 'cancelled')
    && (lastRootEvent ? terminalConversationState(lastRootEvent) !== null : !activeTurn)
    && (!lastActivityTurnId || !activeTurn || lastActivityTurnId === activeTurn.turn_id)

  // A waiting or terminal turn must keep the question/plan card and normal
  // composer usable and immediately hide AgentPulse even if the conversation
  // snapshot briefly still reports the preceding running state.
  const running =
    !stopping
    && RUNNING_STATES.has(conversation?.state ?? '')
    && activeTurn !== null
    && RUNNING_STATES.has(activeTurn.state)
    && openQuestionTurnIds.size === 0
    && !hasTerminalEventForActiveTurn
    && !isActivityTerminal

  const friendDisplayName = friendPersona?.display_name || (conversationMode === 'friend' ? conversation?.title : undefined) || 'Orin'

  // Auto-open LiveVoiceStage when navigated from background wake word (`?live=1` or `orin:open-friend-live`).
  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const locState = (location.state as { openLiveVoice?: boolean; initialLiveUtterance?: string } | null) ?? null
    const wantsLive = params.get('live') === '1' || Boolean(locState?.openLiveVoice)
    if (!wantsLive) return
    const utterance = (params.get('utterance') ?? locState?.initialLiveUtterance ?? '').trim()
    if (utterance) {
      pendingWakeUtteranceRef.current = utterance
    }
    queueMicrotask(() => setLiveVoiceOpen(true))
    navigate(conversationPath, { replace: true, state: null })
  }, [conversationPath, location.search, location.state, navigate])

  useEffect(() => {
    const onOpenFriendLive = (event: Event) => {
      const detail = (event as CustomEvent<{ conversationId?: string; commandRemainder?: string }>).detail
      if (detail?.conversationId && detail.conversationId !== conversationId) return
      const remainder = detail?.commandRemainder?.trim() ?? ''
      if (remainder) {
        pendingWakeUtteranceRef.current = remainder
      }
      queueMicrotask(() => setLiveVoiceOpen(true))
    }
    window.addEventListener('orin:open-friend-live', onOpenFriendLive)
    return () => window.removeEventListener('orin:open-friend-live', onOpenFriendLive)
  }, [conversationId])

  // The persona is the friend conversation's identity: load it with the conversation or when live voice opens.
  useEffect(() => {
    if (conversationMode !== 'friend' && !liveVoiceOpen) return
    const controller = new AbortController()
    getFriendPersona(client, controller.signal).then(setFriendPersona).catch(() => {})
    return () => controller.abort()
  }, [client, conversationMode, conversationId, liveVoiceOpen])
  useEffect(() => {
    if (!friendSoulOpen) return
    const controller = new AbortController()
    getFriendPersona(client, controller.signal).then(setFriendPersona).catch(() => {})
    listFriendMemories(client, controller.signal).then(setFriendMemories).catch(() => {})
    return () => controller.abort()
  }, [client, friendSoulOpen])

  const liveVoiceTranscriptItems = useMemo<LiveVoiceTranscriptItem[]>(() => {
    const turnById = new Map((conversation?.turns ?? []).map((t) => [t.turn_id, t]))
    return messages
      .filter((m): m is ConversationMessage & { role: 'user' | 'assistant' } =>
        (m.role === 'user' || m.role === 'assistant') && m.content.trim().length > 0,
      )
      .slice(-6)
      .map((m) => {
        const rawCreatedAt =
          (m as { created_at?: string }).created_at ??
          (m.turn_id ? turnById.get(m.turn_id)?.created_at : undefined)
        return {
          id: m.message_id,
          role: m.role === 'user' ? 'user' : 'assistant',
          content: m.content,
          timestamp: rawCreatedAt,
        }
      })
  }, [conversation?.turns, messages])

  const latestDelegatedCodeSession = useMemo(
    () => [...activity.events].reverse().find((ev) => Boolean(ev.delegatedCodeSession))?.delegatedCodeSession ?? null,
    [activity.events],
  )

  // What the Live voice should say: the turn's `<speak>` chunks as they stream,
  // plus any unspoken remainder (or full cleaned answer when no `<speak>` arrived).
  const assistantSpeech = useMemo(
    () => buildAssistantSpeech(activity.events, messages),
    [activity.events, messages],
  )

  const liveWorkingStatus = useMemo(() => {
    if (!running) return null
    const latestTool = [...activity.events].reverse().find((event) => event.kind === 'tool' && !event.parentAgentId)
    return latestTool && latestTool.state === 'working' ? latestTool.summary || null : null
  }, [activity.events, running])

  const liveWorkStep = useMemo(() => {
    if (!running) return null
    const turnScopedEvents = activeTurn?.turn_id
      ? activity.events.filter((ev) => ev.turnId === activeTurn.turn_id || !ev.turnId)
      : activity.events
    const latestActive = [...turnScopedEvents]
      .reverse()
      .find((ev) => ev.kind === 'tool' || ev.kind === 'agent' || ev.kind === 'lifecycle')
    const title = liveWorkingStatus ?? latestActive?.summary ?? 'Em andamento…'
    const subtitle = latestActive?.detail ?? latestActive?.tool ?? null
    return { title, subtitle }
  }, [activeTurn, activity.events, liveWorkingStatus, running])

  const userInitial = useMemo(() => {
    try {
      const stored = localStorage.getItem('orin.user.displayName') ?? localStorage.getItem('agentos.user.name') ?? ''
      const trimmed = stored.trim()
      if (trimmed) return trimmed.charAt(0).toUpperCase()
    } catch {
      // Ignore storage access issues
    }
    return 'V'
  }, [])

  // The queue lives on the server: read it when the chat opens (a reload keeps it visible) and whenever the turn
  // starts or ends (a turn that ends has read what was queued for it).
  useEffect(() => {
    if (!conversationId) return
    let cancelled = false
    listQueue(client, conversationId)
      .then((items) => { if (!cancelled) setQueue(items) })
      .catch(() => undefined) // the queue is a convenience: a failed read keeps the last list
    return () => { cancelled = true }
  }, [client, conversationId, running])

  // W7: the person's messages in this conversation, oldest first, for up/down and ctrl+r in the composer.
  const sentHistory = useMemo(
    () => messages.filter((item) => item.role === 'user' && item.content.trim() && !item.message_id.startsWith('pending-')).map((item) => item.content),
    [messages],
  )

  const chatCommands = useChatCommands({
    client, conversationId, navigate, messages,
    title: conversation?.title ?? '', modelId: effectiveChatModelId, provider: effectiveChatProvider ?? '',
    permissionMode: codePermissionMode, mode: conversationMode, workspacePath: conversation?.workspace?.path ?? null,
    send: async (text) => {
      if (running) { setCommandNotice('Espere o turno atual terminar para enviar.'); return }
      setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: text, status: 'completed', retryable: false, attachments: [], command: null })
      try {
        await sendChatMessage(text, [])
        await loadSnapshot(false)
      } catch {
        setPendingUserMessage(null)
        setError('Não foi possível enviar. Tente novamente.')
      }
    },
    reload: async () => { await loadSnapshot(false) },
    openRewind: () => { void openRewind() },
    openTasks: () => setTasksOpen(true),
    setAccent: theme.setAccent,
    rename: async (title) => {
      const result = await renameConversation(client, conversationId, title)
      setConversation((current) => (current ? { ...current, title: result.title } : current))
      window.dispatchEvent(new Event('orin:conversations-updated'))
      setCommandNotice(`Conversa renomeada: ${result.title}`)
    },
    branch: async (title) => {
      const fork = await forkConversation(client, conversationId, title ? { title } : {})
      window.dispatchEvent(new Event('orin:conversations-updated'))
      navigate(`/chats/${fork.conversationId}`)
    },
    fork: async (prompt) => {
      const fork = await forkConversation(client, conversationId)
      window.dispatchEvent(new Event('orin:conversations-updated'))
      if (prompt) await sendConversationMessage(client, fork.conversationId, prompt, [], chatSelection, 'code')
      setCommandNotice(`Cópia criada: ${fork.title}${prompt ? ' (o pedido roda lá)' : ''}`)
    },
    notify: setCommandNotice,
    openInfo: setInfoPanel,
    openPreferences: () => setPreferencesOpen(true),
    openModelPicker: () => setModelPickerSignal((value) => value + 1),
    setPermissionMode: (mode) => changeCodePermissionMode(mode),
  })

  const showAgentPulse =
    running
    && activeTurn !== null
    && RUNNING_STATES.has(activeTurn.state)
    && !isActivityTerminal

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
  }, [messages, activity.events, activity.cursor, showAgentPulse])

  useLayoutEffect(() => {
    document
      .querySelectorAll('.workspace-navigation .project-navigation__chat-time')
      .forEach((el) => el.setAttribute('aria-hidden', 'true'))
  }, [chats])

  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return
    const threadEl = threadRef.current
    if (!threadEl) return
    const observer = new ResizeObserver(() => {
      if (!pinnedRef.current) return
      const scrollEl = scrollRef.current
      if (!scrollEl) return
      scrollEl.scrollTop = scrollEl.scrollHeight
    })
    observer.observe(threadEl)
    return () => observer.disconnect()
  }, [conversationId])

  const scrollToLatest = useCallback(() => {
    const element = scrollRef.current
    if (!element) return
    pinnedRef.current = true
    setAtBottom(true)
    setNewActivityCount(0)
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: element.scrollHeight, behavior: reduced ? 'auto' : 'smooth' })
    else element.scrollTop = element.scrollHeight
  }, [reduced])

  const questionSubmitInFlightRef = useRef(false)

  const submitQuestionAnswers = useCallback(async (event: ConversationActivityEvent, answers: UserQuestionAnswer[]) => {
    if (!event.questions || !event.turnId || !openQuestionTurnIds.has(event.turnId) || running || questionSubmitInFlightRef.current) return
    questionSubmitInFlightRef.current = true
    const byId = new Map(event.questions.map((question) => [question.id, question]))
    const text = ['Respostas às perguntas do agente:']
    for (const answer of answers) {
      const question = byId.get(answer.id)
      if (!question) continue
      const labels = answer.selected.map((id) => question.options.find((option) => option.id === id)?.label).filter((label): label is string => Boolean(label))
      text.push(`\n${question.question}\n${labels.length ? `Seleção: ${labels.join(', ')}` : 'Seleção: nenhuma'}`)
      const note = answer.note.trim()
      if (note) text.push(`Observação: ${note}`)
    }
    const response = text.join('\n').trim()
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
    } finally {
      questionSubmitInFlightRef.current = false
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

  const submitCodeSessionProposal = useCallback(async (event: ConversationActivityEvent, decision: CodeSessionProposalDecision) => {
    if (!event.codeSessionProposal) return
    setError(null)
    const convProjectId = (conversation as { project_id?: string | null } | null)?.project_id ?? null
    const targetProjectId = decision.project_id ?? convProjectId ?? projectId ?? null
    try {
      const receipt = await createConversation(client, {
        message: decision.prompt,
        provider: decision.provider,
        model_id: decision.model_id,
        project_id: targetProjectId,
        workspace_path: decision.workspace_path,
        workspace_acknowledged_risk: true,
        mode: 'code',
        permission_mode: decision.permission_mode,
      })
      try {
        localStorage.setItem(
          `orin.launched-code-session.${event.codeSessionProposal.proposal_id}`,
          JSON.stringify({
            conversation_id: receipt.conversation_id,
            project_id: targetProjectId,
            title: decision.title,
          }),
        )
      } catch {
        // Ignore storage write errors
      }
      const nextUrl = targetProjectId
        ? `/projects/${encodeURIComponent(targetProjectId)}/chats/${encodeURIComponent(receipt.conversation_id)}`
        : `/chats/${encodeURIComponent(receipt.conversation_id)}`
      navigate(nextUrl)
    } catch (caught) {
      setError('Não foi possível iniciar a sessão no Orin Code. Tente novamente.')
      throw caught
    }
  }, [client, conversation, navigate, projectId])

  const declineCodeSessionProposal = useCallback(async (event: ConversationActivityEvent) => {
    if (!event.codeSessionProposal || running) return
    const response = 'Prefiro continuar por aqui no Chat sem abrir o Orin Code.'
    setError(null)
    setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: response, status: 'completed', retryable: false, attachments: [], command: null })
    try {
      await sendChatMessage(response)
      await loadSnapshot(false)
    } catch (caught) {
      setPendingUserMessage(null)
      setError('Não foi possível registrar sua escolha. Tente novamente.')
      throw caught
    }
  }, [loadSnapshot, running, sendChatMessage])

  const submitToolPermissionDecision = useCallback(async (event: ConversationActivityEvent, decision: ToolPermissionDecision) => {
    const request = event.toolPermission
    const turnId = request?.turnId ?? event.turnId
    if (!request || !turnId || !openQuestionTurnIds.has(turnId)) return
    setError(null)
    const resolvedState: 'allowed' | 'denied' = decision === 'deny' ? 'denied' : 'allowed'
    setResolvedPermissionDecisions((current) => {
      const next = new Map(current)
      next.set(request.requestId, resolvedState)
      return next
    })
    await submitToolPermission(client, conversationId, turnId, request.requestId, decision)
  }, [client, conversationId, openQuestionTurnIds])

  const handleResolveDockPermission = useCallback(async (
    requestId: string,
    decisionPayload: CodePermissionDockDecision,
  ) => {
    const targetReq = pendingToolPermissions.find((req) => req.requestId === requestId)
    const turnId = targetReq?.turnId ?? activeTurn?.turn_id
    if (!turnId) return
    // W7: an edit's "don't ask again" switches this session to accept-edits and allows this call once.
    const editsForSession = decisionPayload.behavior === 'allow' && decisionPayload.kind === 'edits'
    const apiDecision: ToolPermissionDecision =
      decisionPayload.behavior === 'deny'
        ? 'deny'
        : editsForSession
          ? 'allow_once'
          : decisionPayload.scope === 'project' || decisionPayload.scope === 'session'
            ? 'allow_always'
            : 'allow_once'
    if (editsForSession && codePermissionMode !== 'accept_edits') changeCodePermissionMode('accept_edits')
    const resolvedState: 'allowed' | 'denied' = apiDecision === 'deny' ? 'denied' : 'allowed'
    setError(null)
    setResolvedPermissionDecisions((current) => {
      const next = new Map(current)
      next.set(requestId, resolvedState)
      return next
    })
    try {
      await submitToolPermission(
        client,
        conversationId,
        turnId,
        requestId,
        apiDecision,
        decisionPayload.reason,
      )
    } catch {
      setResolvedPermissionDecisions((current) => {
        const next = new Map(current)
        next.delete(requestId)
        return next
      })
      setError('Não foi possível enviar a decisão de permissão. Tente novamente.')
    }
  }, [activeTurn?.turn_id, changeCodePermissionMode, client, codePermissionMode, conversationId, pendingToolPermissions])

  const handleApprovePlanAcceptEdits = useCallback(async (event: ConversationActivityEvent) => {
    const request = event.toolPermission
    const turnId = request?.turnId ?? event.turnId
    if (turnId && (!openQuestionTurnIds.has(turnId) || decidedPlanKeys.has(turnId))) return
    if (decidedPlanKeys.has(event.eventId)) return
    setError(null)
    setDecidedPlanKeys((current) => {
      const next = new Set(current)
      next.add(event.eventId)
      if (turnId) next.add(turnId)
      return next
    })
    changeCodePermissionMode('accept_edits')
    if (request && turnId) {
      await submitToolPermission(client, conversationId, turnId, request.requestId, 'allow_always')
      await loadSnapshot(false)
      return
    }
    const response = 'Plano aprovado. Pode prosseguir com accept edits.'
    setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: response, status: 'completed', retryable: false, attachments: [], command: null })
    await sendChatMessage(response)
    await loadSnapshot(false)
  }, [changeCodePermissionMode, client, conversationId, decidedPlanKeys, loadSnapshot, openQuestionTurnIds, sendChatMessage])

  const handleApprovePlanAskPermissions = useCallback(async (event: ConversationActivityEvent) => {
    const request = event.toolPermission
    const turnId = request?.turnId ?? event.turnId
    if (turnId && (!openQuestionTurnIds.has(turnId) || decidedPlanKeys.has(turnId))) return
    if (decidedPlanKeys.has(event.eventId)) return
    setError(null)
    setDecidedPlanKeys((current) => {
      const next = new Set(current)
      next.add(event.eventId)
      if (turnId) next.add(turnId)
      return next
    })
    changeCodePermissionMode('ask_permissions')
    if (request && turnId) {
      await submitToolPermission(client, conversationId, turnId, request.requestId, 'allow_once')
      await loadSnapshot(false)
      return
    }
    const response = 'Plano aprovado. Pode prosseguir pedindo permissão para edições.'
    setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: response, status: 'completed', retryable: false, attachments: [], command: null })
    await sendChatMessage(response)
    await loadSnapshot(false)
  }, [changeCodePermissionMode, client, conversationId, decidedPlanKeys, loadSnapshot, openQuestionTurnIds, sendChatMessage])

  const handleRequestPlanChanges = useCallback(async (feedback: string, event: ConversationActivityEvent) => {
    const request = event.toolPermission
    const turnId = request?.turnId ?? event.turnId
    if (turnId && (!openQuestionTurnIds.has(turnId) || decidedPlanKeys.has(turnId))) return
    if (decidedPlanKeys.has(event.eventId)) return
    setError(null)
    setDecidedPlanKeys((current) => {
      const next = new Set(current)
      next.add(event.eventId)
      if (turnId) next.add(turnId)
      return next
    })
    if (request && turnId) {
      await submitToolPermission(client, conversationId, turnId, request.requestId, 'deny')
    }
    const normalizedFeedback = feedback.trim()
    setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: normalizedFeedback, status: 'completed', retryable: false, attachments: [], command: null })
    await sendChatMessage(normalizedFeedback)
    await loadSnapshot(false)
  }, [client, conversationId, decidedPlanKeys, loadSnapshot, openQuestionTurnIds, sendChatMessage])

  const copyAssistantMessage = useCallback(async (messageId: string, content: string) => {
    if (!content.trim()) return
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(content)
      }
      setCopiedMessageId(messageId)
      window.setTimeout(() => {
        setCopiedMessageId((current) => (current === messageId ? null : current))
      }, 1800)
    } catch {
      // Ignore clipboard failures in restricted environments.
    }
  }, [])

  /** W7-R13: one submit at a time, so a second Enter while a command (`/fork`, `/compact`…) runs does not run it again. */
  /**
   * `afterStop` (W7-R14): send-now has just stopped the turn, but this closure still sees it running; the
   * stop-related checks are then skipped, because the turn is known to have stopped.
   */
  async function submit(override?: string, afterStop = false, liveModeOverride?: boolean) {
    if (submitInFlight.current) return
    submitInFlight.current = true
    try {
      await runSubmit(override, afterStop, liveModeOverride)
    } finally {
      submitInFlight.current = false
    }
  }

  useEffect(() => {
    if (!liveVoiceOpen || loading || !conversation) return
    const pendingUtterance = pendingWakeUtteranceRef.current
    if (!pendingUtterance) return
    pendingWakeUtteranceRef.current = null
    void submit(pendingUtterance, false, true)
  })

  async function runSubmit(override: string | undefined, afterStop: boolean, liveModeOverride?: boolean) {
    const text = (override ?? message).trim()
    const readyUploads = readyUploadIds()
    const turnBusy = !afterStop && (running || stopping)
    // W7 `!command`: a shell turn in the session's folder (Code conversations only).
    if (conversationMode === 'code' && text.startsWith('!') && text.length > 1 && readyUploads.length === 0) {
      if (turnBusy) { setCommandNotice('Espere o turno atual terminar para rodar um comando.'); return }
      setMessage('')
      setError(null)
      try {
        await runShell(client, conversationId, text.slice(1).trim())
        await loadSnapshot(false)
      } catch (caught) {
        setMessage(text)
        setError(caught instanceof ApiError && caught.status === 404 ? 'O modo shell não está disponível neste Orin.' : 'Não foi possível rodar o comando.')
      }
      return
    }
    // W7: a built-in slash command runs here (even while a turn runs); a skill or plugin command is sent as before.
    if (text.startsWith('/') && readyUploads.length === 0 && !(stopping && !afterStop)) {
      let outcome: Awaited<ReturnType<typeof chatCommands.run>> | { kind: 'failed' }
      try {
        outcome = await chatCommands.run(text)
      } catch {
        outcome = { kind: 'failed' }
      }
      if (outcome.kind === 'handled') { setMessage(''); setError(null); return }
      if (outcome.kind === 'excluded') { setError(`/${outcome.name} não existe no Orin.`); return }
      if (outcome.kind === 'failed') { setError('O comando não pôde ser concluído. Tente novamente.'); return }
    }
    if ((!text && readyUploads.length === 0) || turnBusy) return
    if (text.length > MAX_MESSAGE_CHARACTERS) {
      setOversizedInput({ text, attachments: readyUploads })
      return
    }
    setMessage('')
    setError(null)
    setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: text, status: 'completed', retryable: false, attachments: [], command: null })
    try {
      await sendChatMessage(text, readyUploads, liveModeOverride)
      resetAttachments()
      await loadSnapshot(false)
      void listConversations(client).then((value) => setChats(value.items)).catch(() => undefined)
      window.dispatchEvent(new Event('orin:conversations-updated'))
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
      window.dispatchEvent(new Event('orin:conversations-updated'))
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

  const stop = useCallback(async () => {
    if (stopping) return
    setStopping(true)
    const runningIds = (conversation?.turns ?? [])
      .filter((turn) => RUNNING_STATES.has(turn.state))
      .map((turn) => turn.turn_id)
    if (runningIds.length > 0) {
      setCancelledTurnIds((current) => new Set([...current, ...runningIds]))
      setConversation((current) => current === null ? current : {
        ...current,
        state: 'cancelled',
        turns: current.turns.map((turn) => runningIds.includes(turn.turn_id)
          ? { ...turn, state: 'cancelled', finished_at: turn.finished_at ?? new Date().toISOString() }
          : turn),
      })
    }
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
      window.dispatchEvent(new Event('orin:conversations-updated'))
    } catch {
      setError('Não foi possível parar a execução.')
    } finally {
      setStopping(false)
    }
  }, [client, conversation?.turns, conversationId, loadSnapshot, stopping])

  /** W7: Enter while a turn runs queues the text for it. */
  async function queueMessage() {
    const text = message.trim()
    if (!text || text.startsWith('/')) return
    setMessage('')
    try {
      const receipt = await enqueueMessage(client, conversationId, text)
      if (!receipt.queued) await loadSnapshot(false) // the turn had already ended: the text was sent as a new turn
      await refreshQueue()
    } catch {
      setMessage(text)
      setError('Não foi possível pôr a mensagem na fila.')
    }
  }

  /** W7 "send now": stop the running turn, wait until it has stopped, then send the text. */
  /** W7-R9: waits until the stopped turn is no longer running; false when it still runs after the wait. */
  async function waitUntilStopped(): Promise<boolean> {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const snapshot = await loadSnapshot(false).catch(() => null)
      if (snapshot && !snapshot.turns.some((turn) => RUNNING_STATES.has(turn.state))) return true
      await new Promise((resolve) => setTimeout(resolve, 500))
    }
    return false
  }

  async function sendNow(text: string, queueId?: string) {
    const clean = text.trim()
    if (!clean || queueBusy) return
    setQueueBusy(true)
    try {
      await stop()
      if (!(await waitUntilStopped())) {
        setError('O turno ainda está rodando; a mensagem não foi enviada. Tente de novo quando ele parar.')
        return
      }
      if (queueId) {
        // The turn may have read the item already: then the server answers 404 and the text is not sent again.
        const cancelled = await removeQueued(client, conversationId, queueId).catch((caught: unknown) => {
          if (caught instanceof ApiError && caught.status === 404) return false
          throw caught
        })
        if (!cancelled) {
          setError('O agente já leu esta mensagem.')
          return
        }
      }
      // W7-R14: a built-in command or a `!` shell turn is not a prompt: it runs as Enter would run it.
      if (clean.startsWith('/') || (conversationMode === 'code' && clean.startsWith('!'))) {
        await submit(clean, true)
        return
      }
      if (!queueId) setMessage('')
      setPendingUserMessage({ message_id: `pending-${Date.now()}`, role: 'user', content: clean, status: 'completed', retryable: false, attachments: [], command: null })
      await sendChatMessage(clean, [])
      await loadSnapshot(false)
    } catch {
      setPendingUserMessage(null)
      setError('Não foi possível enviar agora. A mensagem continua com você.')
      // The text is not lost: back in an empty composer, or else queued again behind what the person is typing.
      if (composerTextRef.current.trim()) {
        await enqueueMessage(client, conversationId, clean).catch(() => setError('Não foi possível guardar a mensagem. Ela não foi enviada.'))
      } else {
        setMessage(clean)
      }
    } finally {
      setQueueBusy(false)
      await refreshQueue()
    }
  }

  /** W7: the rewind picker; from a turn's "voltar a este ponto", it starts on that turn's message. */
  async function openRewind(turnId?: string) {
    const initialMessageId = turnId
      ? messages.find((item) => item.role === 'user' && item.turn_id === turnId)?.message_id ?? null
      : null
    setRewindState({ history: [], busy: true, error: null, initialMessageId })
    try {
      setRewindState({ history: await getFileHistory(client, conversationId), busy: false, error: null, initialMessageId })
    } catch {
      setRewindState({ history: [], busy: false, error: null, initialMessageId })
    }
  }

  async function rewindTo(messageId: string, mode: RewindMode, forceUnbacked: readonly string[] = []) {
    setRewindState((current) => (current ? { ...current, busy: true, error: null } : current))
    try {
      if (mode === 'code') {
        const result = await rewindCode(client, conversationId, messageId, forceUnbacked)
        setRewindState(null)
        setCommandNotice(`Código restaurado: ${result.restored.length + result.deleted.length} arquivo(s)${result.unbacked.length > 0 ? `, ${result.unbacked.length} sem cópia para desfazer` : ''}.`)
        await loadSnapshot(false)
        return
      }
      const result = await rewindMessage(client, conversationId, messageId, mode, forceUnbacked)
      setRewindState(null)
      window.dispatchEvent(new Event('orin:conversations-updated'))
      navigate(`/chats/${result.fork.conversationId}`, { state: { prefill: result.fork.prompt ?? '' } })
    } catch (caught) {
      const blocked = caught instanceof ApiError && caught.code === 'rewind_blocked'
      // W7: when only the size of some files blocks the rewind, the picker offers to restore them anyway.
      const files = blocked ? caught.blockedFiles : []
      const onlySize = files.length > 0 && files.every((file) => file.reason === TOO_LARGE_TO_BACK_UP)
      setRewindState((current) => (current ? {
        ...current, busy: false,
        error: blocked
          ? (onlySize ? 'Nada foi mudado ainda.' : 'Alguns arquivos não puderam ser restaurados (grandes demais ou fora da pasta); nada foi mudado.')
          : 'Não foi possível voltar a esta mensagem.',
        unbackable: onlySize ? files.map((file) => file.path) : [],
        unbackableFor: onlySize ? messageId : null,
      } : current))
    }
  }

  useEffect(() => {
    if (!running || stopping) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      if (pendingToolPermissions.length > 0) return
      if (oversizedInput || previewReference) return
      if (document.querySelector('[role="dialog"], [role="listbox"], [role="search"]')) return
      event.preventDefault()
      void stop()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [oversizedInput, pendingToolPermissions.length, previewReference, running, stop, stopping])

  const [projectMenuConversationId, setProjectMenuConversationId] = useState<string | null>(null)
  const projectMenuOpen = projectMenuConversationId === conversationId
  const [availableProjects, setAvailableProjects] = useState<Project[]>([])
  const [newProjectName, setNewProjectName] = useState('')
  const [newProjectDescription, setNewProjectDescription] = useState('')
  const [projectBusy, setProjectBusy] = useState(false)

  const toggleProjectMenu = useCallback(() => {
    setProjectMenuConversationId((openConvId) => {
      const nextOpen = openConvId !== conversationId
      if (nextOpen) {
        setNewProjectName(conversation?.title || 'Novo projeto')
        setNewProjectDescription('')
        void listProjects(client)
          .then((res) => setAvailableProjects(res.items))
          .catch(() => undefined)
      }
      return nextOpen ? conversationId : null
    })
  }, [client, conversation, conversationId])

  const handleMoveToProject = useCallback(async (targetProjectId: string | null) => {
    if (projectBusy) return
    setProjectBusy(true)
    setError(null)
    try {
      const updated = await moveConversationToProject(client, conversationId, targetProjectId)
      setConversation((prev) =>
        prev
          ? {
              ...prev,
              project_id: updated.project_id,
              project_name: updated.project_name,
              workspace: updated.workspace ?? prev.workspace,
            }
          : prev,
      )
      setProjectMenuConversationId(null)
      window.dispatchEvent(new CustomEvent('orin:projects-updated'))
      const nextUrl = targetProjectId
        ? `/projects/${encodeURIComponent(targetProjectId)}/chats/${encodeURIComponent(conversationId)}`
        : `/chats/${encodeURIComponent(conversationId)}`
      navigate(nextUrl)
    } catch {
      setError('Não foi possível mover a sessão para o projeto.')
    } finally {
      setProjectBusy(false)
    }
  }, [client, conversationId, navigate, projectBusy])

  const handleCreateProjectFromSession = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!newProjectName.trim() || projectBusy) return
    setProjectBusy(true)
    setError(null)
    try {
      const created = await createProject(client, {
        name: newProjectName.trim(),
        description: newProjectDescription.trim(),
        workspace_path: conversation?.workspace?.kind === 'local' ? conversation.workspace.path : null,
        workspace_acknowledged_risk: true,
        source_conversation_id: conversationId,
      })
      setConversation((prev) =>
        prev
          ? {
              ...prev,
              project_id: created.project_id,
              project_name: created.name,
            }
          : prev,
      )
      setProjectMenuConversationId(null)
      window.dispatchEvent(new CustomEvent('orin:projects-updated'))
      await loadSnapshot(false).catch(() => undefined)
      navigate(`/projects/${encodeURIComponent(created.project_id)}/chats/${encodeURIComponent(conversationId)}`)
    } catch {
      setError('Não foi possível criar o projeto a partir desta sessão.')
    } finally {
      setProjectBusy(false)
    }
  }, [client, conversation, conversationId, loadSnapshot, navigate, newProjectDescription, newProjectName, projectBusy])


  const activeConvProjectId = conversation?.project_id ?? projectId ?? null
  const activeConvProjectName =
    conversation?.project_name
    ?? conversation?.workspace?.projectName
    ?? (activeConvProjectId ? availableProjects.find((p) => p.project_id === activeConvProjectId)?.name ?? null : null)

  const { codeWorkbenchFiles, codeWorkbenchTerminalEntries, detectedPreviewUrl, latestFilePatchesByPath } = useMemo(() => {
    const fileMap = new Map<string, CodeWorkbenchFileItem>()
    const terminalList: CodeWorkbenchTerminalEntry[] = []
    const patchesByPath: Record<string, StructuredPatchHunk[]> = {}
    let previewUrl: string | undefined

    for (const event of activity.events) {
      const candidateUrl = event.detectedUrl ?? event.bashOutput?.detected_url
      if (candidateUrl) {
        previewUrl = candidateUrl
      }

      const toolName = (event.toolName ?? '').toLowerCase()
      const toolKind = (event.toolKind ?? '').toLowerCase()
      const rawPath =
        event.path
        ?? event.filePath
        ?? event.diff?.path
        ?? (toolKind === 'filesystem' && event.label && !event.todos ? event.label : undefined)

      const isWriteOrEdit =
        toolName === 'write_file'
        || toolName === 'edit_file'
        || toolName === 'create_file'
        || event.type === 'code_mode.file_edited'
        || Boolean(event.structuredPatch?.length)
        || Boolean(event.diff)
      const isRead =
        toolName === 'read_file'
        || toolName === 'view_file'
        || (toolKind === 'filesystem' && !isWriteOrEdit && toolName !== 'todo_write')

      if (rawPath && (isWriteOrEdit || isRead)) {
        const cleanPath = rawPath.trim()
        if (cleanPath) {
          const patch = event.structuredPatch ?? event.diff?.structured_patch
          if (patch && patch.length > 0) {
            patchesByPath[cleanPath] = patch
          }
          const existing = fileMap.get(cleanPath)
          const nextStatus: 'modified' | 'inspected' =
            isWriteOrEdit || existing?.status === 'modified' ? 'modified' : 'inspected'
          fileMap.set(cleanPath, {
            path: cleanPath,
            status: nextStatus,
            addedLines: event.addedLines ?? event.diff?.added_lines ?? existing?.addedLines,
            removedLines: event.removedLines ?? event.diff?.removed_lines ?? existing?.removedLines,
            structuredPatch: patch && patch.length > 0 ? patch : existing?.structuredPatch,
          })
        }
      }

      const isTerminal =
        toolKind === 'terminal'
        || toolName === 'run_command'
        || toolName === 'bash'
        || Boolean(event.command)
        || Boolean(event.bashOutput?.command)

      if (isTerminal) {
        const command = event.command ?? event.bashOutput?.command ?? event.label ?? 'command'
        const stdout = event.bashOutput?.stdout
        const stderr = event.bashOutput?.stderr
        const output = [stdout, stderr, event.outputTail].filter(Boolean).join('\n') || undefined
        const status: CodeWorkbenchTerminalEntry['status'] =
          event.state === 'failed'
            ? 'failed'
            : event.state === 'working' || event.state === 'queued'
              ? 'running'
              : 'completed'
        terminalList.push({
          id: event.eventId || `term-${terminalList.length}`,
          command,
          output,
          status,
        })
      }
    }

    return {
      codeWorkbenchFiles: Array.from(fileMap.values()),
      codeWorkbenchTerminalEntries: terminalList,
      detectedPreviewUrl: previewUrl,
      latestFilePatchesByPath: patchesByPath,
    }
  }, [activity.events])

  return (
    <main className={conversationMode === 'code' && workbenchOpen ? 'chat chat--code-split' : 'chat'}>
      <PushNotificationToast notice={pushNotice} onDismiss={dismissPushNotice} />
      {infoPanel && <CommandInfoDialog panel={infoPanel} commands={chatCommands.offered} onClose={() => setInfoPanel(null)} />}
      {tasksOpen && <TasksPanel client={client} conversationId={conversationId} onClose={() => setTasksOpen(false)} />}
      {transcriptOpen && <TranscriptView messages={messages} events={activity.events} onClose={() => setTranscriptOpen(false)} />}
      {rewindState && (
        <RewindPicker
          messages={messages.filter((item) => item.role === 'user' && !item.message_id.startsWith('pending-')).map((item) => ({ messageId: item.message_id, content: item.content }))}
          history={rewindState.history}
          busy={rewindState.busy}
          error={rewindState.error}
          initialMessageId={rewindState.initialMessageId ?? null}
          unbackable={rewindState.unbackable ?? []}
          unbackableFor={rewindState.unbackableFor ?? null}
          projectName={activeConvProjectName}
          filePatches={latestFilePatchesByPath}
          onRewind={(messageId, mode, force) => { void rewindTo(messageId, mode, force) }}
          onClose={() => setRewindState(null)}
        />
      )}
      <header className="chat__bar canvas-header" data-testid="canvas-header">
        <div className="chat__bar-left canvas-header__left">
          {effectiveChatProvider && (
            <ModelPicker
              variant="unified"
              providers={[...PROVIDER_NAMES]}
              provider={effectiveChatProvider}
              onProviderChange={changeChatProvider}
              models={chatModels}
              modelId={effectiveChatModelId}
              onModelChange={setChatModelId}
              loading={chatModelsLoading}
              failed={chatModelsFailed}
              disabled={running || stopping}
              openSignal={modelPickerSignal}
            />
          )}
          {conversationMode === 'code' && (
            <span className="chat__code-badge" data-testid="orin-code-badge" title="Modo Orin Code">
              &gt;_<span className="visually-hidden"> Orin Code</span>
            </span>
          )}
          {conversationMode === 'code' && (conversation?.workspace?.folderName || conversation?.workspace?.path) && (
            <span
              className="visually-hidden"
              data-testid="orin-code-workspace-chip"
              title={conversation?.workspace?.path ?? undefined}
            >
              {conversation?.workspace?.folderName ?? conversation?.workspace?.path}
            </span>
          )}
          {activeConvProjectId && activeConvProjectName ? (
            <Link
              to={`/projects/${encodeURIComponent(activeConvProjectId)}`}
              className="chat__workspace-chip"
              data-testid="conversation-project-badge"
              title={`Projeto: ${activeConvProjectName}`}
            >
              {activeConvProjectName}
            </Link>
          ) : null}
        </div>

        <div className="chat__title canvas-header__center">
          <h1 title={conversation?.title ?? 'Conversa'}>{conversation?.title ?? 'Conversa'}</h1>
          {conversation && <span className="chat__model visually-hidden">{conversation.provider} · {conversation.model_id}</span>}
        </div>

        <div className="chat__bar-actions canvas-header__right">
          {conversationMode === 'code' && (
            <>
              <button
                type="button"
                className="chat__history-btn"
                data-testid="code-open-history-button"
                onClick={() => { void openRewind() }}
              >
                <span aria-hidden="true">🕒</span>
                <span>Histórico</span>
              </button>
              <button
                type="button"
                className={workbenchOpen ? 'chat__history-btn chat__workbench-toggle-btn is-active' : 'chat__history-btn chat__workbench-toggle-btn'}
                data-testid="code-workbench-toggle"
                aria-pressed={workbenchOpen}
                aria-expanded={workbenchOpen}
                onClick={() => setWorkbenchOpen((prev) => !prev)}
              >
                <span aria-hidden="true">◫</span>
                <span>Bancada</span>
                {codeWorkbenchFiles.length > 0 && (
                  <span className="chat__workbench-toggle-badge">{codeWorkbenchFiles.length}</span>
                )}
              </button>
            </>
          )}
          {conversationMode !== 'code' && (
            <button
              type="button"
              className="chat__open-code-btn"
              data-testid="chat-open-in-code-button"
              onClick={() => navigate(activeConvProjectId ? `/projects/${encodeURIComponent(activeConvProjectId)}/new?mode=code` : '/?mode=code')}
            >
              <span aria-hidden="true">&lt;&gt;</span>
              <span>Abrir no Code</span>
            </button>
          )}
          <button
            type="button"
            className={teamViewOpen ? 'chat__team-toggle-btn is-active' : 'chat__team-toggle-btn'}
            data-testid="chat-team-view-toggle"
            aria-pressed={teamViewOpen}
            onClick={() => setTeamViewOpen((prev) => !prev)}
          >
            <span>Equipe em atividade</span>
          </button>
          <ContextIndicator usage={contextUsage} />

          <div className="chat__project-control" data-testid="conversation-project-control">
            <button
              type="button"
              className={projectMenuOpen ? 'ghost-button chat__icon-action is-active' : 'ghost-button chat__icon-action'}
              onClick={toggleProjectMenu}
              aria-expanded={projectMenuOpen}
              aria-label={activeConvProjectId ? 'Trocar projeto' : 'Enviar para projeto'}
              title={activeConvProjectId ? `Projeto: ${activeConvProjectName ?? 'Trocar projeto'}` : 'Organizar em projeto e detalhes da sessão'}
              data-testid="conversation-project-trigger"
            >
              <svg className="chat__action-icon" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h2.88a1.5 1.5 0 0 1 1.06.44l.87.87a.5.5 0 0 0 .35.15h3.84A1.5 1.5 0 0 1 14 5.96v5.54A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5v-7Z" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M8 6.75v3.5M6.25 8.5h3.5" strokeLinecap="round" />
              </svg>
              <span className="visually-hidden">
                {activeConvProjectId ? 'Trocar projeto' : 'Enviar para projeto'}
              </span>
            </button>
            {projectMenuOpen && (
              <div
                role="dialog"
                aria-label="Organizar em projeto"
                data-testid="conversation-project-menu"
                className="chat__session-popover"
              >
                {(conversation?.workspace?.path || conversation?.turns.some((turn) => turn.scheduled_by_schedule_id)) && (
                  <div className="chat__session-popover-meta">
                    <p className="eyebrow" style={{ margin: '0 0 4px' }}>DETALHES DA SESSÃO</p>
                    {conversation?.workspace?.path && (
                      <p className="chat__session-popover-path" title={conversation.workspace.path}>
                        Pasta: <code>{conversation.workspace.folderName ?? conversation.workspace.path}</code>
                      </p>
                    )}
                    {conversation?.turns.some((turn) => turn.scheduled_by_schedule_id) && (
                      <p className="chat__session-popover-path">Origem: execuções agendadas</p>
                    )}
                  </div>
                )}
                <p className="eyebrow" style={{ margin: '0 0 6px' }}>MOVER PARA PROJETO</p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', maxHeight: '140px', overflowY: 'auto' }}>
                  {availableProjects.map((proj) => (
                    <button
                      key={proj.project_id}
                      type="button"
                      className="ghost-button"
                      disabled={projectBusy || proj.project_id === activeConvProjectId}
                      onClick={() => { void handleMoveToProject(proj.project_id) }}
                      style={{ justifyContent: 'flex-start', textAlign: 'left' }}
                    >
                      {proj.name}
                    </button>
                  ))}
                  {activeConvProjectId && (
                    <button
                      type="button"
                      className="ghost-button"
                      disabled={projectBusy}
                      onClick={() => { void handleMoveToProject(null) }}
                      data-testid="remove-from-project-button"
                      style={{ justifyContent: 'flex-start', textAlign: 'left' }}
                    >
                      Remover do projeto (sessão avulsa)
                    </button>
                  )}
                </div>
                <hr style={{ margin: '8px 0', border: 0, borderTop: '1px solid var(--orin-border-subtle, var(--border-subtle))' }} />
                <form onSubmit={(e) => { void handleCreateProjectFromSession(e) }} data-testid="create-project-from-session-form">
                  <p className="eyebrow" style={{ margin: '0 0 6px' }}>CRIAR PROJETO DESTA SESSÃO</p>
                  <input
                    type="text"
                    name="project-name"
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                    placeholder="Nome do novo projeto"
                    aria-label="Nome do novo projeto"
                    style={{ width: '100%', marginBottom: '6px' }}
                  />
                  <input
                    type="text"
                    name="project-description"
                    value={newProjectDescription}
                    onChange={(e) => setNewProjectDescription(e.target.value)}
                    placeholder="Descrição (opcional)"
                    aria-label="Descrição do novo projeto"
                    style={{ width: '100%', marginBottom: '8px' }}
                  />
                  <button type="submit" className="button button--primary" disabled={projectBusy || !newProjectName.trim()} style={{ width: '100%' }}>
                    Criar projeto e vincular
                  </button>
                </form>
              </div>
            )}
          </div>

          {conversationMode === 'code' && (
            <button
              type="button"
              className={workspaceDrawerOpen ? 'ghost-button chat__icon-action is-active' : 'ghost-button chat__icon-action'}
              aria-pressed={workspaceDrawerOpen}
              aria-label="Arquivos"
              title="Arquivos do workspace"
              data-testid="workspace-files-drawer-toggle"
              onClick={toggleWorkspaceDrawer}
            >
              <svg className="chat__action-icon" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h3.172a1.5 1.5 0 0 1 1.06.44l.829.828a.5.5 0 0 0 .353.147H12.5A1.5 1.5 0 0 1 14 4.915v7.585A1.5 1.5 0 0 1 12.5 14h-9A1.5 1.5 0 0 1 2 12.5v-9Z" />
              </svg>
              <span className="chat__action-label visually-hidden">Arquivos</span>
            </button>
          )}
          <button
            type="button"
            className={showOverview ? 'ghost-button chat__icon-action is-active' : 'ghost-button chat__icon-action'}
            aria-pressed={showOverview}
            aria-label="Visão geral"
            title="Visão geral da conversa"
            onClick={() => navigate(showOverview ? conversationPath : `${conversationPath}/overview`)}
          >
            <svg className="chat__action-icon" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
              <circle cx="8" cy="8" r="5.5" />
              <circle cx="8" cy="8" r="2" />
            </svg>
            <span className="chat__action-label visually-hidden">Visão geral</span>
          </button>
          {conversationMode === 'friend' && (
            <button
              type="button"
              className={friendSoulOpen ? 'ghost-button chat__icon-action chat__text-action is-active' : 'ghost-button chat__icon-action chat__text-action'}
              aria-pressed={friendSoulOpen}
              aria-label={`Personalizar ${friendDisplayName}: nome, aparência, personalidade e memórias`}
              title={`Personalizar ${friendDisplayName}`}
              data-testid="open-friend-soul-drawer-button"
              onClick={() => setFriendSoulOpen((prev) => !prev)}
            >
              <svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4z" /><path d="M19 15l.8 1.9L21.7 18l-1.9.8L19 20.7l-.8-1.9L16.3 18l1.9-1.1z" /></svg>
              <span className="chat__action-label">Personalizar</span>
            </button>
          )}
          <button
            type="button"
            className={liveVoiceSettingsOpen ? 'ghost-button chat__icon-action chat__text-action is-active' : 'ghost-button chat__icon-action chat__text-action'}
            aria-pressed={liveVoiceSettingsOpen}
            aria-label="Configurar voz e microfone"
            title="Configurar voz e microfone"
            data-testid="open-live-voice-settings-button"
            onClick={() => setLiveVoiceSettingsOpen((prev) => !prev)}
          >
            <svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
            <span className="chat__action-label">Voz</span>
          </button>
          <CommandPalette conversations={chats} />
          <button
            type="button"
            className="canvas-header__voice-btn"
            data-testid="chat-voice-button"
            onClick={() => setLiveVoiceOpen((prev) => !prev)}
          >
            <svg className="canvas-header__voice-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
              <path d="M3 6.5v3M6.3 4v8M9.7 5.2v5.6M13 6.8v2.4" />
            </svg>
            <span>Conversar por voz</span>
          </button>
          <OrinMascot size={30} variant="flat" statusDot="online" ariaLabel="Orin ativo" />
        </div>
      </header>

      {preferencesOpen && (
        <PreferencesModal
          open={preferencesOpen}
          onClose={() => setPreferencesOpen(false)}
          client={client}
        />
      )}

      <aside className="workspace-navigation">
        <WorkspaceNavigation
          client={client}
          onChatsChange={setChats}
          onOpenPreferences={() => setPreferencesOpen(true)}
          initialView={projectId ? 'projects' : undefined}
        />
      </aside>

      <div className="chat__body" style={conversationMode === 'code' && workspaceDrawerOpen ? { position: 'relative' } : undefined}>
        <div className="chat__scroll" ref={scrollRef} onScroll={(event) => {
          const element = event.currentTarget
          const nextAtBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 120
          pinnedRef.current = nextAtBottom
          setAtBottom((current) => current === nextAtBottom ? current : nextAtBottom)
          if (nextAtBottom) setNewActivityCount(0)
          if (element.scrollTop < 250 && hasOlderActivities && !loadingOlderRef.current) {
            void loadOlderActivities()
          }
        }}>
          {teamViewOpen ? (
            <TeamActivityView
              events={activity.events}
              todos={activity.todos}
              running={running}
              paused={teamPaused}
              onPauseTeam={() => setTeamPaused((prev) => !prev)}
              onBackToChat={() => setTeamViewOpen(false)}
              onResolveDecision={(optionValue) => {
                setTeamViewOpen(false)
                void submit(optionValue)
              }}
              onOpenDeliverable={(deliverable) => {
                if (deliverable.path) {
                  setPreviewReference({ conversationId, path: deliverable.path, name: deliverable.fileName })
                }
              }}
            />
          ) : (
          <div className="chat__thread" ref={threadRef} role="log" aria-live="polite" aria-relevant="additions text">
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

            {!loadFailure && messages.map((item, itemIndex) => {
              const timeline = item.role === 'assistant' ? timelinesByMessage.get(item.message_id) : undefined
              const oldestEventId = consolidatedEvents[0]?.eventId
              const isEarliestPartialTurn = Boolean(
                hasOlderActivities
                && item.role === 'assistant'
                && timeline
                && oldestEventId
                && timeline.some((seg) => seg.kind === 'activity' && seg.group.events.some((ev) => ev.eventId === oldestEventId)),
              )
              const hasSubsequentUserMessage = messages.slice(itemIndex + 1).some((candidate) => candidate.role === 'user')
              const timelineTurnIds = timeline
                ? Array.from(
                    new Set(
                      timeline.flatMap((seg) =>
                        seg.kind === 'activity'
                          ? seg.group.events.map((ev) => ev.turnId).filter((id): id is string => Boolean(id))
                          : seg.kind === 'thought' && seg.thought.turnId
                            ? [seg.thought.turnId]
                            : [],
                      ),
                    ),
                  )
                : []
              const isTurnWaitingOnUser = timelineTurnIds.some((tid) => openQuestionTurnIds.has(tid))
              const isTurnFinished =
                hasSubsequentUserMessage
                || (!isTurnWaitingOnUser && (
                  item.status === 'completed'
                  || item.status === 'failed'
                  || item.status === 'cancelled'
                ))
                || timelineTurnIds.some((tid) => {
                  if (cancelledTurnIds.has(tid) || decidedPlanKeys.has(tid)) return true
                  const turnRecord = conversation?.turns.find((t) => t.turn_id === tid)
                  return turnRecord ? (turnRecord.state === 'completed' || turnRecord.state === 'failed' || turnRecord.state === 'cancelled') : false
                })
              const effectiveOpenQuestionTurnIds = isTurnFinished ? EMPTY_TURN_IDS : openQuestionTurnIds
              const messageTurnId = item.role === 'assistant'
                ? (item.turn_id ?? timelineTurnIds[0] ?? resolveTurnId(consolidatedEvents, item.message_id))
                : null
              const matchedTurn = messageTurnId
                ? conversation?.turns.find((t) => t.turn_id === messageTurnId)
                : undefined
              const isCompletedSuccessTurn = Boolean(
                matchedTurn
                && (matchedTurn.state === 'completed' || matchedTurn.state === 'completed_with_caveats')
                && !cancelledTurnIds.has(matchedTurn.turn_id),
              )
              const turnWorkDurationLabel = isCompletedSuccessTurn
                ? formatTurnWorkDuration(matchedTurn?.started_at, matchedTurn?.finished_at)
                : null
              const retrySource = item.role === 'assistant' && item.retryable
                ? messages.slice(0, itemIndex).reverse().find((candidate) => candidate.role === 'user' && candidate.content.trim())
                : undefined
              return (
                <motion.article
                  key={item.message_id}
                  className={`bubble bubble--${item.role}`}
                  initial={reduced ? false : { opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.24, ease: [0.22, 0.61, 0.36, 1] }}
                >
                  {item.role === 'assistant' && (
                    <div className="chat__assistant-mascot" data-testid="assistant-turn-mascot">
                      <OrinMascot size={28} variant="flat" ariaHidden />
                    </div>
                  )}
                  {item.role === 'user' && (
                    <span className="chat__user-avatar" data-testid="user-message-avatar" aria-hidden="true">
                      {userInitial}
                    </span>
                  )}
                  {isEarliestPartialTurn && (
                    <div className="flex justify-start pb-2">
                      <button
                        type="button"
                        data-testid="load-older-activities"
                        disabled={loadingOlderActivities}
                        onClick={() => { void loadOlderActivities() }}
                        className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-1 text-[12px] text-[var(--text-secondary)] hover:border-[var(--accent-primary)] hover:text-[var(--text-primary)] disabled:opacity-60 transition-colors"
                      >
                        {loadingOlderActivities ? 'Carregando atividades anteriores…' : '↑ Carregar atividades anteriores deste turno'}
                      </button>
                    </div>
                  )}
                  {timeline
                    ? (
                      <TurnTimeline
                        items={timeline}
                        conversationId={conversationId}
                        client={client}
                        defaultProvider={effectiveChatProvider ?? undefined}
                        defaultModelId={effectiveChatModelId || undefined}
                        models={effectiveChatProvider ? { [effectiveChatProvider]: chatModels } : undefined}
                        mode={conversationMode}
                        permissionMode={codePermissionMode}
                        turnCompleted={isTurnFinished}
                        turnStartedAt={isCompletedSuccessTurn ? matchedTurn?.started_at : null}
                        turnFinishedAt={isCompletedSuccessTurn ? matchedTurn?.finished_at : null}
                        dockActive={true}
                        onPreview={setPreviewReference}
                        openQuestionTurnIds={effectiveOpenQuestionTurnIds}
                        onAnswer={submitQuestionAnswers}
                        onMcpApprove={submitMcpApproval}
                        onMcpDecline={declineMcpApproval}
                        onPluginApprove={submitPluginApproval}
                        onPluginDecline={declinePluginApproval}
                        onToolPermission={submitToolPermissionDecision}
                        onCodeSessionAccept={submitCodeSessionProposal}
                        onCodeSessionDecline={declineCodeSessionProposal}
                        onOpenRewindTurn={(turnId) => { void openRewind(turnId) }}
                        onApprovePlanAcceptEdits={isTurnFinished ? undefined : handleApprovePlanAcceptEdits}
                        onApprovePlanAskPermissions={isTurnFinished ? undefined : handleApprovePlanAskPermissions}
                        onRequestPlanChanges={isTurnFinished ? undefined : handleRequestPlanChanges}
                      />
                    )
                    : item.role === 'assistant' && item.content
                      ? (
                        <>
                          <MarkdownMessage content={item.content} conversationId={conversationId} client={client} onPreview={setPreviewReference} />
                          {turnWorkDurationLabel && (
                            <div
                              className="turn-timeline__work-duration font-mono text-[11px] text-[var(--text-secondary)] pt-1"
                              data-testid="turn-work-duration"
                            >
                              {turnWorkDurationLabel}
                            </div>
                          )}
                        </>
                      )
                      : item.command
                        ? <MessageCommandChip command={item.command} />
                        : item.role === 'assistant' && !item.content && (item.status === 'streaming' || item.status === 'queued')
                          ? (
                            <div className="assistant-live-placeholder" data-testid="assistant-live-placeholder">
                              <span className="agent-pulse__dot" aria-hidden="true" />
                              <span className="agent-pulse__label--shimmer">
                                {item.status === 'queued' ? 'Na fila…' : 'Pensando…'}
                              </span>
                            </div>
                          )
                          : <p>{item.content || placeholderFor(item)}</p>}
                  {item.role === 'assistant' && item.content.trim().length > 0 && item.status === 'completed' && (
                    <div className="bubble__actions" style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '6px' }}>
                      <button
                        type="button"
                        className="ghost-button"
                        aria-label="Copiar resposta"
                        data-testid={`copy-message-${item.message_id}`}
                        onClick={() => { void copyAssistantMessage(item.message_id, item.content) }}
                        style={{ fontSize: '11px', padding: '2px 8px' }}
                      >
                        {copiedMessageId === item.message_id ? 'Copiado' : 'Copiar resposta'}
                      </button>
                    </div>
                  )}
                  {item.role === 'user' && item.attachments.length > 0 && (
                    <MessageAttachments conversationId={conversationId} items={item.attachments} client={client} onPreview={setPreviewReference} />
                  )}
                  {item.retryable && <div className="bubble__retry">
                    <span role="status">Você pode tentar novamente sem perder a solicitação.</span>
                    {retrySource && <button type="button" className="ghost-button" disabled={running} onClick={() => { setMessage(retrySource.content); setError(null) }}>Usar solicitação novamente</button>}
                  </div>}
                </motion.article>
              )
            })}

            {!loadFailure && unclaimedEvents.length > 0 && (() => {
              const isUnclaimedTurnActive = openQuestionTurnIds.size > 0 && unclaimedEvents.some((ev) => ev.turnId && openQuestionTurnIds.has(ev.turnId))
              return conversationMode === 'code'
                ? (
                  <TurnTimeline
                    events={unclaimedEvents}
                    conversationId={conversationId}
                    client={client}
                    defaultProvider={effectiveChatProvider ?? undefined}
                    defaultModelId={effectiveChatModelId || undefined}
                    models={effectiveChatProvider ? { [effectiveChatProvider]: chatModels } : undefined}
                    mode="code"
                    permissionMode={codePermissionMode}
                    dockActive={true}
                    onPreview={setPreviewReference}
                    openQuestionTurnIds={openQuestionTurnIds}
                    onAnswer={submitQuestionAnswers}
                    onMcpApprove={submitMcpApproval}
                    onMcpDecline={declineMcpApproval}
                    onPluginApprove={submitPluginApproval}
                    onPluginDecline={declinePluginApproval}
                    onToolPermission={submitToolPermissionDecision}
                    onCodeSessionAccept={submitCodeSessionProposal}
                    onCodeSessionDecline={declineCodeSessionProposal}
                    onOpenRewindTurn={(turnId) => { void openRewind(turnId) }}
                    onApprovePlanAcceptEdits={isUnclaimedTurnActive ? handleApprovePlanAcceptEdits : undefined}
                    onApprovePlanAskPermissions={isUnclaimedTurnActive ? handleApprovePlanAskPermissions : undefined}
                    onRequestPlanChanges={isUnclaimedTurnActive ? handleRequestPlanChanges : undefined}
                  />
                )
                : (
                  <ActivityStream
                    events={unclaimedEvents}
                    conversationId={conversationId}
                    client={client}
                    defaultProvider={effectiveChatProvider ?? undefined}
                    defaultModelId={effectiveChatModelId || undefined}
                    models={effectiveChatProvider ? { [effectiveChatProvider]: chatModels } : undefined}
                    onPreview={setPreviewReference}
                    openQuestionTurnIds={openQuestionTurnIds}
                    onAnswer={submitQuestionAnswers}
                    onMcpApprove={submitMcpApproval}
                    onMcpDecline={declineMcpApproval}
                    onPluginApprove={submitPluginApproval}
                    onPluginDecline={declinePluginApproval}
                    onToolPermission={submitToolPermissionDecision}
                    onCodeSessionAccept={submitCodeSessionProposal}
                    onCodeSessionDecline={declineCodeSessionProposal}
                  />
                )
            })()}

            {!loadFailure && activity.events.some((ev) => Boolean(ev.delegatedCodeSession)) && (() => {
              const latestDelegated = [...activity.events].reverse().find((ev) => Boolean(ev.delegatedCodeSession))?.delegatedCodeSession
              if (!latestDelegated) return null
              return (
                <CodeSessionLiveCard
                  codeConversationId={latestDelegated.code_conversation_id}
                  goal={latestDelegated.goal}
                  status={latestDelegated.status}
                  completedTodoCount={latestDelegated.completed_todo_count}
                  totalTodoCount={latestDelegated.total_todo_count}
                  activeTaskTitle={latestDelegated.active_task_title}
                  modifiedFilesCount={latestDelegated.modified_files_count}
                  examinedFilesCount={latestDelegated.examined_files_count}
                  friendlyMilestone={latestDelegated.friendly_milestone}
                  onOpenCodeSession={(codeConvId) => navigate(`/chats/${encodeURIComponent(codeConvId)}`)}
                />
              )
            })()}
            {!loadFailure && conversationMode === 'code' && codeWorkbenchFiles.length > 0 && (
              <div className="code-changed-files-card" data-testid="code-changed-files-card">
                <div className="code-changed-files-card__header">
                  <div className="code-changed-files-card__summary">
                    <span className="code-changed-files-card__icon" aria-hidden="true">📄</span>
                    <span className="code-changed-files-card__count">
                      {codeWorkbenchFiles.length} {codeWorkbenchFiles.length === 1 ? 'arquivo alterado' : 'arquivos alterados'}
                    </span>
                    <span className="code-changed-files-card__dot" aria-hidden="true">·</span>
                    <button
                      type="button"
                      className="code-changed-files-card__btn"
                      data-testid="code-view-changes-button"
                      onClick={() => {
                        setWorkbenchOpen(true)
                        setWorkbenchTab('files')
                        setChangedFilesExpanded((prev) => !prev)
                      }}
                    >
                      Ver alterações
                    </button>
                  </div>
                  <button
                    type="button"
                    className="code-changed-files-card__chevron"
                    aria-label={changedFilesExpanded ? 'Recolher arquivos alterados' : 'Expandir arquivos alterados'}
                    onClick={() => setChangedFilesExpanded((prev) => !prev)}
                  >
                    {changedFilesExpanded ? '▴' : '▾'}
                  </button>
                </div>
                {changedFilesExpanded && (
                  <ul className="code-changed-files-card__list">
                    {codeWorkbenchFiles.map((file) => (
                      <li key={file.path} className="code-changed-files-card__item">
                        <button
                          type="button"
                          className="code-changed-files-card__file-btn"
                          onClick={() => {
                            setWorkbenchOpen(true)
                            setSelectedWorkbenchFile(file.path)
                            setWorkbenchTab('files')
                          }}
                        >
                          <span className="code-changed-files-card__file-path">{file.path}</span>
                          <span className={`code-changed-files-card__file-badge is-${file.status}`}>
                            {file.status === 'modified' ? 'Editado' : 'Lido'}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {!loadFailure && conversationMode === 'code' && activity.todos.length > 0 && (
              <div className="code-steps-checklist-card" data-testid="code-steps-checklist-card">
                <ul className="code-steps-checklist-card__list">
                  {activity.todos.map((todo, idx) => {
                    const isDone = todo.status === 'completed'
                    return (
                      <li
                        key={`${idx}-${todo.content}`}
                        className={`code-steps-checklist-card__item${isDone ? ' is-done' : ' is-pending'}`}
                      >
                        <span className="code-steps-checklist-card__check" aria-hidden="true">
                          {isDone ? '✓' : '○'}
                        </span>
                        <span className="code-steps-checklist-card__text">{todo.content}</span>
                        {!isDone && (
                          <span className="code-steps-checklist-card__status">Em andamento</span>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}
            <AnimatePresence>
              {!loadFailure && showAgentPulse && (
                <motion.div
                  key="pulse"
                  initial={reduced ? false : { opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <AgentPulse
                    mode={mode}
                    state={activity.events.at(-1)?.state ?? 'working'}
                    activeTurnId={activeTurn?.turn_id ?? null}
                  />
                </motion.div>
              )}
            </AnimatePresence>
            {!loadFailure && running && (
              <div className="live-work-progress-card" data-testid="live-work-progress-card">
                <div className="live-work-progress-card__status">
                  <span className="live-work-progress-card__dot" aria-hidden="true" />
                  <div className="live-work-progress-card__copy">
                    <span className="live-work-progress-card__title">
                      {liveWorkStep?.title ?? 'Em andamento…'}
                    </span>
                    {Boolean(liveWorkStep?.subtitle) && (
                      <span className="live-work-progress-card__subtitle">
                        {liveWorkStep?.subtitle}
                      </span>
                    )}
                  </div>
                </div>
                <div className="live-work-progress-card__actions">
                  <button
                    type="button"
                    className="live-work-progress-card__details-btn"
                    data-testid="live-work-view-details"
                    onClick={() => setTeamViewOpen(true)}
                  >
                    Ver detalhes ›
                  </button>
                  <button
                    type="button"
                    className="live-work-progress-card__stop-btn"
                    data-testid="live-work-stop-button"
                    onClick={() => { void stop() }}
                  >
                    Parar
                  </button>
                </div>
              </div>
            )}
          </div>
          )}
        </div>

        {newActivityCount > 0 && !atBottom && (
          <button type="button" className="chat__new-activity" onClick={scrollToLatest}>
            Ir para o fim · {newActivityCount} novas atividades
          </button>
        )}

        {conversationMode === 'code' && workspaceDrawerOpen && (
          <aside
            className="chat__files-drawer chat__workspace-drawer"
            data-testid="workspace-files-drawer"
            aria-label="Arquivos do workspace"
            style={{
              position: 'absolute',
              top: 0,
              right: 0,
              bottom: 0,
              width: '340px',
              maxWidth: '100%',
              background: 'var(--orin-surface-2, var(--surface-raised))',
              borderLeft: '1px solid var(--orin-border-subtle, var(--border-subtle))',
              display: 'flex',
              flexDirection: 'column',
              zIndex: 24,
              boxShadow: '-8px 0 24px rgba(0, 0, 0, 0.24)',
            }}
          >
            <div
              className="chat__workspace-drawer-header"
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '12px 16px',
                borderBottom: '1px solid var(--orin-border-subtle, var(--border-subtle))',
              }}
            >
              <h2 style={{ margin: 0, fontSize: '13px', fontWeight: 600 }}>Arquivos do workspace</h2>
              <button
                type="button"
                className="ghost-button"
                aria-label="Fechar arquivos do workspace"
                onClick={() => setWorkspaceDrawerOpen(false)}
              >
                ×
              </button>
            </div>
            <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--orin-border-subtle, var(--border-subtle))' }}>
              <input
                type="search"
                value={drawerSearchQuery}
                onChange={(event) => { void handleDrawerSearchChange(event.target.value) }}
                placeholder="Filtrar arquivos…"
                aria-label="Filtrar arquivos do workspace"
                data-testid="workspace-files-search"
                style={{
                  width: '100%',
                  padding: '6px 10px',
                  borderRadius: '8px',
                  border: '1px solid var(--orin-border-subtle, var(--border-subtle))',
                  background: 'var(--surface-base, transparent)',
                  color: 'inherit',
                  fontSize: '12px',
                }}
              />
            </div>
            <ul
              className="chat__workspace-drawer-list"
              style={{
                listStyle: 'none',
                margin: 0,
                padding: '8px 12px',
                overflowY: 'auto',
                flex: 1,
              }}
            >
              {drawerFiles.length === 0 ? (
                <li className="chat__workspace-drawer-empty" style={{ padding: '8px 4px', fontSize: '12px', opacity: 0.72 }}>
                  Nenhum arquivo listado.
                </li>
              ) : (
                drawerFiles.map((filePath) => (
                  <li key={filePath} style={{ marginBottom: '2px' }}>
                    <button
                      type="button"
                      className="ghost-button"
                      style={{ width: '100%', justifyContent: 'flex-start', textAlign: 'left', fontSize: '12px' }}
                      onClick={() => setPreviewReference({ conversationId, path: filePath, name: filePath.split('/').at(-1) })}
                    >
                      {filePath}
                    </button>
                  </li>
                ))
              )}
            </ul>
          </aside>
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

      {previewReference && (() => {
        const latestPatchEvent = [...activity.events]
          .reverse()
          .find((event) => event.path === previewReference.path && event.structuredPatch && event.structuredPatch.length > 0)
        return (
          <WorkspaceFilePreview
            reference={previewReference}
            client={client}
            onClose={closePreview}
            structuredPatch={latestPatchEvent?.structuredPatch}
            addedLines={latestPatchEvent?.addedLines}
            removedLines={latestPatchEvent?.removedLines}
          />
        )
      })()}

      {!loadFailure && <footer className="chat__foot" data-testid="chat-composer" data-at-bottom={atBottom || running || stopping}>
        {activity.connection === 'degraded' && (
          <p className="chat__connection" role="status">Atualizações em tempo real indisponíveis; tentando reconectar.</p>
        )}
        <CodePermissionDock
          requests={pendingToolPermissions}
          onResolve={(requestId, decision) => { void handleResolveDockPermission(requestId, decision) }}
        />
        {commandNotice && <p className="chat__command-notice" role="status">{commandNotice}</p>}
        <QueuePanel
          items={queue}
          busy={queueBusy}
          onRemove={(queueId) => { void removeQueued(client, conversationId, queueId).then(refreshQueue).catch(() => setError('Não foi possível tirar da fila.')) }}
          onSendNow={(item) => { void sendNow(item.content, item.queueId) }}
          onEdit={(item) => {
            setQueue((current) => current.filter((q) => q.queueId !== item.queueId))
            setMessage(item.content)
            void removeQueued(client, conversationId, item.queueId).then(refreshQueue).catch(() => {
              void refreshQueue()
              setError('Não foi possível editar item da fila.')
            })
          }}
        />
        <Composer
          value={message}
          onChange={setMessage}
          onSubmit={() => void submit()}
          onStop={() => void stop()}
          running={running}
          disabled={stopping}
          error={error}
          hint={stopping ? 'parando…' : undefined}
          placeholder={conversationMode === 'code' && messages.length > 0 ? 'Peça um ajuste...' : 'Continue a conversa…'}
          attachments={attachments}
          onAttach={onAttach}
          onRemoveAttachment={onRemoveAttachment}
          canSend={attachmentsReady && !stopping}
          notice={notice}
          commands={pluginCommands}
          skills={skills}
          builtinCommands={chatCommands.offered}
          catalogCommands={catalogCommands}
          onOpenModelPicker={() => setModelPickerSignal((value) => value + 1)}
          onRunCommand={(text) => void submit(text)}
          onQueue={() => void queueMessage()}
          onSendNow={() => void sendNow(message)}
          onOpenRewind={() => void openRewind()}
          codeMode={conversationMode}
          conversationMode={conversationMode}
          isLiveVoiceActive={liveVoiceOpen}
          onToggleLiveVoice={() => setLiveVoiceOpen((v) => !v)}
          hideCodeModeToggle={true}
          codePermissionMode={codePermissionMode}
          onCodePermissionModeChange={changeCodePermissionMode}
          todos={todosHidden ? [] : activity.todos}
          history={sentHistory}
          onSearchWorkspaceFiles={searchWorkspaceFiles}
          worktreeIsolated={worktreeIsolated}
          onWorktreeIsolatedChange={toggleWorktreeIsolation}
          worktreeBranch={worktreeBranch}
          sessionContext={{
            mode: conversationMode,
            permissionMode: codePermissionMode,
            onPermissionModeChange: changeCodePermissionMode,
            worktreeIsolated,
            onWorktreeIsolatedChange: toggleWorktreeIsolation,
            worktreeBranch,
            workspaceState: conversation?.workspace ?? MANAGED_WORKSPACE,
            onInspectWorkspace: (path) => inspectWorkspaceFolder(client, conversationId, path),
            onAttachWorkspace: (path, acknowledged) => attachWorkspaceFolder(client, conversationId, path, acknowledged),
            onDetachWorkspace: () => detachWorkspaceFolder(client, conversationId),
            onWorkspaceChange: (next) => setConversation((current) => (current ? { ...current, workspace: next } : current)),
            client,
            activeProjectId: activeConvProjectId,
            activeProjectName: activeConvProjectName,
          }}
        />
        <StatusLineBar client={client} conversationId={conversationId} refreshKey={`${running ? 'running' : 'idle'}:${conversation?.turns.length ?? 0}`} />
      </footer>}
      {!loadFailure && conversationMode === 'code' && workbenchOpen && (
        <CodeWorkbenchPanel
          files={codeWorkbenchFiles}
          terminalEntries={codeWorkbenchTerminalEntries}
          activeTab={workbenchTab}
          onTabChange={setWorkbenchTab}
          initialPreviewUrl={detectedPreviewUrl}
          running={running}
          selectedFilePath={selectedWorkbenchFile}
          onSelectFile={setSelectedWorkbenchFile}
          onClose={() => setWorkbenchOpen(false)}
        />
      )}
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
      <FriendSoulDrawer
        isOpen={friendSoulOpen}
        persona={friendPersona}
        memories={friendMemories}
        onClose={() => setFriendSoulOpen(false)}
        onStartVoice={() => {
          setFriendSoulOpen(false)
          setLiveVoiceOpen(true)
        }}
        onUpdatePersona={async (input) => {
          const updated = await updateFriendPersona(client, input)
          setFriendPersona(updated)
          if (updated.display_name) {
            setConversation((current) => (current && current.mode === 'friend' ? { ...current, title: updated.display_name } : current))
          }
          return updated
        }}
        onSyncSoul={async () => {
          const synced = await syncFriendSoul(client)
          setFriendPersona(synced)
          return synced
        }}
        onDeleteMemory={async (memoryId) => {
          await deleteFriendMemory(client, memoryId)
          setFriendMemories((prev) => prev.filter((m) => m.id !== memoryId))
        }}
      />
      <LiveVoiceStage
        isOpen={liveVoiceOpen}
        conversationId={conversationId}
        client={client}
        companionName={conversationMode === 'friend' ? friendDisplayName : 'Orin'}
        userNickname={friendPersona?.user_nickname || ''}
        orbThemeId={conversationMode === 'friend' ? friendPersona?.orb_theme ?? 'aurora' : 'aurora'}
        assistantSpeech={assistantSpeech}
        assistantBusy={running}
        workingStatusText={liveWorkingStatus}
        transcriptItems={liveVoiceTranscriptItems}
        todos={activity.todos}
        delegatedCodeSession={latestDelegatedCodeSession}
        onOpenCodeSession={(codeConvId) => {
          setLiveVoiceOpen(false)
          if (codeConvId) navigate(`/chats/${encodeURIComponent(codeConvId)}`)
          else if (activeConvProjectId) navigate(`/projects/${encodeURIComponent(activeConvProjectId)}/new?mode=code`)
          else navigate('/?mode=code')
        }}
        onUserSpeechUtterance={(spokenPrompt) => {
          if (running || stopping) {
            void stop().finally(() => {
              void submit(spokenPrompt, true, true)
            })
            return
          }
          void submit(spokenPrompt, false, true)
        }}
        onOpenPersonalize={conversationMode === 'friend' ? () => setFriendSoulOpen(true) : undefined}
        onClose={() => setLiveVoiceOpen(false)}
      />
      <LiveVoiceQuickSettings
        isOpen={liveVoiceSettingsOpen}
        client={client}
        companionName={conversationMode === 'friend' ? friendDisplayName : 'Orin'}
        onClose={() => setLiveVoiceSettingsOpen(false)}
      />
    </main>
  )
}

const MAX_SPOKEN_SUMMARY_CHARS = 2400

/** Clean speakable text without markdown/code blocks, up to a generous safety ceiling. */
export function spokenSummaryOf(content: string): string {
  const plain = content
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/^\s*\|.*\|\s*$/gm, ' ')
    .replace(/<\/?speak>/gi, ' ')
    .replace(/\[(?:hmm|pausa|riso[^\]]*|suspiro|risos?)\]/gi, ' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*•+]\s+/gm, '')
    .replace(/[*_~>|#]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  if (plain.length <= MAX_SPOKEN_SUMMARY_CHARS) return plain
  const cut = plain.slice(0, MAX_SPOKEN_SUMMARY_CHARS)
  const lastStop = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('! '), cut.lastIndexOf('? '))
  return lastStop > 240 ? cut.slice(0, lastStop + 1) : `${cut.trimEnd()}…`
}

function foldPtBrTtsNormalizations(raw: string): string {
  return raw
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/\b(\d+)\s+horas?\s+e\s+(\d+)\s+minutos?\b/g, '$1h$2')
    .replace(/\b(\d+)\s+horas?\b/g, '$1h')
    .replace(/r\$\s*(\d+(?:[.,]\d+)?)/g, '$1')
    .replace(/\b(\d+(?:[.,]\d+)?)\s+reais\b/g, '$1')
    .replace(/(\d+)\s*%/g, '$1')
    .replace(/\b(\d+)\s+por\s+cento\b/g, '$1')
    .replace(/\ba\s+p\s+i\b/g, 'api')
    .replace(/\bu\s+r\s+l\b/g, 'url')
    .replace(/\bs\s+q\s+l\b/g, 'sql')
    .replace(/\bc\s+p\s+u\b/g, 'cpu')
    .replace(/\bg\s+p\s+u\b/g, 'gpu')
    .replace(/\bt\s+t\s+s\b/g, 'tts')
    .replace(/\bs\s+t\s+t\b/g, 'stt')
    .replace(/\bl\s+l\s+m\b/g, 'llm')
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function extractUnspokenRemainder(fullSummary: string, spokenSoFar: string): string {
  if (!fullSummary || !spokenSoFar) return ''
  const trimmedSpoken = spokenSoFar.trim()
  const trimmedFull = fullSummary.trim()
  if (!trimmedSpoken) return ''

  const normFull = foldPtBrTtsNormalizations(trimmedFull)
  const normSpoken = foldPtBrTtsNormalizations(trimmedSpoken)
  if (!normFull || !normSpoken || normSpoken === normFull || normFull.endsWith(normSpoken)) return ''

  const spokenWords = normSpoken.split(/\s+/).filter(Boolean)
  const fullWords = normFull.split(/\s+/).filter(Boolean)
  if (spokenWords.length >= fullWords.length) return ''

  // If the last 3+ words of spokenSoFar already appear near the end (within the last 15% or 40 chars) of fullSummary,
  // spokenSoFar already reached the end of the message — zero remainder.
  if (spokenWords.length >= 3) {
    const lastThree = spokenWords.slice(-3).join(' ')
    const lastIdx = normFull.lastIndexOf(lastThree)
    if (lastIdx !== -1) {
      const matchEnd = lastIdx + lastThree.length
      const remainingChars = normFull.length - matchEnd
      const nearEndLimit = Math.min(40, Math.ceil(normFull.length * 0.15))
      if (remainingChars <= nearEndLimit) return ''
    }
  }

  const endsWithColon = trimmedSpoken.endsWith(':')
  const coversLessThan55Pct = normSpoken.length < normFull.length * 0.55 || trimmedSpoken.length < trimmedFull.length * 0.55
  if (!endsWithColon && !coversLessThan55Pct) return ''

  const cleanSlice = (rawRemainder: string) => {
    const cleaned = rawRemainder.replace(/^[\s:;,.–—-]+/, '').trim()
    return cleaned.length >= 8 ? cleaned : ''
  }

  const lowerFull = trimmedFull.toLowerCase()
  const lowerSpoken = trimmedSpoken.toLowerCase()
  const directIdx = lowerFull.indexOf(lowerSpoken)
  if (directIdx !== -1 && directIdx + trimmedSpoken.length < trimmedFull.length) {
    return cleanSlice(trimmedFull.slice(directIdx + trimmedSpoken.length))
  }

  // Fallback 1: match the tail of spokenSoFar in case minor punctuation differed earlier in the intro.
  const tailProbe = lowerSpoken.slice(-Math.min(36, lowerSpoken.length))
  if (tailProbe.length >= 12) {
    const tailIdx = lowerFull.indexOf(tailProbe)
    if (tailIdx !== -1 && tailIdx + tailProbe.length < trimmedFull.length) {
      return cleanSlice(trimmedFull.slice(tailIdx + tailProbe.length))
    }
  }

  // Fallback 2: word-by-word prefix alignment across punctuation/formatting/TTS-normalization differences.
  const normWord = (w: string) => {
    if (/^r\$$/i.test(w.replace(/[^\p{L}\p{N}$]/gu, ''))) return ''
    return w.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
  }
  const fullTokens = [...trimmedFull.matchAll(/\S+/g)]
  if (spokenWords.length >= 3 && fullTokens.length > spokenWords.length) {
    let sIdx = 0
    let lastMatchedEndOffset = -1
    for (const token of fullTokens) {
      const nw = normWord(token[0])
      if (!nw) continue
      if (nw === spokenWords[sIdx]) {
        sIdx += 1
        lastMatchedEndOffset = (token.index ?? 0) + token[0].length
        if (sIdx === spokenWords.length) break
      } else if (sIdx + 1 < spokenWords.length && nw === spokenWords[sIdx + 1]) {
        sIdx += 2
        lastMatchedEndOffset = (token.index ?? 0) + token[0].length
        if (sIdx >= spokenWords.length) break
      }
    }
    if (sIdx >= Math.ceil(spokenWords.length * 0.75) && lastMatchedEndOffset > 0 && lastMatchedEndOffset < trimmedFull.length) {
      return cleanSlice(trimmedFull.slice(lastMatchedEndOffset))
    }
  }
  return ''
}

export function buildAssistantSpeech(
  events: ReadonlyArray<Pick<ConversationActivityEvent, 'eventId' | 'type' | 'speakText' | 'turnId' | 'parentAgentId'> & { cleanText?: string; messageId?: string }>,
  messages: ReadonlyArray<Pick<ConversationMessage, 'message_id' | 'role' | 'content' | 'status' | 'turn_id'>>,
): Array<{ id: string; text: string; turnId?: string; messageId?: string }> {
  const items: Array<{ id: string; text: string; turnId?: string; messageId?: string }> = []
  const turnsWithSpeech = new Set<string>()
  const spokenTextByTurn = new Map<string, string>()
  const cleanTextByTurn = new Map<string, string>()
  const turnToMessageId = new Map<string, string>()
  for (const msg of messages) {
    if (msg.role === 'assistant' && msg.turn_id && msg.message_id) {
      turnToMessageId.set(msg.turn_id, msg.message_id)
    }
  }
  for (const event of events) {
    if (event.type !== 'speak.chunk_ready' || !event.speakText || event.parentAgentId) continue
    const cleanChunk = (event.cleanText ?? event.speakText).trim()
    const spokenChunk = event.speakText.trim()
    if (event.turnId) {
      turnsWithSpeech.add(event.turnId)
      const prevSpoken = spokenTextByTurn.get(event.turnId) ?? ''
      spokenTextByTurn.set(event.turnId, prevSpoken ? `${prevSpoken} ${spokenChunk}` : spokenChunk)
      const prevClean = cleanTextByTurn.get(event.turnId) ?? ''
      cleanTextByTurn.set(event.turnId, prevClean ? `${prevClean} ${cleanChunk}` : cleanChunk)
    }
    const resolvedMsgId = event.messageId ?? (event.turnId ? turnToMessageId.get(event.turnId) : undefined)
    items.push({
      id: `speak:${event.eventId}`,
      text: event.speakText,
      ...(event.turnId ? { turnId: event.turnId } : {}),
      ...(resolvedMsgId ? { messageId: resolvedMsgId } : {}),
    })
  }
  const lastCompletedAssistant = [...messages]
    .reverse()
    .find((item) => item.role === 'assistant' && item.status === 'completed' && Boolean(item.content.trim()))
  if (lastCompletedAssistant) {
    const fullSummary = spokenSummaryOf(lastCompletedAssistant.content)
    if (fullSummary) {
      const turnId = lastCompletedAssistant.turn_id ?? undefined
      const messageId = lastCompletedAssistant.message_id
      if (turnId && turnsWithSpeech.has(turnId)) {
        const cleanSpokenSoFar = spokenSummaryOf(cleanTextByTurn.get(turnId) ?? '')
        const ttsSpokenSoFar = spokenSummaryOf(spokenTextByTurn.get(turnId) ?? '')
        const remainderFromClean = extractUnspokenRemainder(fullSummary, cleanSpokenSoFar)
        const remainderFromTts = cleanSpokenSoFar === ttsSpokenSoFar
          ? remainderFromClean
          : extractUnspokenRemainder(fullSummary, ttsSpokenSoFar)
        const remainder = remainderFromClean && remainderFromTts ? remainderFromClean : ''
        if (remainder) {
          items.push({
            id: `message-rem:${messageId}`,
            text: remainder,
            turnId,
            messageId,
          })
        }
      } else {
        items.push({
          id: `message:${messageId}`,
          text: fullSummary,
          ...(turnId ? { turnId } : {}),
          messageId,
        })
      }
    }
  }
  return items
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
  if (
    event.type === 'turn.completed'
    || event.type === 'TURN_COMPLETED'
    || event.type === 'code_mode.completed'
    || event.type === 'code_mode.completed_with_caveats'
  ) {
    return 'completed'
  }
  if (
    event.type === 'turn.cancelled'
    || event.type === 'TURN_CANCELLED'
    || event.type === 'execution.canceled'
    || event.type === 'EXECUTION_CANCELED'
  ) {
    return 'cancelled'
  }
  if (event.type === 'turn.failed' || event.type === 'TURN_FAILED') {
    return event.state === 'cancelled' || event.status === 'cancelled' || event.errorCode === 'TURN_CANCELLED'
      ? 'cancelled'
      : 'failed'
  }
  if (
    event.type === 'turn.waiting_user'
    || event.type === 'code_mode.blocked'
    || event.type === 'code_mode.plan_ready'
    || event.type === 'tool.permission_required'
    || Boolean(event.planApproval)
    || event.tool === 'exit_plan_mode'
    || event.toolName === 'exit_plan_mode'
  ) {
    return 'waiting_user'
  }
  return null
}

function isResyncError(value: unknown): boolean {
  if (!(value instanceof ApiError)) return false
  const code = value.code.toLowerCase().replaceAll('-', '_')
  return value.status === 409 || code === 'cursor_invalid' || code === 'invalid_cursor' || code === 'cursor_expired' || code === 'retention_expired' || code === 'sequence_gap'
}

export type { ConversationActivityEvent }
