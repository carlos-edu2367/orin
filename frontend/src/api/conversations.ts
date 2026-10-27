import { createBrowserApiClient, type ApiClient, type MutationIntent } from './client'
import { ApiError, invalidResponseError, parseApiErrorResponse } from './errors'
import type { ProviderName } from './providers'
import { parseWorkspaceState, type WorkspaceState } from './workspace'
import {
  kindFor,
  stateFor,
  type ActivityBashOutput,
  type ActivityDiffHunk,
  type ActivityPlanApproval,
  type CodeSessionProposalRequest,
  type ContextUsage,
  type ConversationActivityEvent,
  type StructuredPatchHunk,
  type TodoItem,
  type ToolPermissionDecision,
  type ToolPermissionRequest,
} from '../features/conversations/activityTypes'
import type { MessageCommand } from '../features/conversations/MessageCommandChip'
import {
  normalizeCodePermissionMode,
  nextCodePermissionMode,
  type CodePermissionMode,
} from './codeMode'

export type ConversationMode = 'friend' | 'chat' | 'code'
export type PermissionMode = CodePermissionMode
export { normalizeCodePermissionMode, nextCodePermissionMode, type CodePermissionMode }

export type CreateConversationInput = {
  message: string
  provider: ProviderName
  model_id: string
  workspace_id?: string | null
  project_id?: string | null
  workspace_path?: string | null
  workspace_acknowledged_risk?: boolean
  attachments?: string[]
  code_mode?: CodeModeChoice
  mode?: ConversationMode
  permission_mode?: PermissionMode
}

export type CodeModeChoice = 'auto' | 'code' | 'chat' | 'friend'

export type ConversationReceipt = {
  conversation_id: string
  title: string
  turn_id: string
  message_id: string
  state: string
  mode: ConversationMode
  permission_mode: PermissionMode
}

export type MessageAttachment = { path: string; original_name: string; media_type: string; kind: string; bytes: number }

export type Conversation = {
  conversation_id: string
  title: string
  state: string
  mode: ConversationMode
  permission_mode: PermissionMode
  project_id?: string | null
  project_name?: string | null
  messages: ConversationMessage[]
  turns: ConversationTurn[]
  provider: string
  model_id: string
  activities: ConversationActivityEvent[]
  activity_cursor: string
  has_older_activities?: boolean
  oldest_activity_id?: number | null
  workspace: WorkspaceState
  context_usage: ContextUsage | null
}
export type ConversationDetail = Conversation
export type ConversationSummary = Pick<Conversation, 'conversation_id' | 'title' | 'state'> & {
  mode?: ConversationMode
  permission_mode?: PermissionMode
  updated_at?: string | null
}
export type ConversationMessage = { message_id: string; role: 'user' | 'assistant'; content: string; status: string; retryable: boolean; attachments: MessageAttachment[]; command: MessageCommand | null; turn_id?: string | null }
export type ConversationTurn = { turn_id: string; state: string; created_at: string; started_at: string | null; finished_at: string | null; scheduled_by_schedule_id: string | null; code_mode: 'code' | null }

export type SendMessageInput = {
  message: string
  attachments?: string[]
  selection?: Pick<CreateConversationInput, 'provider' | 'model_id'>
  code_mode?: CodeModeChoice
  mode?: ConversationMode
  permission_mode?: PermissionMode
}

function parseModeAndPermission(data: Record<string, unknown>, fallbackMode: ConversationMode = 'chat'): {
  mode: ConversationMode
  permission_mode: PermissionMode
} {
  const mode: ConversationMode = data.mode === 'friend' ? 'friend' : data.mode === 'code' ? 'code' : data.mode === 'chat' ? 'chat' : fallbackMode
  const defaultPermission: PermissionMode = mode === 'code' ? 'accept_edits' : 'ask_permissions'
  const permission_mode: PermissionMode = typeof data.permission_mode === 'string'
    ? normalizeCodePermissionMode(data.permission_mode)
    : defaultPermission
  return { mode, permission_mode }
}

export function createConversation(client: ApiClient, input: CreateConversationInput, intent = client.createMutationIntent()): Promise<ConversationReceipt> {
  const effectiveCodeMode: CodeModeChoice | undefined = input.mode
    ? input.mode
    : input.code_mode && input.code_mode !== 'auto'
      ? input.code_mode
      : undefined
  return client.request({
    path: '/v1/conversations', method: 'POST', expectedStatus: 201, intent,
    body: {
      message: input.message,
      selection: { provider: input.provider, model_id: input.model_id },
      workspace_id: input.workspace_id ?? null,
      project_id: input.project_id ?? null,
      workspace_path: input.workspace_path ?? null,
      workspace_acknowledged_risk: input.workspace_acknowledged_risk ?? false,
      attachments: input.attachments ?? [],
      ...(effectiveCodeMode ? { code_mode: effectiveCodeMode } : {}),
      ...(input.permission_mode ? { permission_mode: input.permission_mode } : {}),
    },
    parse: parseConversationReceipt,
  })
}

export function parseConversationReceipt(value: unknown): ConversationReceipt {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  const { mode, permission_mode } = parseModeAndPermission(data)
  return {
    conversation_id: string(data.conversation_id),
    title: string(data.title),
    turn_id: string(data.turn_id),
    message_id: string(data.message_id),
    state: string(data.state),
    mode,
    permission_mode,
  }
}

export const parseReceipt = parseConversationReceipt

export function getConversation(client: ApiClient, id: string): Promise<Conversation> {
  return client.request({ path: `/v1/conversations/${encodeURIComponent(id)}`, parse: parseConversation })
}

export type OlderActivitiesPage = {
  activities: ConversationActivityEvent[]
  has_older_activities: boolean
  oldest_activity_id: number | null
}

export function listOlderConversationActivities(
  client: ApiClient,
  conversationId: string,
  beforeId: number,
  limit = 150,
  signal?: AbortSignal,
): Promise<OlderActivitiesPage> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/activities`,
    query: { before_id: beforeId, limit },
    signal,
    parse: (value) => {
      const data = record(value)
      return {
        activities: parseSnapshotActivities(data.activities),
        has_older_activities: data.has_older_activities === true,
        oldest_activity_id: typeof data.oldest_activity_id === 'number' && Number.isFinite(data.oldest_activity_id) ? data.oldest_activity_id : null,
      }
    },
  })
}

export function parseConversationSummary(value: unknown): Required<Pick<ConversationSummary, 'conversation_id' | 'title' | 'state' | 'mode' | 'permission_mode'>> & { updated_at: string | null } {
  const data = record(value)
  const { mode, permission_mode } = parseModeAndPermission(data)
  return {
    conversation_id: string(data.conversation_id),
    title: string(data.title),
    state: string(data.state),
    mode,
    permission_mode,
    updated_at: typeof data.updated_at === 'string' ? data.updated_at : null,
  }
}

export function listConversations(client: ApiClient): Promise<{ items: Array<Required<Pick<ConversationSummary, 'conversation_id' | 'title' | 'state' | 'mode' | 'permission_mode'>> & { updated_at: string | null }> }> {
  return client.request({ path: '/v1/conversations', parse: (value) => {
    if (typeof value !== 'object' || value === null || !Array.isArray((value as { items?: unknown }).items)) throw invalidResponseError()
    return { items: (value as { items: unknown[] }).items.map(parseConversationSummary) }
  } })
}

export function sendConversationMessage(
  client: ApiClient,
  id: string,
  messageOrInput: string | SendMessageInput,
  attachments: string[] = [],
  selection?: Pick<CreateConversationInput, 'provider' | 'model_id'>,
  codeMode: CodeModeChoice = 'auto',
  permissionModeOrIntent?: PermissionMode | MutationIntent,
  intentArg?: MutationIntent,
): Promise<ConversationReceipt> {
  if (typeof messageOrInput === 'object' && messageOrInput !== null) {
    const effectiveMode = messageOrInput.mode ?? (messageOrInput.code_mode && messageOrInput.code_mode !== 'auto' ? messageOrInput.code_mode : undefined)
    const intent = (typeof permissionModeOrIntent === 'object' && permissionModeOrIntent !== null ? permissionModeOrIntent : intentArg) ?? client.createMutationIntent()
    return client.request({
      path: `/v1/conversations/${encodeURIComponent(id)}/messages`,
      method: 'POST',
      expectedStatus: 201,
      intent,
      body: {
        message: messageOrInput.message,
        attachments: messageOrInput.attachments ?? [],
        ...(effectiveMode ? { code_mode: effectiveMode } : {}),
        ...(messageOrInput.permission_mode ? { permission_mode: messageOrInput.permission_mode } : {}),
        ...(messageOrInput.selection ? { selection: messageOrInput.selection } : {}),
      },
      parse: parseConversationReceipt,
    })
  }
  const permissionMode = typeof permissionModeOrIntent === 'string' ? permissionModeOrIntent : undefined
  const intent = (typeof permissionModeOrIntent === 'object' && permissionModeOrIntent !== null ? permissionModeOrIntent : intentArg) ?? client.createMutationIntent()
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(id)}/messages`, method: 'POST', expectedStatus: 201, intent,
    body: {
      message: messageOrInput,
      attachments,
      ...(codeMode !== 'auto' ? { code_mode: codeMode } : {}),
      ...(permissionMode ? { permission_mode: permissionMode } : {}),
      ...(selection ? { selection } : {}),
    },
    parse: parseConversationReceipt,
  })
}

