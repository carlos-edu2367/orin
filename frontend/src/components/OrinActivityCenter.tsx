import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createBrowserApiClient, type ApiClient } from '../api/client'
import {
  getConversation,
  listConversations,
  sendConversationMessage,
  submitToolPermission,
  trustConversationWorkspace,
  updateConversationPermissionMode,
  type Conversation,
} from '../api/conversations'
import { approveMcpServer } from '../api/mcp'
import { approvePlugin } from '../api/plugins'
import { listProjectSidebar, type ProjectSidebarItem } from '../api/projects'
import { OrinMascot } from './OrinMascot'

const LAST_SEEN_STORAGE_KEY = 'orin.activityCenter.lastSeen'
const POLL_INTERVAL_MS = 12_000

const ACTIVE_SESSION_STATES = new Set([
  'running',
  'streaming',
  'queued',
  'starting',
  'working',
  'in_progress',
])

const INSPECTABLE_SESSION_STATES = new Set([
  'waiting_user',
  'running',
  'streaming',
  'queued',
  'starting',
  'working',
  'in_progress',
])

export interface OrinActivityCenterProps {
  client?: ApiClient
  activeConversationId?: string
  celebrating?: boolean
  className?: string
  onApprovalResolved?: () => void
}

interface PendingToolPermissionApproval {
  kind: 'toolPermission'
  key: string
  conversationId: string
  conversationTitle: string
  projectId: string | null
  turnId: string
  requestId: string
  toolName: string
  command: string | null
  reason: string | null
  suggestedRule: string | null
  summary: string
}

interface PendingPlanApproval {
  kind: 'planApproval'
  key: string
  conversationId: string
  conversationTitle: string
  projectId: string | null
  planPath: string | null
  summary: string
}

interface PendingWorkspaceTrustApproval {
  kind: 'workspaceTrust'
  key: string
  conversationId: string
  conversationTitle: string
  projectId: string | null
  path: string
  summary: string
}

interface PendingPluginApproval {
  kind: 'pluginApproval'
  key: string
  conversationId: string
  conversationTitle: string
  projectId: string | null
  pluginId: string
  displayName: string
  summary: string
}

interface PendingMcpApproval {
  kind: 'mcpApproval'
  key: string
  conversationId: string
  conversationTitle: string
  projectId: string | null
  serverId: string
  displayName: string
  requiresSecrets: boolean
  summary: string
}

interface PendingInteractionApproval {
  kind: 'interaction'
  key: string
  conversationId: string
  conversationTitle: string
  projectId: string | null
  summary: string
}

type PendingApprovalItem =
  | PendingToolPermissionApproval
  | PendingPlanApproval
  | PendingWorkspaceTrustApproval
  | PendingPluginApproval
  | PendingMcpApproval
  | PendingInteractionApproval

interface UnreadSessionUpdate {
  conversationId: string
  title: string
  state: string
  updatedAt: string
  projectId: string | null
  projectName: string | null
}

function readLastSeenMap(): Record<string, string> {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(LAST_SEEN_STORAGE_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {}
    }
    const result: Record<string, string> = {}
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof k === 'string' && typeof v === 'string') {
        result[k] = v
      }
    }
    return result
  } catch {
    return {}
  }
}

function writeLastSeenMap(map: Record<string, string>): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(LAST_SEEN_STORAGE_KEY, JSON.stringify(map))
  } catch {
    // Ignore storage quota or private mode errors
  }
}

function sessionVersionStamp(item: { state: string; updatedAt?: string }): string {
  return `${item.state}:${item.updatedAt ?? ''}`
}