export const sendMessage = sendConversationMessage

export type UpdatePermissionModeResponse = {
  conversation_id: string
  mode: ConversationMode
  permission_mode: PermissionMode
}

export function updateConversationPermissionMode(
  clientOrConversationId: ApiClient | string,
  conversationIdOrPermissionMode: string,
  maybePermissionMode?: PermissionMode,
  maybeIntent?: MutationIntent,
): Promise<UpdatePermissionModeResponse> {
  const client = typeof clientOrConversationId === 'string'
    ? createBrowserApiClient()
    : clientOrConversationId
  const conversationId = typeof clientOrConversationId === 'string'
    ? clientOrConversationId
    : conversationIdOrPermissionMode
  const permissionMode = (typeof clientOrConversationId === 'string'
    ? conversationIdOrPermissionMode
    : maybePermissionMode) as PermissionMode
  const intent = maybeIntent ?? client.createMutationIntent()
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/permission-mode`,
    method: 'PATCH',
    intent,
    body: { permission_mode: permissionMode },
    parse: (value) => {
      const data = record(value)
      const { mode, permission_mode } = parseModeAndPermission(data)
      return {
        conversation_id: typeof data.conversation_id === 'string' ? data.conversation_id : conversationId,
        mode,
        permission_mode: typeof data.permission_mode === 'string' ? permission_mode : normalizeCodePermissionMode(permissionMode),
      }
    },
  })
}

export function cancelConversation(client: ApiClient, id: string, intent = client.createMutationIntent()): Promise<{ cancelling: string[] }> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(id)}/cancel`, method: 'POST', expectedStatus: 202, intent,
    body: {},
    parse: (value) => {
      const data = record(value)
      return { cancelling: Array.isArray(data.cancelling) ? data.cancelling.map((item) => String(item)) : [] }
    },
  })
}

export type ToolPermissionDecisionResponse = {
  request_id: string
  decision: ToolPermissionDecision
  allowed: boolean
  persisted_rule?: string | null
}

export function submitToolPermission(
  client: ApiClient,
  conversationId: string,
  turnId: string,
  requestId: string,
  decision: ToolPermissionDecision,
  reason?: string,
  intent = client.createMutationIntent(),
): Promise<ToolPermissionDecisionResponse> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/tool-permission`,
    method: 'POST',
    intent,
    body: { request_id: requestId, decision, ...(reason ? { reason } : {}) },
    parse: (value) => {
      const data = record(value)
      const parsedDecision = data.decision === 'allow_once' || data.decision === 'allow_always' || data.decision === 'deny'
        ? data.decision
        : decision
      return {
        request_id: typeof data.request_id === 'string' ? data.request_id : requestId,
        decision: parsedDecision,
        allowed: data.allowed === true,
        persisted_rule: typeof data.persisted_rule === 'string' ? data.persisted_rule : null,
      }
    },
  })
}

export type RewindTurnResponse = {
  turn_id: string
  rewound: boolean
  restored_files: string[]
  deleted_files: string[]
  undo_backup_id: string | null
}

export function rewindTurn(
  client: ApiClient,
  conversationId: string,
  turnId: string,
  intent = client.createMutationIntent(),
): Promise<RewindTurnResponse> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/rewind`,
    method: 'POST',
    intent,
    body: {},
    parse: (value) => {
      const data = record(value)
      return {
        turn_id: typeof data.turn_id === 'string' ? data.turn_id : turnId,
        rewound: data.rewound === true,
        restored_files: Array.isArray(data.restored_files) ? data.restored_files.map((item) => String(item)) : [],
        deleted_files: Array.isArray(data.deleted_files) ? data.deleted_files.map((item) => String(item)) : [],
        undo_backup_id: typeof data.undo_backup_id === 'string' ? data.undo_backup_id : null,
      }
    },
  })
}

export {
  getConversationWorktree,
  listWorkspaceFiles,
  manageConversationWorktree,
  type ConversationWorktreeResponse,
  type ManageWorktreeInput,
  type WorktreeInfo,
} from './codeMode'


export type ConversationOverview = {
  conversation_id: string
  title: string
  state: string
  provider: string
  model_id: string
  token_usage: TokenUsage
  agents: Array<{ agent_id: string; name: string; role: string; parent_agent_id: string | null; provider: string; model_id: string; state: string; token_usage: TokenUsage }>
  tools: Array<{ tool_name: string; kind: string; count: number; failures: number; agent_id: string }>
  messages: Array<{ event_id: string; from_agent_id: string; to_agent_id: string; preview: string; occurred_at: string }>
  errors: Array<{ event_id: string; code: string; summary: string; occurred_at: string }>
  turns: ConversationTurn[]
  activity_count: number
  duration_seconds: number | null
}

export type TokenUsage = {
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  estimated_cost_usd?: number | null
  usage_reported: boolean
}

export function getConversationOverview(client: ApiClient, id: string): Promise<ConversationOverview> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(id)}/overview`,
    parse: (value) => {
      const data = record(value)
      const list = (key: string): Record<string, unknown>[] => Array.isArray(data[key]) ? (data[key] as unknown[]).map(record) : []
      return {
        conversation_id: string(data.conversation_id),
        title: string(data.title),
        state: string(data.state),
        provider: typeof data.provider === 'string' ? data.provider : '',
        model_id: typeof data.model_id === 'string' ? data.model_id : '',
        agents: list('agents').map((item) => ({
          agent_id: string(item.agent_id), name: string(item.name),
          role: typeof item.role === 'string' ? item.role : '',
          parent_agent_id: typeof item.parent_agent_id === 'string' ? item.parent_agent_id : null,
          provider: typeof item.provider === 'string' ? item.provider : '',
          model_id: typeof item.model_id === 'string' ? item.model_id : '',
          state: typeof item.state === 'string' ? item.state : 'idle',
          token_usage: tokenUsage(item.token_usage),
        })),
        tools: list('tools').map((item) => ({
          tool_name: string(item.tool_name), kind: typeof item.kind === 'string' ? item.kind : 'tool',
          count: Number(item.count) || 0, failures: Number(item.failures) || 0,
          agent_id: typeof item.agent_id === 'string' ? item.agent_id : '',
        })),
        messages: list('messages').map((item) => ({
          event_id: string(item.event_id), from_agent_id: string(item.from_agent_id), to_agent_id: string(item.to_agent_id),
          preview: typeof item.preview === 'string' ? item.preview : '',
          occurred_at: typeof item.occurred_at === 'string' ? item.occurred_at : '',
        })),
        errors: list('errors').map((item) => ({
          event_id: string(item.event_id), code: string(item.code),
          summary: typeof item.summary === 'string' ? item.summary : '',
          occurred_at: typeof item.occurred_at === 'string' ? item.occurred_at : '',
        })),
        turns: list('turns').map((item) => ({
          turn_id: string(item.turn_id), state: string(item.state), created_at: string(item.created_at),
          started_at: typeof item.started_at === 'string' ? item.started_at : null,
          finished_at: typeof item.finished_at === 'string' ? item.finished_at : null,
          scheduled_by_schedule_id: typeof item.scheduled_by_schedule_id === 'string' ? item.scheduled_by_schedule_id : null,
          code_mode: item.code_mode === 'code' ? 'code' : null,
        })),
        activity_count: Number(data.activity_count) || 0,
        duration_seconds: typeof data.duration_seconds === 'number' ? data.duration_seconds : null,
        token_usage: tokenUsage(data.token_usage),
      }
    },
  })
}

function tokenUsage(value: unknown): TokenUsage {
  const data = value !== null && typeof value === 'object' ? value as Record<string, unknown> : {}
  const count = (item: unknown): number | null => typeof item === 'number' && Number.isFinite(item) && item >= 0 ? item : null
  return {
    input_tokens: count(data.input_tokens),
    output_tokens: count(data.output_tokens),
    total_tokens: count(data.total_tokens),
    estimated_cost_usd: count(data.estimated_cost_usd),
    usage_reported: data.usage_reported === true,
  }
}

export type ConversationEventStreamHandlers = {
  onEvent: (event: ConversationActivityEvent) => void
  onCursor: (cursor: string) => void
}

/**
 * Reads the existing SSE endpoint without exposing its payload object to the UI.
 * The stream is intentionally one-shot: callers own reconnect/backoff and can
 * therefore resync from a fresh snapshot after an invalid cursor.
 */
export async function streamConversationEvents(
  client: ApiClient,
  conversationId: string,
  cursor: string,
  handlers: ConversationEventStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await client.stream({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/events?after=${encodeURIComponent(cursor)}`,
    signal,
  })
  if (!response.ok) throw await parseApiErrorResponse(response)
  if (!response.body) throw invalidResponseError()

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let record: SseRecord = { data: [], event: '', id: '' }

  const consumeLine = (line: string) => {
    if (line === '') {
      const current = record
      record = { data: [], event: '', id: '' }
      if (current.data.length === 0) return
      const data = parseJson(current.data.join('\n'))
      if (current.event === 'heartbeat') {
        const heartbeat = parseHeartbeat(data)
        handlers.onCursor(heartbeat)
        return
      }
      if (current.event === 'resync') {
        throw new ApiError({
          status: 409,
          code: 'cursor_invalid',
          category: 'CONFLICT',
          messageKey: 'Stream cursor requires resync',
        })
      }
      const event = parseConversationActivityEvent(data, current.id)
      handlers.onEvent(event)
      handlers.onCursor(event.cursor)
      return
    }
    if (line.startsWith('data:')) record.data.push(line.slice(5).trimStart())
    else if (line.startsWith('event:')) record.event = line.slice(6).trimStart()
    else if (line.startsWith('id:')) record.id = line.slice(3).trimStart()
  }

  while (true) {
    const chunk = await reader.read()
    buffer += decoder.decode(chunk.value ?? new Uint8Array(), { stream: !chunk.done })
    const lines = buffer.split(/\r?\n/)
    buffer = lines.pop() ?? ''
    lines.forEach(consumeLine)
    if (chunk.done) {
      if (buffer) consumeLine(buffer)
      consumeLine('')
      break
    }
  }
}

/** Public parser used by the stream and unit tests.
 *
 * The server owns redaction and bounds, so this parser normalizes rather than
 * re-authorizes: it keeps the fields the UI renders, coerces the rest away, and
 * lets an unrecognized event type through as a plain lifecycle line. Failing the
 * whole stream on one unknown field would turn a harmless backend addition into a
 * blank chat.
 */
export function parseConversationActivityEvent(value: unknown, cursor: string): ConversationActivityEvent {
  const item = record(value)
  const payload = typeof item.payload === 'object' && item.payload !== null && !Array.isArray(item.payload)
    ? (item.payload as Record<string, unknown>)
    : {}
  const type = optionalText(item.event_type, 64) ?? 'turn.started'
  const toolKind = optionalText(payload.tool_kind, 32)
  const status = optionalText(payload.status, 32)
  const errorCode = optionalText(payload.error_code, 64) ?? null
  const kind = kindFor(type, toolKind)
  const resolvedCursor = optionalText(item.cursor, 512) ?? cursor

  const event: ConversationActivityEvent = {
    eventId: publicId(item.event_id ?? item.id ?? resolvedCursor),
    cursor: publicId(resolvedCursor),
    type,
    kind,
    state: stateFor(type, status, errorCode),
    agentId: optionalText(item.agent_id, 255) ?? 'agent:main',
    summary: optionalText(item.summary, 512) ?? '',
  }
  const assign = <K extends keyof ConversationActivityEvent>(key: K, candidate: ConversationActivityEvent[K] | undefined) => {
    if (candidate !== undefined) event[key] = candidate
  }
  assign('agentName', optionalText(payload.agent_name, 120))
  assign('parentAgentId', optionalText(item.parent_agent_id, 255))
  assign('turnId', optionalText(item.turn_id, 255))
  assign('messageId', optionalText(payload.message_id, 255))
  assign('toolName', optionalText(payload.tool_name, 64))
  assign('toolKind', toolKind)
  assign('invocationId', optionalText(payload.invocation_id, 255))
  assign('status', status)
  assign('label', optionalText(payload.label, 200))
  const resolvedPath = optionalText(payload.path ?? payload.file_path, 512)
  assign('path', resolvedPath)
  assign('filePath', resolvedPath)
  if (payload.unchanged === true) event.unchanged = true
  assign('screenshotPath', optionalWorkspacePath(payload.screenshot_path))
  assign('codeStage', optionalText(payload.stage, 64))
  if (payload.code_approval === true) event.codeApproval = true
  assign('memoryId', optionalText(payload.memory_id, 255))
  assign('memoryProjectId', optionalText(payload.project_id, 255))
  if (payload.scope === 'user' || payload.scope === 'project') event.memoryScope = payload.scope
  const trust = payload.workspace_trust
  if (trust && typeof trust === 'object' && !Array.isArray(trust)) {
    const trustRecord = trust as Record<string, unknown>
    const trustPath = optionalText(trustRecord.path, 1024)
    const items = Array.isArray(trustRecord.items)
      ? trustRecord.items.filter((item): item is string => typeof item === 'string').slice(0, 20).map((item) => item.slice(0, 80))
      : []
    if (trustPath) event.workspaceTrust = { path: trustPath, items }
  }
  assign('occurredAt', optionalText(item.occurred_at, 64))
  // The backend cuts a notification to 180 UTF-16 units, the limit the desktop notifier applies (W4-R14).
  if (type === 'notification.pushed') assign('notificationMessage', optionalText(payload.message, 180))
  const contextUsage = parseContextUsage(payload)
  if (contextUsage) event.contextUsage = contextUsage
  const questions = parseUserQuestions(payload.questions)
  if (questions) event.questions = questions
  const mcpApproval = parseMcpApprovalRequest(payload)
  if (mcpApproval) event.mcpApproval = mcpApproval
  const pluginApproval = parsePluginApprovalRequest(payload)
  if (pluginApproval) event.pluginApproval = pluginApproval
  const codeSessionProposal = parseCodeSessionProposalRequest(payload)
  if (codeSessionProposal) event.codeSessionProposal = codeSessionProposal
  if (typeof payload.delegated_code_session === 'object' && payload.delegated_code_session !== null && !Array.isArray(payload.delegated_code_session)) {
    const rawDel = payload.delegated_code_session as Record<string, unknown>
    if (typeof rawDel.code_conversation_id === 'string' && rawDel.code_conversation_id) {
      event.delegatedCodeSession = {
        code_conversation_id: rawDel.code_conversation_id,
        goal: typeof rawDel.goal === 'string' ? rawDel.goal : 'Tarefa no Orin Code',
        status: rawDel.status === 'completed' || rawDel.status === 'failed' ? rawDel.status : 'running',
        completed_todo_count: typeof rawDel.completed_todo_count === 'number' ? rawDel.completed_todo_count : 0,
        total_todo_count: typeof rawDel.total_todo_count === 'number' ? rawDel.total_todo_count : 0,
        active_task_title: typeof rawDel.active_task_title === 'string' ? rawDel.active_task_title : null,
        modified_files_count: typeof rawDel.modified_files_count === 'number' ? rawDel.modified_files_count : 0,
        examined_files_count: typeof rawDel.examined_files_count === 'number' ? rawDel.examined_files_count : 0,
        friendly_milestone: typeof rawDel.friendly_milestone === 'string' ? rawDel.friendly_milestone : null,
      }
    }
  }
  if (type === 'speak.chunk_ready') {
    const spoken = optionalText(payload.tts_text, 2000) ?? optionalText(payload.clean_text, 2000)
    if (spoken) event.speakText = spoken
  }
  const planApproval = parsePlanApproval(payload)
  if (planApproval) {
    event.planApproval = planApproval
    if (planApproval.plan_path) event.planPath = planApproval.plan_path
    if (planApproval.plan_content) event.planContent = planApproval.plan_content
  }
  const extraPayload: Record<string, unknown> = {}
  const modelId = optionalText(payload.model_id, 128)
  const subagentType = optionalText(payload.subagent_type, 128)
  const phase = optionalText(payload.phase, 64)
  const callId = optionalText(payload.call_id, 255)
  const payloadToolName = optionalText(payload.tool_name, 64)
  if (modelId) extraPayload.model_id = modelId
  if (subagentType) extraPayload.subagent_type = subagentType
  if (phase) extraPayload.phase = phase
  if (callId) extraPayload.call_id = callId
  if (payloadToolName) extraPayload.tool_name = payloadToolName
  if (typeof payload.delta === 'string' && payload.delta.length <= 16_000 && !isRedactedValue(payload.delta)) {
    extraPayload.delta = payload.delta
  }
  if (typeof payload.bytes_received === 'number' && Number.isFinite(payload.bytes_received) && payload.bytes_received >= 0) {
    extraPayload.bytes_received = payload.bytes_received
  }
  if (typeof payload.iteration === 'number' && Number.isFinite(payload.iteration) && payload.iteration >= 0) {
    extraPayload.iteration = payload.iteration
  }
  if (type === 'plugin.hook_executed') {
    // W7 detailed transcript: what a hook was and how it ended (the server has already redacted these).
    const hookEvent = optionalText(payload.hook_event, 64)
    const source = optionalText(payload.source, 32)
    const hookType = optionalText(payload.type, 16)
    const reason = optionalText(payload.reason, 300)
    if (hookEvent) extraPayload.hook_event = hookEvent
    if (source) extraPayload.source = source
    if (hookType) extraPayload.hook_type = hookType
    if (reason) extraPayload.reason = reason
    if (typeof payload.exit_code === 'number' && Number.isInteger(payload.exit_code)) extraPayload.exit_code = payload.exit_code
    if (typeof payload.blocked === 'boolean') extraPayload.blocked = payload.blocked
    if (typeof payload.duration_ms === 'number' && Number.isFinite(payload.duration_ms) && payload.duration_ms >= 0) {
      extraPayload.duration_ms = Math.round(payload.duration_ms)
    }
  }
  if (Object.keys(extraPayload).length > 0) {
    event.payload = {
      ...(event.payload ?? {}),
      ...extraPayload,
    }
  }
  const toolPermission = parseToolPermissionRequest(payload, event.turnId, planApproval)
  if (toolPermission) {
    event.toolPermission = toolPermission
    event.kind = 'tool'
    if (toolPermission.state === 'allowed') {
      event.state = 'completed'
    } else if (toolPermission.state === 'denied') {
      event.state = 'failed'
    } else {
      event.state = 'waiting_user'
    }
  }
  const structuredPatch = parseStructuredPatch(payload.structured_patch)
  if (structuredPatch) event.structuredPatch = structuredPatch
  if (typeof payload.added_lines === 'number' && Number.isFinite(payload.added_lines) && payload.added_lines >= 0) {
    event.addedLines = payload.added_lines
  }
  if (typeof payload.removed_lines === 'number' && Number.isFinite(payload.removed_lines) && payload.removed_lines >= 0) {
    event.removedLines = payload.removed_lines
  }
  if (structuredPatch || event.addedLines !== undefined || event.removedLines !== undefined || payload.unchanged === true) {
    event.diff = {
      path: resolvedPath ?? event.label ?? '',
      added_lines: event.addedLines ?? 0,
      removed_lines: event.removedLines ?? 0,
      structured_patch: (structuredPatch ?? []) as ActivityDiffHunk[],
      ...(event.summary ? { summary: event.summary } : {}),
    }
  }
  assign('command', optionalText(payload.command, 4000))
  const rawOutputTail =
    typeof payload.output_tail === 'string'
      ? payload.output_tail
      : typeof payload.outputTail === 'string'
        ? payload.outputTail
        : undefined
  if (rawOutputTail && rawOutputTail.length > 0 && rawOutputTail.length <= 4_000 && !isRedactedValue(rawOutputTail)) {
    event.outputTail = rawOutputTail
  }
  if (typeof payload.elapsed_ms === 'number' && Number.isFinite(payload.elapsed_ms) && payload.elapsed_ms >= 0) {
    event.elapsedMs = payload.elapsed_ms
  }
  const bashOutput = parseBashOutput(payload, toolKind, event.toolName, event.state, status, errorCode)
  if (bashOutput) {
    event.bashOutput = bashOutput
    if (bashOutput.command && !event.command) event.command = bashOutput.command
    if (bashOutput.description) event.description = bashOutput.description
    if (bashOutput.auto_backgrounded !== undefined) event.autoBackgrounded = bashOutput.auto_backgrounded
    if (bashOutput.detected_url) event.detectedUrl = bashOutput.detected_url
    if (bashOutput.shell_id) event.shellId = bashOutput.shell_id
    if (bashOutput.exit_code !== undefined) event.exitCode = bashOutput.exit_code
  } else {
    assign('description', optionalText(payload.description, 500))
  }
  const todosSource =
    payload.todos ??
    (typeof payload.result === 'object' && payload.result !== null ? (payload.result as Record<string, unknown>).todos : undefined) ??
    (typeof payload.output === 'object' && payload.output !== null ? (payload.output as Record<string, unknown>).todos : undefined)
  const todos = parseTodoItems(todosSource)
  if (todos) event.todos = todos
  if (errorCode) event.errorCode = errorCode
  if (typeof payload.content === 'string' && payload.content.length <= 16_000 && !isRedactedValue(payload.content)) {
    event.content = payload.content
  } else if (type === 'assistant.delta' && typeof payload.delta === 'string' && payload.delta.length <= 16_000 && !isRedactedValue(payload.delta)) {
    event.content = payload.delta
  } else if (event.state === 'failed') {
    const errorText = optionalText(payload.error ?? payload.detail, 16_000)
    if (errorText && !isRedactedValue(errorText)) {
      event.content = errorText
    }
  }
  return event
}