async function fetchConversationWithRawActivities(
  apiClient: ApiClient,
  conversationId: string,
): Promise<{ snapshot: Conversation; rawActivities: Array<Record<string, unknown>> }> {
  let rawActivities: Array<Record<string, unknown>> = []
  const proxyClient = Object.create(apiClient) as ApiClient
  proxyClient.request = async <T,>(
    options: Parameters<ApiClient['request']>[0],
  ): Promise<T> => {
    return apiClient.request<T>({
      ...(options as Parameters<ApiClient['request']>[0] & { parse: (v: unknown) => T }),
      parse: (value: unknown): T => {
        if (
          value &&
          typeof value === 'object' &&
          Array.isArray((value as Record<string, unknown>).activities)
        ) {
          rawActivities = (value as Record<string, unknown>).activities as Array<
            Record<string, unknown>
          >
        }
        return (options.parse as (v: unknown) => T)(value)
      },
    })
  }
  const snapshot = await getConversation(proxyClient, conversationId)
  return { snapshot, rawActivities }
}

function extractPendingApprovalsFromConversation(
  snapshot: Conversation,
  rawActivities: Array<Record<string, unknown>>,
  projectId: string | null,
  locallyResolved: ReadonlySet<string>,
): PendingApprovalItem[] {
  const items: PendingApprovalItem[] = []
  const resolvedRequestIds = new Set<string>()
  const nonPendingRequestIds = new Set<string>()

  for (const rawEvent of rawActivities) {
    const eventType = typeof rawEvent.event_type === 'string' ? rawEvent.event_type : ''
    const payload =
      rawEvent.payload && typeof rawEvent.payload === 'object'
        ? (rawEvent.payload as Record<string, unknown>)
        : null
    if (!payload) continue

    if (
      eventType === 'code_mode.permission_resolved' ||
      eventType === 'tool.permission_resolved'
    ) {
      const reqId =
        typeof payload.request_id === 'string'
          ? payload.request_id
          : payload.permission_request &&
              typeof payload.permission_request === 'object' &&
              typeof (payload.permission_request as Record<string, unknown>).request_id === 'string'
            ? ((payload.permission_request as Record<string, unknown>).request_id as string)
            : null
      if (reqId) {
        resolvedRequestIds.add(reqId)
      }
    }

    const permReq =
      payload.permission_request && typeof payload.permission_request === 'object'
        ? (payload.permission_request as Record<string, unknown>)
        : null
    if (permReq && typeof permReq.request_id === 'string') {
      const status = typeof permReq.status === 'string' ? permReq.status.toLowerCase() : 'pending'
      if (status !== 'pending' && status !== 'waiting_user' && status !== 'waiting_decision') {
        nonPendingRequestIds.add(permReq.request_id)
      }
    }
  }

  for (const event of snapshot.activities) {
    if (
      event.type === 'code_mode.permission_resolved' ||
      event.type === 'tool.permission_resolved'
    ) {
      const reqId = event.toolPermission?.requestId
      if (reqId) {
        resolvedRequestIds.add(reqId)
      }
    }
  }

  const fallbackTurnId =
    snapshot.turns[snapshot.turns.length - 1]?.turn_id ??
    snapshot.activities[snapshot.activities.length - 1]?.turnId ??
    'turn-1'
  const seenKeys = new Set<string>()
  const convId = snapshot.conversation_id
  const convTitle = snapshot.title || 'Conversa sem título'

  for (const event of snapshot.activities) {
    if (event.toolPermission) {
      const perm = event.toolPermission
      const key = `tool:${convId}:${perm.requestId}`
      if (
        !seenKeys.has(key) &&
        !locallyResolved.has(key) &&
        !resolvedRequestIds.has(perm.requestId) &&
        !nonPendingRequestIds.has(perm.requestId) &&
        perm.state !== 'allowed' &&
        perm.state !== 'denied' &&
        perm.state !== 'completed' &&
        perm.state !== 'resolved'
      ) {
        seenKeys.add(key)
        items.push({
          kind: 'toolPermission',
          key,
          conversationId: convId,
          conversationTitle: convTitle,
          projectId,
          turnId: perm.turnId || event.turnId || fallbackTurnId,
          requestId: perm.requestId,
          toolName: perm.toolName,
          command: perm.command ?? null,
          reason: perm.reason ?? null,
          suggestedRule: perm.suggestedRule ?? null,
          summary: event.summary || `Permissão para ${perm.toolName}`,
        })
      }
    }

    if (event.planApproval && event.planApproval.status !== 'approved') {
      const key = `plan:${convId}:${event.eventId}`
      if (!seenKeys.has(key) && !locallyResolved.has(key)) {
        seenKeys.add(key)
        items.push({
          kind: 'planApproval',
          key,
          conversationId: convId,
          conversationTitle: convTitle,
          projectId,
          planPath: event.planApproval.plan_path ?? null,
          summary: event.summary || 'Plano pronto para aprovação',
        })
      }
    }

    if (event.workspaceTrust) {
      const key = `trust:${convId}:${event.workspaceTrust.path}`
      if (!seenKeys.has(key) && !locallyResolved.has(key)) {
        seenKeys.add(key)
        items.push({
          kind: 'workspaceTrust',
          key,
          conversationId: convId,
          conversationTitle: convTitle,
          projectId,
          path: event.workspaceTrust.path,
          summary: event.summary || `Confiar na pasta ${event.workspaceTrust.path}`,
        })
      }
    }

    if (event.pluginApproval) {
      const key = `plugin:${convId}:${event.pluginApproval.plugin_id}`
      if (!seenKeys.has(key) && !locallyResolved.has(key)) {
        seenKeys.add(key)
        items.push({
          kind: 'pluginApproval',
          key,
          conversationId: convId,
          conversationTitle: convTitle,
          projectId,
          pluginId: event.pluginApproval.plugin_id,
          displayName: event.pluginApproval.display_name,
          summary: event.summary || `Aprovar plugin ${event.pluginApproval.display_name}`,
        })
      }
    }

    if (event.mcpApproval) {
      const key = `mcp:${convId}:${event.mcpApproval.server_id}`
      if (!seenKeys.has(key) && !locallyResolved.has(key)) {
        seenKeys.add(key)
        items.push({
          kind: 'mcpApproval',
          key,
          conversationId: convId,
          conversationTitle: convTitle,
          projectId,
          serverId: event.mcpApproval.server_id,
          displayName: event.mcpApproval.display_name,
          requiresSecrets: (event.mcpApproval.secret_names?.length ?? 0) > 0,
          summary: event.summary || `Conectar servidor MCP ${event.mcpApproval.display_name}`,
        })
      }
    }

    if (
      snapshot.state === 'waiting_user' &&
      ((event.questions && event.questions.length > 0) || event.codeSessionProposal)
    ) {
      const key = `interaction:${convId}:${event.eventId}`
      if (!seenKeys.has(key) && !locallyResolved.has(key)) {
        seenKeys.add(key)
        items.push({
          kind: 'interaction',
          key,
          conversationId: convId,
          conversationTitle: convTitle,
          projectId,
          summary: event.summary || 'Aguardando resposta na conversa',
        })
      }
    }
  }

  return items
}