function isRedactedValue(value: unknown): boolean {
  if (typeof value !== 'string') return false
  const trimmed = value.trim()
  if (!trimmed) return false
  return trimmed === '[REDACTED]' || trimmed.replace(/\[REDACTED\]/g, '').trim() === ''
}

function parseBashOutput(
  payload: Record<string, unknown>,
  toolKind?: string,
  toolName?: string,
  state?: string,
  status?: string,
  errorCode?: string | null,
): ActivityBashOutput | undefined {
  const bashSource = typeof payload.bash_output === 'object' && payload.bash_output !== null && !Array.isArray(payload.bash_output)
    ? (payload.bash_output as Record<string, unknown>)
    : payload
  const isTerminalTool = toolKind === 'terminal' || toolName === 'run_command' || toolName === 'bash' || toolName === 'bash_output'
  const command = optionalText(bashSource.command ?? payload.command, 4000)
  const description = optionalText(bashSource.description ?? payload.description, 500)
  const fallbackTail = typeof bashSource.output_tail === 'string' && !isRedactedValue(bashSource.output_tail)
    ? bashSource.output_tail
    : typeof payload.output_tail === 'string' && !isRedactedValue(payload.output_tail)
      ? payload.output_tail
      : typeof payload.outputTail === 'string' && !isRedactedValue(payload.outputTail)
        ? payload.outputTail
        : undefined
  const rawStdout = typeof bashSource.stdout === 'string' && !isRedactedValue(bashSource.stdout)
    ? bashSource.stdout.slice(0, 16_000)
    : typeof payload.stdout === 'string' && !isRedactedValue(payload.stdout)
      ? payload.stdout.slice(0, 16_000)
      : undefined
  const stdout = rawStdout !== undefined && rawStdout.length > 0
    ? rawStdout
    : isTerminalTool && fallbackTail !== undefined
      ? fallbackTail.slice(0, 16_000)
      : rawStdout
  const stderr = typeof bashSource.stderr === 'string' && !isRedactedValue(bashSource.stderr)
    ? bashSource.stderr.slice(0, 16_000)
    : typeof payload.stderr === 'string' && !isRedactedValue(payload.stderr)
      ? payload.stderr.slice(0, 16_000)
      : undefined
  const isFailedEvent =
    state === 'failed' ||
    status === 'failed' ||
    Boolean(errorCode)
  const rawExitCode = typeof bashSource.exit_code === 'number' && Number.isFinite(bashSource.exit_code)
    ? bashSource.exit_code
    : bashSource.exit_code === null
      ? null
      : typeof payload.exit_code === 'number' && Number.isFinite(payload.exit_code)
        ? payload.exit_code
        : undefined
  const exitCode = isFailedEvent && rawExitCode === 0 ? undefined : rawExitCode
  const background = typeof bashSource.background === 'boolean'
    ? bashSource.background
    : typeof payload.background === 'boolean'
      ? payload.background
      : undefined
  const autoBackgrounded = typeof bashSource.auto_backgrounded === 'boolean'
    ? bashSource.auto_backgrounded
    : typeof payload.auto_backgrounded === 'boolean'
      ? payload.auto_backgrounded
      : undefined
  const shellId = optionalText(bashSource.shell_id ?? payload.shell_id, 128)
  const detectedUrl = optionalText(bashSource.detected_url ?? payload.detected_url, 1024)
  if (
    command === undefined &&
    stdout === undefined &&
    stderr === undefined &&
    exitCode === undefined &&
    background === undefined &&
    autoBackgrounded === undefined &&
    shellId === undefined &&
    detectedUrl === undefined &&
    !(isTerminalTool && description !== undefined)
  ) {
    return undefined
  }
  return {
    ...(command !== undefined ? { command } : {}),
    ...(description !== undefined ? { description } : {}),
    ...(stdout !== undefined ? { stdout } : {}),
    ...(stderr !== undefined ? { stderr } : {}),
    ...(exitCode !== undefined ? { exit_code: exitCode } : {}),
    ...(background !== undefined ? { background } : {}),
    ...(autoBackgrounded !== undefined ? { auto_backgrounded: autoBackgrounded } : {}),
    ...(shellId !== undefined ? { shell_id: shellId } : {}),
    ...(detectedUrl !== undefined ? { detected_url: detectedUrl } : {}),
  }
}

function parsePlanApproval(payload: Record<string, unknown>): ActivityPlanApproval | undefined {
  const nested = typeof payload.plan_approval === 'object' && payload.plan_approval !== null && !Array.isArray(payload.plan_approval)
    ? (payload.plan_approval as Record<string, unknown>)
    : typeof payload.permission_request === 'object' && payload.permission_request !== null && !Array.isArray(payload.permission_request)
      ? (payload.permission_request as Record<string, unknown>)
      : null
  const planPath = optionalText(nested?.plan_path ?? payload.plan_path, 512)
  const rawContent = nested?.plan_content ?? payload.plan_content
  const planContent = typeof rawContent === 'string' && rawContent.length <= 32_000 ? rawContent : undefined
  const rawPrompts = Array.isArray(nested?.allowed_prompts)
    ? nested.allowed_prompts
    : Array.isArray(payload.allowed_prompts)
      ? payload.allowed_prompts
      : undefined
  const allowedPrompts = rawPrompts
    ? rawPrompts
        .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null && !Array.isArray(item))
        .map((item) => ({
          ...(typeof item.tool === 'string' ? { tool: item.tool } : {}),
          ...(typeof item.prompt === 'string' ? { prompt: item.prompt } : {}),
        }))
    : undefined
  const isPlanEvent =
    payload.plan_approval === true ||
    nested?.plan_approval === true ||
    payload.tool_name === 'exit_plan_mode' ||
    nested?.tool_name === 'exit_plan_mode' ||
    planPath !== undefined ||
    planContent !== undefined
  if (!isPlanEvent) return undefined
  return {
    ...(planPath !== undefined ? { plan_path: planPath } : {}),
    ...(planContent !== undefined ? { plan_content: planContent } : {}),
    ...(allowedPrompts !== undefined ? { allowed_prompts: allowedPrompts } : {}),
  }
}

function parseTodoItems(value: unknown): TodoItem[] | undefined {
  if (!Array.isArray(value) || value.length === 0 || value.length > 50) return undefined
  const items: TodoItem[] = []
  for (const entry of value) {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) continue
    const raw = entry as Record<string, unknown>
    const id = optionalText(raw.id, 64)
    const content = optionalText(raw.content, 500)
    const status = raw.status === 'pending' || raw.status === 'in_progress' || raw.status === 'completed'
      ? raw.status
      : 'pending'
    if (!id || !content) continue
    const activeForm = optionalText(raw.active_form ?? raw.activeForm, 500) ?? content
    items.push({ id, content, status, activeForm, active_form: activeForm })
  }
  return items.length > 0 ? items : undefined
}

type ExtendedToolPermissionRequest = ToolPermissionRequest & {
  target?: string
  reason?: string
  state?: 'pending' | 'allowed' | 'denied'
}

function parseToolPermissionRequest(
  payload: Record<string, unknown>,
  fallbackTurnId?: string,
  planApproval?: ActivityPlanApproval,
): ExtendedToolPermissionRequest | undefined {
  const source = typeof payload.permission_request === 'object' && payload.permission_request !== null && !Array.isArray(payload.permission_request)
    ? (payload.permission_request as Record<string, unknown>)
    : payload.tool_permission === true
      ? payload
      : null
  if (!source) return undefined
  const requestId = optionalText(source.request_id, 128)
  const toolName = optionalText(source.tool_name, 64) ?? optionalText(payload.tool_name, 64)
  if (!requestId || !toolName) return undefined
  const structuredPatch = parseStructuredPatch(source.structured_patch ?? payload.structured_patch)
  const addedLines = typeof source.added_lines === 'number' && Number.isFinite(source.added_lines)
    ? source.added_lines
    : typeof payload.added_lines === 'number' && Number.isFinite(payload.added_lines)
      ? payload.added_lines
      : undefined
  const removedLines = typeof source.removed_lines === 'number' && Number.isFinite(source.removed_lines)
    ? source.removed_lines
    : typeof payload.removed_lines === 'number' && Number.isFinite(payload.removed_lines)
      ? payload.removed_lines
      : undefined
  const command = optionalText(source.command, 2000) ?? optionalText(payload.command, 2000)
  const path = optionalText(source.path ?? source.file_path ?? source.plan_path, 512) ?? optionalText(payload.path ?? payload.file_path ?? payload.plan_path, 512)
  const ruleSuggestion =
    optionalText(source.rule_suggestion ?? source.suggested_rule, 255) ??
    optionalText(payload.rule_suggestion ?? payload.suggested_rule, 255)
  const target =
    optionalText(source.target, 2000) ??
    optionalText(payload.target, 2000) ??
    path ??
    command
  const reason =
    optionalText(source.reason, 500) ??
    optionalText(payload.reason, 500)
  const rawState = optionalText(source.state ?? payload.state, 32)
  const state: 'pending' | 'allowed' | 'denied' =
    rawState === 'allowed' || rawState === 'denied' ? rawState : 'pending'
  return {
    requestId,
    turnId: optionalText(source.turn_id, 255) ?? fallbackTurnId,
    toolName,
    ruleSuggestion,
    command,
    path,
    target,
    reason,
    state,
    structuredPatch,
    addedLines,
    removedLines,
    ...(planApproval ? { planApproval } : {}),
  }
}

function parseStructuredPatch(value: unknown): StructuredPatchHunk[] | undefined {
  if (!Array.isArray(value) || value.length === 0 || value.length > 50) return undefined
  const hunks: StructuredPatchHunk[] = []
  for (const entry of value) {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) continue
    const raw = entry as Record<string, unknown>
    const rawOldStart = raw.oldStart ?? raw.old_start
    const rawOldLines = raw.oldLines ?? raw.old_lines
    const rawNewStart = raw.newStart ?? raw.new_start
    const rawNewLines = raw.newLines ?? raw.new_lines
    const oldStart = typeof rawOldStart === 'number' && Number.isFinite(rawOldStart) ? rawOldStart : null
    const oldLines = typeof rawOldLines === 'number' && Number.isFinite(rawOldLines) ? rawOldLines : null
    const newStart = typeof rawNewStart === 'number' && Number.isFinite(rawNewStart) ? rawNewStart : null
    const newLines = typeof rawNewLines === 'number' && Number.isFinite(rawNewLines) ? rawNewLines : null
    if (oldStart === null || oldLines === null || newStart === null || newLines === null || !Array.isArray(raw.lines)) continue
    const lines = raw.lines.filter((line): line is string => typeof line === 'string').slice(0, 500)
    hunks.push({
      oldStart,
      oldLines,
      newStart,
      newLines,
      old_start: oldStart,
      old_lines: oldLines,
      new_start: newStart,
      new_lines: newLines,
      lines,
    })
  }
  return hunks.length > 0 ? hunks : undefined
}

export type MoveConversationToProjectResponse = {
  conversation_id: string
  project_id: string | null
  project_name: string | null
  workspace: WorkspaceState
  migrated_files?: string[]
}