export function OrinActivityCenter({
  client,
  activeConversationId,
  celebrating = false,
  className,
  onApprovalResolved,
}: OrinActivityCenterProps) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const navigate = useNavigate()

  const [open, setOpen] = useState(false)
  const [approvals, setApprovals] = useState<PendingApprovalItem[]>([])
  const [unreadUpdates, setUnreadUpdates] = useState<UnreadSessionUpdate[]>([])
  const [inFlightKeys, setInFlightKeys] = useState<Record<string, boolean>>({})
  const [actionError, setActionError] = useState<string | null>(null)

  const rootRef = useRef<HTMLDivElement | null>(null)
  const mountedRef = useRef<boolean>(true)
  const refreshSeqRef = useRef<number>(0)
  const inFlightRef = useRef<Set<string>>(new Set())
  const locallyResolvedRef = useRef<Set<string>>(new Set())
  const activeBackgroundIdsRef = useRef<Set<string>>(new Set())
  const hasSeededInitialRef = useRef<boolean>(false)
  const latestCatalogRef = useRef<
    Array<{
      conversationId: string
      title: string
      state: string
      updatedAt: string
      projectId: string | null
      projectName: string | null
    }>
  >([])

  const refreshActivityCenter = useCallback(async () => {
    const seq = ++refreshSeqRef.current
    const [convResp, projectResp] = await Promise.all([
      listConversations(apiClient).catch(() => ({ items: [] })),
      listProjectSidebar(apiClient).catch((): { items: ProjectSidebarItem[] } => ({ items: [] })),
    ])
    if (!mountedRef.current || seq !== refreshSeqRef.current) return

    const convList = Array.isArray(convResp?.items) ? convResp.items : []
    const projectList = Array.isArray(projectResp?.items) ? projectResp.items : []

    const catalogMap = new Map<
      string,
      {
        conversationId: string
        title: string
        state: string
        updatedAt: string
        projectId: string | null
        projectName: string | null
      }
    >()

    for (const item of convList) {
      if (!item?.conversation_id) continue
      catalogMap.set(item.conversation_id, {
        conversationId: item.conversation_id,
        title: item.title || 'Conversa sem título',
        state: item.state || 'idle',
        updatedAt: item.updated_at ?? '',
        projectId: null,
        projectName: null,
      })
    }

    for (const project of projectList) {
      for (const conv of project.chats ?? []) {
        if (!conv?.conversation_id) continue
        const existing = catalogMap.get(conv.conversation_id)
        catalogMap.set(conv.conversation_id, {
          conversationId: conv.conversation_id,
          title: conv.title || existing?.title || 'Conversa sem título',
          state: conv.state || existing?.state || 'idle',
          updatedAt: conv.updated_at ?? existing?.updatedAt ?? '',
          projectId: project.project_id,
          projectName: project.name,
        })
      }
    }

    const catalog = Array.from(catalogMap.values())
    latestCatalogRef.current = catalog

    const lastSeen = readLastSeenMap()
    const isFirstSeed = !hasSeededInitialRef.current && Object.keys(lastSeen).length === 0
    let lastSeenMutated = false

    if (isFirstSeed) {
      hasSeededInitialRef.current = true
      for (const entry of catalog) {
        if (ACTIVE_SESSION_STATES.has(entry.state)) {
          if (entry.conversationId !== activeConversationId) {
            activeBackgroundIdsRef.current.add(entry.conversationId)
          }
        } else if (entry.state === 'completed' || entry.state === 'failed') {
          lastSeen[entry.conversationId] = sessionVersionStamp(entry)
          lastSeenMutated = true
        }
      }
    } else {
      hasSeededInitialRef.current = true
    }

    if (activeConversationId) {
      const activeEntry = catalogMap.get(activeConversationId)
      if (activeEntry) {
        const stamp = sessionVersionStamp(activeEntry)
        if (lastSeen[activeConversationId] !== stamp) {
          lastSeen[activeConversationId] = stamp
          lastSeenMutated = true
        }
      }
      activeBackgroundIdsRef.current.delete(activeConversationId)
    }

    if (lastSeenMutated) {
      writeLastSeenMap(lastSeen)
    }

    const inspectCandidates = catalog.filter((entry) =>
      INSPECTABLE_SESSION_STATES.has(entry.state),
    )
    if (activeConversationId && !catalogMap.has(activeConversationId)) {
      inspectCandidates.push({
        conversationId: activeConversationId,
        title: 'Conversa atual',
        state: 'waiting_user',
        updatedAt: '',
        projectId: null,
        projectName: null,
      })
    }

    const detailResults = await Promise.all(
      inspectCandidates.map(async (entry) => {
        try {
          const { snapshot, rawActivities } = await fetchConversationWithRawActivities(
            apiClient,
            entry.conversationId,
          )
          return { entry, snapshot, rawActivities }
        } catch {
          return null
        }
      }),
    )

    const nextApprovals: PendingApprovalItem[] = []
    const conversationsWithPendingApprovals = new Set<string>()

    for (const detail of detailResults) {
      if (!detail) continue
      const extracted = extractPendingApprovalsFromConversation(
        detail.snapshot,
        detail.rawActivities,
        detail.entry.projectId,
        locallyResolvedRef.current,
      )
      if (extracted.length > 0) {
        conversationsWithPendingApprovals.add(detail.entry.conversationId)
        nextApprovals.push(...extracted)
      }
    }

    const nextUnread: UnreadSessionUpdate[] = []
    for (const entry of catalog) {
      if (entry.conversationId === activeConversationId) continue

      if (ACTIVE_SESSION_STATES.has(entry.state)) {
        activeBackgroundIdsRef.current.add(entry.conversationId)
        continue
      }

      if (conversationsWithPendingApprovals.has(entry.conversationId)) {
        continue
      }

      const stamp = sessionVersionStamp(entry)
      const wasRunningInBackground = activeBackgroundIdsRef.current.has(entry.conversationId)
      const seenStamp = lastSeen[entry.conversationId]

      if (
        (entry.state === 'completed' || entry.state === 'failed' || entry.state === 'waiting_user') &&
        (wasRunningInBackground || (seenStamp !== undefined && seenStamp !== stamp))
      ) {
        nextUnread.push({
          conversationId: entry.conversationId,
          title: entry.title,
          state: entry.state,
          updatedAt: entry.updatedAt,
          projectId: entry.projectId,
          projectName: entry.projectName,
        })
      }
    }

    if (!mountedRef.current || seq !== refreshSeqRef.current) return
    setApprovals(nextApprovals)
    setUnreadUpdates(nextUnread)
  }, [apiClient, activeConversationId])

  useEffect(() => {
    mountedRef.current = true
    void refreshActivityCenter()
    const intervalId = window.setInterval(() => {
      void refreshActivityCenter()
    }, POLL_INTERVAL_MS)

    const handleRefreshEvent = () => {
      void refreshActivityCenter()
    }

    window.addEventListener('orin:conversations-updated', handleRefreshEvent)
    window.addEventListener('orin:projects-updated', handleRefreshEvent)
    window.addEventListener('orin:activity-center-refresh', handleRefreshEvent)

    return () => {
      mountedRef.current = false
      window.clearInterval(intervalId)
      window.removeEventListener('orin:conversations-updated', handleRefreshEvent)
      window.removeEventListener('orin:projects-updated', handleRefreshEvent)
      window.removeEventListener('orin:activity-center-refresh', handleRefreshEvent)
    }
  }, [refreshActivityCenter])

  useEffect(() => {
    if (!open) return

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false)
      }
    }

    function handlePointerDown(event: PointerEvent) {
      const target = event.target as Node | null
      if (rootRef.current && target && !rootRef.current.contains(target)) {
        setOpen(false)
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    document.addEventListener('pointerdown', handlePointerDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.removeEventListener('pointerdown', handlePointerDown)
    }
  }, [open])

  const totalCount = approvals.length + unreadUpdates.length
  const hasAttention = totalCount > 0

  const triggerLabel = useMemo(() => {
    if (totalCount === 0) return 'Central do Orin (sem pendências)'
    if (approvals.length > 0 && unreadUpdates.length > 0) {
      return `Central do Orin (${approvals.length} aprovações pendentes e ${unreadUpdates.length} novidades)`
    }
    if (approvals.length > 0) {
      return `Central do Orin (${approvals.length} aprovações pendentes)`
    }
    return `Central do Orin (${unreadUpdates.length} novidades)`
  }, [approvals.length, totalCount, unreadUpdates.length])

  function navigateToConversation(conversationId: string, projectId: string | null) {
    const lastSeen = readLastSeenMap()
    const entry = latestCatalogRef.current.find((c) => c.conversationId === conversationId)
    lastSeen[conversationId] = entry ? sessionVersionStamp(entry) : `seen:${Date.now()}`
    activeBackgroundIdsRef.current.delete(conversationId)
    writeLastSeenMap(lastSeen)
    setUnreadUpdates((prev) => prev.filter((item) => item.conversationId !== conversationId))
    setOpen(false)
    navigate(
      projectId
        ? `/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(conversationId)}`
        : `/chats/${encodeURIComponent(conversationId)}`,
    )
  }

  function handleMarkAllRead() {
    const lastSeen = readLastSeenMap()
    for (const entry of latestCatalogRef.current) {
      lastSeen[entry.conversationId] = sessionVersionStamp(entry)
    }
    for (const item of unreadUpdates) {
      lastSeen[item.conversationId] = sessionVersionStamp({
        state: item.state,
        updatedAt: item.updatedAt,
      })
    }
    activeBackgroundIdsRef.current.clear()
    writeLastSeenMap(lastSeen)
    setUnreadUpdates([])
  }

  async function runGuardedAction(actionKey: string, task: () => Promise<void>) {
    if (inFlightRef.current.has(actionKey)) return
    inFlightRef.current.add(actionKey)
    setInFlightKeys((prev) => ({ ...prev, [actionKey]: true }))
    setActionError(null)

    try {
      await task()
      locallyResolvedRef.current.add(actionKey)
      setApprovals((prev) => prev.filter((item) => item.key !== actionKey))
      window.dispatchEvent(new CustomEvent('orin:conversations-updated'))
      window.dispatchEvent(new CustomEvent('orin:activity-center-refresh'))
      onApprovalResolved?.()
      await refreshActivityCenter()
    } catch (caught) {
      setActionError(
        caught instanceof Error ? caught.message : 'Não foi possível concluir a ação agora.',
      )
    } finally {
      inFlightRef.current.delete(actionKey)
      setInFlightKeys((prev) => {
        const next = { ...prev }
        delete next[actionKey]
        return next
      })
    }
  }

  const classes = ['orin-activity-center', className].filter(Boolean).join(' ')

  return (
    <div ref={rootRef} className={classes} data-testid="orin-activity-center">
      <button
        type="button"
        className="orin-activity-center__trigger"
        data-testid="orin-activity-center-trigger"
        aria-label={triggerLabel}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((prev) => !prev)}
      >
        <OrinMascot
          size={30}
          variant="flat"
          statusDot={hasAttention ? 'online' : 'none'}
          celebrating={celebrating}
          ariaHidden
        />
        {totalCount > 0 && (
          <span
            className="orin-activity-center__badge"
            data-testid="orin-activity-center-badge"
          >
            {totalCount}
          </span>
        )}
      </button>

      {open && (
        <section
          className="orin-activity-center__popover"
          data-testid="orin-activity-center-popover"
          role="dialog"
          aria-label="Central de atividades e aprovações do Orin"
        >
          <header className="orin-activity-center__header">
            <div className="orin-activity-center__header-titles">
              <strong className="orin-activity-center__title">Central do Orin</strong>
              <span className="orin-activity-center__subtitle">
                {totalCount > 0
                  ? `${totalCount} ${totalCount === 1 ? 'item requer atenção' : 'itens requerem atenção'}`
                  : 'Tudo em dia'}
              </span>
            </div>
            {unreadUpdates.length > 0 && (
              <button
                type="button"
                className="orin-activity-center__mark-read-btn"
                onClick={handleMarkAllRead}
              >
                Marcar tudo como lido
              </button>
            )}
          </header>

          {actionError && (
            <p className="orin-activity-center__error" role="alert">
              {actionError}
            </p>
          )}

          {totalCount === 0 ? (
            <div className="orin-activity-center__empty" data-testid="orin-activity-center-empty">
              <p className="orin-activity-center__empty-text">
                Nenhuma aprovação pendente ou novidade no momento.
              </p>
            </div>
          ) : (
            <div className="orin-activity-center__body">
              {approvals.length > 0 && (
                <div
                  className="orin-activity-center__section"
                  data-testid="orin-activity-center-approvals"
                >
                  <h3 className="orin-activity-center__section-title">
                    Aprovações pendentes ({approvals.length})
                  </h3>
                  <ul className="orin-activity-center__list">
                    {approvals.map((item) => {
                      const busy = Boolean(inFlightKeys[item.key])
                      return (
                        <li key={item.key} className="orin-activity-center__card">
                          <div className="orin-activity-center__card-head">
                            <button
                              type="button"
                              className="orin-activity-center__session-link"
                              onClick={() =>
                                navigateToConversation(item.conversationId, item.projectId)
                              }
                            >
                              {item.conversationTitle}
                            </button>
                            <span className="orin-activity-center__kind-pill">
                              {item.kind === 'toolPermission'
                                ? item.toolName
                                : item.kind === 'planApproval'
                                  ? 'Plano'
                                  : item.kind === 'workspaceTrust'
                                    ? 'Workspace'
                                    : item.kind === 'pluginApproval'
                                      ? 'Plugin'
                                      : item.kind === 'mcpApproval'
                                        ? 'MCP'
                                        : 'Ação'}
                            </span>
                          </div>

                          <p className="orin-activity-center__card-summary">{item.summary}</p>

                          {item.kind === 'toolPermission' && (
                            <>
                              {item.command && (
                                <code className="orin-activity-center__code">{item.command}</code>
                              )}
                              {item.reason && (
                                <p className="orin-activity-center__card-reason">{item.reason}</p>
                              )}
                              <div className="orin-activity-center__actions">
                                <button
                                  type="button"
                                  className="orin-activity-center__btn orin-activity-center__btn--primary"
                                  data-testid={`activity-center-allow-once-${item.requestId}`}
                                  disabled={busy}
                                  onClick={() =>
                                    void runGuardedAction(item.key, async () => {
                                      await submitToolPermission(
                                        apiClient,
                                        item.conversationId,
                                        item.turnId,
                                        item.requestId,
                                        'allow_once',
                                      )
                                    })
                                  }
                                >
                                  Permitir
                                </button>
                                <button
                                  type="button"
                                  className="orin-activity-center__btn orin-activity-center__btn--secondary"
                                  data-testid={`activity-center-allow-always-${item.requestId}`}
                                  disabled={busy}
                                  onClick={() =>
                                    void runGuardedAction(item.key, async () => {
                                      await submitToolPermission(
                                        apiClient,
                                        item.conversationId,
                                        item.turnId,
                                        item.requestId,
                                        'allow_always',
                                      )
                                    })
                                  }
                                >
                                  Sempre permitir
                                </button>
                                <button
                                  type="button"
                                  className="orin-activity-center__btn orin-activity-center__btn--danger"
                                  data-testid={`activity-center-deny-${item.requestId}`}
                                  disabled={busy}
                                  onClick={() =>
                                    void runGuardedAction(item.key, async () => {
                                      await submitToolPermission(
                                        apiClient,
                                        item.conversationId,
                                        item.turnId,
                                        item.requestId,
                                        'deny',
                                      )
                                    })
                                  }
                                >
                                  Negar
                                </button>
                              </div>
                            </>
                          )}

                          {item.kind === 'planApproval' && (
                            <div className="orin-activity-center__actions">
                              <button
                                type="button"
                                className="orin-activity-center__btn orin-activity-center__btn--primary"
                                disabled={busy}
                                onClick={() =>
                                  void runGuardedAction(item.key, async () => {
                                    await updateConversationPermissionMode(
                                      apiClient,
                                      item.conversationId,
                                      'accept_edits',
                                    ).catch(() => undefined)
                                    await sendConversationMessage(apiClient, item.conversationId, {
                                      message: 'Plano aprovado. Pode executar.',
                                      mode: 'code',
                                      permission_mode: 'accept_edits',
                                    })
                                  })
                                }
                              >
                                Aprovar plano
                              </button>
                              <button
                                type="button"
                                className="orin-activity-center__btn orin-activity-center__btn--secondary"
                                onClick={() =>
                                  navigateToConversation(item.conversationId, item.projectId)
                                }
                              >
                                Abrir conversa
                              </button>
                            </div>
                          )}

                          {item.kind === 'workspaceTrust' && (
                            <div className="orin-activity-center__actions">
                              <button
                                type="button"
                                className="orin-activity-center__btn orin-activity-center__btn--primary"
                                disabled={busy}
                                onClick={() =>
                                  void runGuardedAction(item.key, async () => {
                                    await trustConversationWorkspace(apiClient, item.conversationId)
                                  })
                                }
                              >
                                Confiar na pasta
                              </button>
                              <button
                                type="button"
                                className="orin-activity-center__btn orin-activity-center__btn--secondary"
                                onClick={() =>
                                  navigateToConversation(item.conversationId, item.projectId)
                                }
                              >
                                Abrir conversa
                              </button>
                            </div>
                          )}

                          {item.kind === 'pluginApproval' && (
                            <div className="orin-activity-center__actions">
                              <button
                                type="button"
                                className="orin-activity-center__btn orin-activity-center__btn--primary"
                                disabled={busy}
                                onClick={() =>
                                  void runGuardedAction(item.key, async () => {
                                    await approvePlugin(apiClient, item.pluginId)
                                  })
                                }
                              >
                                Aprovar plugin
                              </button>
                              <button
                                type="button"
                                className="orin-activity-center__btn orin-activity-center__btn--secondary"
                                onClick={() =>
                                  navigateToConversation(item.conversationId, item.projectId)
                                }
                              >
                                Abrir conversa
                              </button>
                            </div>
                          )}

                          {item.kind === 'mcpApproval' && (
                            <div className="orin-activity-center__actions">
                              {!item.requiresSecrets && (
                                <button
                                  type="button"
                                  className="orin-activity-center__btn orin-activity-center__btn--primary"
                                  disabled={busy}
                                  onClick={() =>
                                    void runGuardedAction(item.key, async () => {
                                      await approveMcpServer(apiClient, item.serverId, {})
                                    })
                                  }
                                >
                                  Aprovar MCP
                                </button>
                              )}
                              <button
                                type="button"
                                className="orin-activity-center__btn orin-activity-center__btn--secondary"
                                onClick={() =>
                                  navigateToConversation(item.conversationId, item.projectId)
                                }
                              >
                                {item.requiresSecrets ? 'Configurar segredos' : 'Abrir conversa'}
                              </button>
                            </div>
                          )}

                          {item.kind === 'interaction' && (
                            <div className="orin-activity-center__actions">
                              <button
                                type="button"
                                className="orin-activity-center__btn orin-activity-center__btn--primary"
                                onClick={() =>
                                  navigateToConversation(item.conversationId, item.projectId)
                                }
                              >
                                Responder na conversa
                              </button>
                            </div>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                </div>
              )}

              {unreadUpdates.length > 0 && (
                <div
                  className="orin-activity-center__section"
                  data-testid="orin-activity-center-updates"
                >
                  <h3 className="orin-activity-center__section-title">
                    Novidades ({unreadUpdates.length})
                  </h3>
                  <ul className="orin-activity-center__list">
                    {unreadUpdates.map((item) => (
                      <li key={item.conversationId} className="orin-activity-center__card">
                        <button
                          type="button"
                          className="orin-activity-center__update-row"
                          data-testid={`activity-center-update-${item.conversationId}`}
                          onClick={() =>
                            navigateToConversation(item.conversationId, item.projectId)
                          }
                        >
                          <span className="orin-activity-center__update-title">{item.title}</span>
                          <span className="orin-activity-center__update-meta">
                            {item.state === 'completed'
                              ? 'Concluída em segundo plano'
                              : item.state === 'failed'
                                ? 'Falhou em segundo plano'
                                : 'Aguardando atenção'}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  )
}