export function moveConversationToProject(
  client: ApiClient,
  conversationId: string,
  projectId: string | null,
  intent = client.createMutationIntent(),
): Promise<MoveConversationToProjectResponse> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/project`,
    method: 'PATCH',
    intent,
    body: { project_id: projectId },
    parse: (value) => {
      const data = record(value)
      return {
        conversation_id: typeof data.conversation_id === 'string' ? data.conversation_id : conversationId,
        project_id: typeof data.project_id === 'string' && data.project_id ? data.project_id : null,
        project_name: typeof data.project_name === 'string' && data.project_name ? data.project_name : null,
        workspace: parseWorkspaceState(data.workspace ?? {}),
        ...(Array.isArray(data.migrated_files)
          ? { migrated_files: data.migrated_files.filter((item): item is string => typeof item === 'string') }
          : {}),
      }
    },
  })
}

export function parseConversation(value: unknown): Conversation {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  if (!Array.isArray(data.messages) || !Array.isArray(data.turns)) throw invalidResponseError()
  const activities = parseSnapshotActivities(data.activities)
  const activityCursor = data.activity_cursor === undefined ? (activities[activities.length - 1]?.cursor ?? '0') : publicId(data.activity_cursor)
  const turns = data.turns.map((item) => {
    const t = item as Record<string, unknown>
    return {
      turn_id: string(t.turn_id),
      state: string(t.state),
      created_at: string(t.created_at),
      started_at: typeof t.started_at === 'string' ? t.started_at : null,
      finished_at: typeof t.finished_at === 'string' ? t.finished_at : null,
      scheduled_by_schedule_id: typeof t.scheduled_by_schedule_id === 'string' ? t.scheduled_by_schedule_id : null,
      code_mode: t.code_mode === 'code' ? ('code' as const) : null,
    }
  })
  const fallbackMode: ConversationMode = turns.some((t) => t.code_mode === 'code') ? 'code' : 'chat'
  const { mode, permission_mode } = parseModeAndPermission(data, fallbackMode)
  const parsedWorkspace = parseWorkspaceState(data.workspace ?? {})
  return {
    conversation_id: string(data.conversation_id),
    title: string(data.title),
    state: string(data.state),
    mode,
    permission_mode,
    project_id: typeof data.project_id === 'string' && data.project_id ? data.project_id : null,
    project_name: typeof data.project_name === 'string' && data.project_name ? data.project_name : parsedWorkspace.projectName,
    provider: string(data.provider),
    model_id: string(data.model_id),
    activities,
    activity_cursor: activityCursor,
    has_older_activities: data.has_older_activities === true,
    oldest_activity_id: typeof data.oldest_activity_id === 'number' && Number.isFinite(data.oldest_activity_id) ? data.oldest_activity_id : null,
    workspace: parsedWorkspace,
    context_usage: parseContextUsage(data.context_usage),
    messages: data.messages.map((item) => {
      const m = item as Record<string, unknown>
      const role = string(m.role)
      if (role !== 'user' && role !== 'assistant') throw invalidResponseError()
      return {
        message_id: string(m.message_id),
        role,
        content: typeof m.content === 'string' ? m.content : '',
        status: string(m.status),
        retryable: m.retryable === true,
        attachments: parseMessageAttachments(m.attachments),
        command: parseMessageCommand(m.command),
        turn_id: typeof m.turn_id === 'string' && m.turn_id ? m.turn_id : null,
      }
    }),
    turns,
  }
}

export const parseConversationDetail = parseConversation

function parseContextUsage(value: unknown): ContextUsage | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const data = value as Record<string, unknown>
  const count = (key: string): number | null => {
    const value = data[key]
    return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
  }
  const values = ['used_tokens', 'limit_tokens', 'percentage', 'system_prompt_tokens', 'history_tokens', 'input_tokens', 'tools_tokens', 'skills_tokens', 'mcps_tokens', 'omitted_messages', 'compaction_count'].map(count)
  if (values.some((item) => item === null)) return null
  return {
    used_tokens: values[0]!, limit_tokens: values[1]!, percentage: Math.min(100, values[2]!),
    system_prompt_tokens: values[3]!, history_tokens: values[4]!, input_tokens: values[5]!,
    tools_tokens: values[6]!, skills_tokens: values[7]!, mcps_tokens: values[8]!,
    omitted_messages: values[9]!, compaction_count: values[10]!, compaction_enabled: data.compaction_enabled === true,
  }
}

function parseMessageCommand(value: unknown): MessageCommand | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const data = value as Record<string, unknown>
  if (typeof data.command_id !== 'string' || typeof data.slug !== 'string') return null
  return { command_id: data.command_id, slug: data.slug, arguments: typeof data.arguments === 'string' ? data.arguments : '' }
}

function parseMessageAttachments(value: unknown): MessageAttachment[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => {
    const data = record(item)
    return {
      path: string(data.path), original_name: string(data.original_name),
      media_type: string(data.media_type), kind: string(data.kind),
      bytes: typeof data.bytes === 'number' ? data.bytes : 0,
    }
  })
}

function string(value: unknown): string {
  if (typeof value !== 'string' || !value) throw invalidResponseError()
  return value
}

type SseRecord = { data: string[]; event: string; id: string }

function parseJson(value: string): unknown {
  try { return JSON.parse(value) as unknown } catch { throw invalidResponseError() }
}

function parseHeartbeat(value: unknown): string {
  const item = record(value)
  if (Object.keys(item).some((key) => key !== 'cursor')) throw invalidResponseError()
  return publicId(item.cursor)
}

function publicId(value: unknown): string {
  if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) return String(value)
  if (typeof value === 'string' && /^[A-Za-z0-9._:-]{1,512}$/.test(value)) return value
  throw invalidResponseError()
}




function parseSnapshotActivities(value: unknown): ConversationActivityEvent[] {
  if (value === undefined) return []
  if (!Array.isArray(value)) throw invalidResponseError()
  return value.map((item) => {
    const data = record(item)
    const cursor = data.cursor ?? data.id ?? data.event_id
    return parseConversationActivityEvent(data, publicId(cursor))
  })
}


function optionalText(value: unknown, max = 255): string | undefined {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= max ? value : undefined
}

function optionalWorkspacePath(value: unknown): string | undefined {
  const path = optionalText(value, 512)
  if (!path || path.startsWith('/') || path.includes('\\') || path.split('/').some((part) => !part || part === '.' || part === '..')) return undefined
  return path
}

function parseUserQuestions(value: unknown): ConversationActivityEvent['questions'] | undefined {
  if (!Array.isArray(value) || value.length === 0 || value.length > 8) return undefined
  const ids = new Set<string>()
  const parsed = value.map((entry) => {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) return null
    const item = entry as Record<string, unknown>
    const id = optionalText(item.id, 64)
    const question = optionalText(item.question, 1000)
    const mode = item.mode
    if (!id || !question || ids.has(id) || (mode !== 'checkbox' && mode !== 'single_choice' && mode !== 'text')) return null
    const rawOptions = Array.isArray(item.options) ? item.options : []
    if (rawOptions.length > 12) return null
    const optionIds = new Set<string>()
    const options = rawOptions.map((option) => {
      if (typeof option !== 'object' || option === null || Array.isArray(option)) return null
      const candidate = option as Record<string, unknown>
      const optionId = optionalText(candidate.id, 64)
      const label = optionalText(candidate.label, 240)
      if (!optionId || !label || optionIds.has(optionId)) return null
      optionIds.add(optionId)
      const description = optionalText(candidate.description, 240)
      const preview = optionalText(candidate.preview, 480)
      return { id: optionId, label, ...(description ? { description } : {}), ...(preview ? { preview } : {}) }
    })
    if (options.some((option) => option === null) || (mode === 'text' && options.length > 0) || (mode === 'checkbox' && options.length < 1) || (mode === 'single_choice' && options.length < 2)) return null
    ids.add(id)
    const header = optionalText(item.header, 12)
    return {
      id, question, mode,
      options: options as Array<{ id: string; label: string; description?: string; preview?: string }>,
      placeholder: optionalText(item.placeholder, 240), ...(header ? { header } : {}),
    }
  })
  return parsed.some((item) => item === null) ? undefined : parsed as NonNullable<ConversationActivityEvent['questions']>
}


function parseMcpApprovalRequest(payload: Record<string, unknown>): ConversationActivityEvent['mcpApproval'] | undefined {
  if (payload.mcp_approval !== true) return undefined
  if (typeof payload.server !== 'object' || payload.server === null || Array.isArray(payload.server)) return undefined
  const data = payload.server as Record<string, unknown>
  const server_id = optionalText(data.server_id, 255)
  const display_name = optionalText(data.display_name, 255)
  const transport = optionalText(data.transport, 16)
  if (!server_id || !display_name || !transport) return undefined
  const secret_names = Array.isArray(data.secret_names) ? data.secret_names.filter((item): item is string => typeof item === 'string') : []
  return { server_id, display_name, transport, secret_names, catalog_id: optionalText(data.catalog_id, 64) ?? null }
}

function parsePluginApprovalRequest(payload: Record<string, unknown>): ConversationActivityEvent['pluginApproval'] | undefined {
  if (payload.plugin_approval !== true || typeof payload.plugin !== 'object' || payload.plugin === null || Array.isArray(payload.plugin)) return undefined
  const plugin = payload.plugin as Record<string, unknown>
  if (typeof plugin.plugin_id !== 'string' || typeof plugin.version !== 'string' || typeof plugin.display_name !== 'string') return undefined
  const textArray = (value: unknown) => Array.isArray(value) && value.every((item) => typeof item === 'string') ? value as string[] : []
  const objects = (value: unknown) => Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null && !Array.isArray(item)) : []
  return {
    plugin_id: plugin.plugin_id, version: plugin.version, display_name: plugin.display_name,
    description: typeof plugin.description === 'string' ? plugin.description : '', author: typeof plugin.author === 'string' ? plugin.author : '',
    warnings: textArray(plugin.warnings),
    skills: objects(plugin.skills).map((item) => ({ skill_id: String(item.skill_id ?? ''), name: String(item.name ?? ''), description: typeof item.description === 'string' ? item.description : undefined })),
    mcp_servers: objects(plugin.mcp_servers).map((item) => ({ slug: String(item.slug ?? ''), display_name: String(item.display_name ?? ''), transport: String(item.transport ?? ''), secret_names: textArray(item.secret_names) })),
    agents: objects(plugin.agents).map((item) => ({ agent_id: String(item.agent_id ?? ''), name: String(item.name ?? ''), role: typeof item.role === 'string' ? item.role : undefined })),
    commands: objects(plugin.commands).map((item) => ({ command_id: String(item.command_id ?? ''), slug: String(item.slug ?? ''), description: typeof item.description === 'string' ? item.description : undefined })),
    hooks: objects(plugin.hooks).map((item) => ({
      hook_id: String(item.hook_id ?? ''), event: String(item.event ?? ''), matcher: String(item.matcher ?? ''),
      command: String(item.command ?? ''), timeout_seconds: typeof item.timeout_seconds === 'number' ? item.timeout_seconds : 10,
    })),
    contribution_count: typeof plugin.contribution_count === 'number' ? plugin.contribution_count : 0,
  }
}

function parseCodeSessionProposalRequest(payload: Record<string, unknown>): CodeSessionProposalRequest | undefined {
  if (typeof payload.code_session_proposal !== 'object' || payload.code_session_proposal === null || Array.isArray(payload.code_session_proposal)) {
    return undefined
  }
  const raw = payload.code_session_proposal as Record<string, unknown>
  const title = optionalText(raw.title, 255)
  const prompt = typeof raw.prompt === 'string' && raw.prompt.trim().length > 0 && raw.prompt.length <= 32_000 ? raw.prompt : undefined
  if (!title || !prompt) return undefined
  const proposal_id = optionalText(raw.proposal_id, 255) ?? 'codeprop_default'
  const workspace_path = optionalText(raw.workspace_path, 1024) ?? null
  const provider = optionalText(raw.provider, 64) ?? null
  const model_id = optionalText(raw.model_id, 128) ?? null
  const permission_mode: CodeSessionProposalRequest['permission_mode'] =
    raw.permission_mode === 'plan_mode' || raw.permission_mode === 'ask_permissions'
      ? raw.permission_mode
      : 'accept_edits'
  const project_id = optionalText(raw.project_id, 255) ?? null
  return {
    proposal_id,
    title,
    prompt,
    workspace_path,
    provider,
    model_id,
    permission_mode,
    project_id,
  }
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

export function trustConversationWorkspace(
  client: ApiClient,
  conversationId: string,
  intent = client.createMutationIntent(),
): Promise<{ trusted: boolean; path: string }> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/workspace-trust`,
    method: 'POST',
    intent,
    parse: (value: unknown) => {
      const data = (value ?? {}) as Record<string, unknown>
      if (typeof data.trusted !== 'boolean' || typeof data.path !== 'string') throw invalidResponseError()
      return { trusted: data.trusted, path: data.path }
    },
  })
}

export type FriendTraitVector = {
  warmth: number
  humor: number
  sarcasm: number
  formality: number
  verbosity: number
  candor: number
  energy: number
}

export type FriendBondStage = 'acquaintance' | 'buddy' | 'close_friend' | 'confidant'

export type FriendPersonaState = {
  traits: FriendTraitVector
  affinity_score: number
  bond_stage: FriendBondStage
  style_notes: string[]
  current_mood: string
  soul_markdown_path: string | null
  updated_at?: string | null
  /** The name the companion answers to. */
  display_name: string
  /** How the companion addresses the person ('' = by no particular name). */
  user_nickname: string
  orb_theme: FriendOrbTheme
}

export const FRIEND_ORB_THEMES = ['aurora', 'ocean', 'sunset', 'mint', 'mono'] as const
export type FriendOrbTheme = (typeof FRIEND_ORB_THEMES)[number]

export type FriendMemoryKind = 'episodic' | 'inside_joke' | 'milestone' | 'emotional'

export type FriendMemoryEntry = {
  id: string
  kind: FriendMemoryKind
  content: string
  importance: number
  emotion_tag: string | null
  created_at: string
}

export type WordTiming = {
  word: string
  start_ms: number
  end_ms: number
  char_start: number
  char_end: number
}

export function clampFriendTraits(traits: Partial<FriendTraitVector>): FriendTraitVector {
  const clamp = (val: number | undefined, min: number, max: number, def: number) => {
    const n = typeof val === 'number' && Number.isFinite(val) ? val : def
    return Math.min(max, Math.max(min, Number(n.toFixed(2))))
  }
  return {
    warmth: clamp(traits.warmth, 0.3, 0.95, 0.65),
    humor: clamp(traits.humor, 0.1, 0.9, 0.5),
    sarcasm: clamp(traits.sarcasm, 0.0, 0.75, 0.25),
    formality: clamp(traits.formality, 0.05, 0.85, 0.35),
    verbosity: clamp(traits.verbosity, 0.15, 0.8, 0.4),
    candor: clamp(traits.candor, 0.5, 0.95, 0.7),
    energy: clamp(traits.energy, 0.2, 0.9, 0.55),
  }
}

export function parseFriendPersonaState(value: unknown): FriendPersonaState {
  const outer = record(value)
  const data = typeof outer.persona === 'object' && outer.persona !== null && !Array.isArray(outer.persona)
    ? (outer.persona as Record<string, unknown>)
    : outer
  const rawTraits = typeof data.traits === 'object' && data.traits !== null ? (data.traits as Partial<FriendTraitVector>) : {}
  const bondStage: FriendBondStage =
    data.bond_stage === 'buddy' || data.bond_stage === 'close_friend' || data.bond_stage === 'confidant'
      ? data.bond_stage
      : 'acquaintance'
  return {
    traits: clampFriendTraits(rawTraits),
    affinity_score: typeof data.affinity_score === 'number' && Number.isFinite(data.affinity_score) ? data.affinity_score : 0,
    bond_stage: bondStage,
    style_notes: Array.isArray(data.style_notes) ? data.style_notes.filter((s): s is string => typeof s === 'string') : [],
    current_mood: typeof data.current_mood === 'string' && data.current_mood ? data.current_mood : 'neutro',
    soul_markdown_path: typeof data.soul_markdown_path === 'string' ? data.soul_markdown_path : null,
    updated_at: typeof data.updated_at === 'string' ? data.updated_at : null,
    display_name: typeof data.display_name === 'string' && data.display_name.trim() ? data.display_name : 'Orin',
    user_nickname: typeof data.user_nickname === 'string' ? data.user_nickname : '',
    orb_theme: (FRIEND_ORB_THEMES as readonly string[]).includes(String(data.orb_theme))
      ? (data.orb_theme as FriendOrbTheme)
      : 'aurora',
  }
}

export function ensureFriendConversation(
  client: ApiClient = createBrowserApiClient(),
  intent = client.createMutationIntent(),
): Promise<{ conversation_id: string; title: string; mode: ConversationMode; state: string }> {
  return client.request({
    path: '/v1/conversations/friend/ensure',
    method: 'POST',
    intent,
    body: {},
    parse: (value) => {
      const outer = record(value)
      const data = typeof outer.conversation === 'object' && outer.conversation !== null && !Array.isArray(outer.conversation)
        ? (outer.conversation as Record<string, unknown>)
        : outer
      const id =
        typeof data.conversation_id === 'string'
          ? data.conversation_id
          : typeof data.id === 'string'
            ? data.id
            : 'conv-orin-friend'
      return {
        conversation_id: id,
        title: typeof data.title === 'string' ? data.title : 'Orin Friend',
        mode: 'friend',
        state: typeof data.state === 'string' ? data.state : typeof data.status === 'string' ? data.status : 'idle',
      }
    },
  })
}

export function getFriendPersona(
  client: ApiClient = createBrowserApiClient(),
  signal?: AbortSignal,
): Promise<FriendPersonaState> {
  return client.request({
    path: '/v1/friend/persona',
    signal,
    parse: parseFriendPersonaState,
  })
}

export function updateFriendPersona(
  client: ApiClient = createBrowserApiClient(),
  input: {
    traits?: Partial<FriendTraitVector>
    style_notes?: string[]
    current_mood?: string
    display_name?: string
    user_nickname?: string
    orb_theme?: FriendOrbTheme
  },
  intent = client.createMutationIntent(),
): Promise<FriendPersonaState> {
  const safeTraits = input.traits ? clampFriendTraits(input.traits) : undefined
  return client.request({
    path: '/v1/friend/persona',
    method: 'PUT',
    intent,
    body: {
      ...(safeTraits ? { traits: safeTraits } : {}),
      ...(input.style_notes ? { style_notes: input.style_notes } : {}),
      ...(input.current_mood ? { current_mood: input.current_mood } : {}),
      ...(input.display_name !== undefined ? { display_name: input.display_name } : {}),
      ...(input.user_nickname !== undefined ? { user_nickname: input.user_nickname } : {}),
      ...(input.orb_theme ? { orb_theme: input.orb_theme } : {}),
    },
    parse: parseFriendPersonaState,
  })
}

export function syncFriendSoul(
  client: ApiClient = createBrowserApiClient(),
  intent = client.createMutationIntent(),
): Promise<FriendPersonaState> {
  return client.request({
    path: '/v1/friend/soul/sync',
    method: 'POST',
    intent,
    body: {},
    parse: parseFriendPersonaState,
  })
}

export function listFriendMemories(
  client: ApiClient = createBrowserApiClient(),
  signal?: AbortSignal,
): Promise<FriendMemoryEntry[]> {
  return client.request({
    path: '/v1/friend/memories',
    signal,
    parse: (value) => {
      const items = Array.isArray(value)
        ? value
        : typeof value === 'object' && value !== null && Array.isArray((value as { items?: unknown }).items)
          ? (value as { items: unknown[] }).items
          : []
      return items.map((item) => {
        const d = record(item)
        const kind: FriendMemoryKind =
          d.kind === 'inside_joke' || d.kind === 'milestone' || d.kind === 'emotional' ? d.kind : 'episodic'
        return {
          id: typeof d.id === 'string' ? d.id : String(d.memory_id ?? ''),
          kind,
          content: typeof d.content === 'string' ? d.content : '',
          importance: typeof d.importance === 'number' ? d.importance : 0.5,
          emotion_tag: typeof d.emotion_tag === 'string' ? d.emotion_tag : null,
          created_at: typeof d.created_at === 'string' ? d.created_at : new Date().toISOString(),
        }
      })
    },
  })
}

export function deleteFriendMemory(
  client: ApiClient = createBrowserApiClient(),
  memoryId: string,
  intent = client.createMutationIntent(),
): Promise<void> {
  return client.request({
    path: `/v1/friend/memories/${encodeURIComponent(memoryId)}`,
    method: 'DELETE',
    intent,
    parse: () => {
      return undefined
    },
  })
}


export type LiveVoiceServerEvent =
  | {
      type: 'session.ready'
      conversation_id: string
      audio_epoch: number
      hardware_profile?: Record<string, unknown>
      voice_config?: Record<string, unknown>
    }
  | {
      type: 'tts.chunk'
      epoch: number
      audio_epoch?: number
      chunk_seq: number
      clean_text: string
      /** Raw 16-bit PCM; absent when the engine returned an encoded file. */
      pcm_base64?: string
      /** `pcm16` (default) or an encoded `mp3`/`wav` in `audio_base64`. */
      encoding?: 'pcm16' | 'mp3' | 'wav'
      audio_base64?: string
      mime?: string
      sample_rate: number
      duration_ms: number
      word_timings: WordTiming[]
      provider?: string
    }
  | { type: 'tts.error'; code: string; message: string; text?: string; hint?: string }
  | {
      type: 'audio.clear'
      previous_epoch: number
      new_epoch: number
      interrupted_chunk_seq: number
      played_ms: number
      truncated_text: string
      unspoken_text: string
      system_receipt: string
    }
  | { type: 'stt.processing'; provider: string; audio_ms: number }
  | { type: 'stt.error'; code: string; message: string; hint?: string }
  | { type: 'stt.empty'; audio_epoch: number }
  | {
      type: 'stt.transcript'
      is_end_of_utterance: boolean
      text: string
      audio_epoch: number
    }
  | {
      type: 'vad.eou_wait'
      is_end_of_utterance: false
      required_silence_ms: number
      silence_ms?: number
      partial_text: string
      detected_hold_marker: string | null
    }
  | {
      type: 'vad.echo_discarded'
      text: string
      audio_epoch: number
    }
  | { type: 'vad.frame'; is_speech: boolean; buffered_bytes: number }
  | { type: 'config.updated'; voice_config: Record<string, unknown> }
  | { type: 'error'; code: string; message: string }

export type LiveVoiceSocketStatus = 'connecting' | 'open' | 'closed' | 'unauthorized'

export type LiveVoiceSocketConfig = {
  stt_provider?: string
  stt_model?: string
  tts_provider?: string
  tts_model?: string
  voice_id?: string
  speed?: number
  language?: string
}

export type LiveVoiceSocketHandle = {
  sendBargeIn: (epoch: number, chunkSeq: number, playedMs: number) => void
  sendSpeakText: (text: string, instructions?: string) => void
  sendPlaybackProgress: (chunkSeq: number, playedMs: number) => void
  sendSpeechEnd: (transcriptHint: string, silenceMs?: number, extra?: Record<string, unknown>) => void
  sendPcmFrame: (pcmBytes: ArrayBuffer) => void
  sendConfig: (config: LiveVoiceSocketConfig) => void
  sendAudioReset: () => void
  isOpen: () => boolean
  close: () => void
}

export type LiveVoiceSocketOptions = {
  csrfToken?: string
  onStatus?: (status: LiveVoiceSocketStatus) => void
}

export function connectLiveVoiceSession(
  conversationId: string,
  onEvent: (event: LiveVoiceServerEvent) => void,
  options: LiveVoiceSocketOptions = {},
): LiveVoiceSocketHandle | null {
  if (typeof window === 'undefined' || typeof window.WebSocket === 'undefined' || !conversationId) {
    return null
  }
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const query = options.csrfToken ? `?csrf=${encodeURIComponent(options.csrfToken)}` : ''
  const url = `${proto}//${window.location.host}/v1/conversations/${encodeURIComponent(conversationId)}/live${query}`
  let ws: WebSocket | null = null
  // Config sent before the socket opens is replayed on open, so a caller never
  // has to wait for `session.ready` before choosing its engines.
  let pendingConfig: LiveVoiceSocketConfig | null = null
  try {
    ws = new window.WebSocket(url)
    ws.binaryType = 'arraybuffer'
    options.onStatus?.('connecting')
    ws.onopen = () => {
      options.onStatus?.('open')
      if (pendingConfig && ws) ws.send(JSON.stringify({ type: 'config', ...pendingConfig }))
    }
    ws.onclose = (event) => {
      options.onStatus?.(event.code === 4401 ? 'unauthorized' : 'closed')
    }
    ws.onmessage = (msg) => {
      if (typeof msg.data !== 'string') return
      try {
        const parsed = JSON.parse(msg.data) as LiveVoiceServerEvent
        if (parsed && typeof parsed.type === 'string') {
          onEvent(parsed)
        }
      } catch {
        // Ignore malformed frames
      }
    }
  } catch {
    return null
  }

  const isOpen = () => Boolean(ws && ws.readyState === window.WebSocket.OPEN)
  const sendJson = (payload: Record<string, unknown>) => {
    if (ws && isOpen()) {
      ws.send(JSON.stringify(payload))
    }
  }

  return {
    sendBargeIn: (epoch, chunkSeq, playedMs) =>
      sendJson({ type: 'barge_in', epoch, chunk_seq: chunkSeq, played_ms: playedMs }),
    sendSpeakText: (text, instructions) => sendJson({ type: 'speak_text', text, ...(instructions ? { instructions } : {}) }),
    sendPlaybackProgress: (chunkSeq, playedMs) =>
      sendJson({ type: 'playback_progress', chunk_seq: chunkSeq, played_ms: playedMs }),
    sendSpeechEnd: (transcriptHint, silenceMs = 420, extra = {}) =>
      sendJson({ type: 'audio_speech_end', transcript_hint: transcriptHint, silence_ms: silenceMs, ...extra }),
    sendPcmFrame: (pcmBytes) => {
      if (ws && isOpen()) {
        ws.send(pcmBytes)
      }
    },
    sendConfig: (config) => {
      pendingConfig = { ...(pendingConfig ?? {}), ...config }
      sendJson({ type: 'config', ...config })
    },
    sendAudioReset: () => sendJson({ type: 'audio_reset' }),
    isOpen,
    close: () => {
      try {
        ws?.close()
      } catch {
        // Ignore close errors
      }
    },
  }
}
