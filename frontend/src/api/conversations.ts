import type { ApiClient } from './client'
import { invalidResponseError, parseApiErrorResponse } from './errors'
import type { ProviderName } from './providers'
import { parseWorkspaceState, type WorkspaceState } from './workspace'
import { kindFor, stateFor, type ContextUsage, type ConversationActivityEvent, type StructuredPatchHunk, type TodoItem, type ToolPermissionDecision, type ToolPermissionRequest } from '../features/conversations/activityTypes'
import type { MessageCommand } from '../features/conversations/MessageCommandChip'

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
}

export type CodeModeChoice = 'auto' | 'code' | 'chat'

export type ConversationReceipt = {
  conversation_id: string
  title: string
  turn_id: string
  message_id: string
  state: string
}

export type MessageAttachment = { path: string; original_name: string; media_type: string; kind: string; bytes: number }

export type Conversation = { conversation_id: string; title: string; state: string; messages: ConversationMessage[]; turns: ConversationTurn[]; provider: string; model_id: string; activities: ConversationActivityEvent[]; activity_cursor: string; workspace: WorkspaceState; context_usage: ContextUsage | null }
export type ConversationMessage = { message_id: string; role: 'user' | 'assistant'; content: string; status: string; retryable: boolean; attachments: MessageAttachment[]; command: MessageCommand | null }
export type ConversationTurn = { turn_id: string; state: string; created_at: string; started_at: string | null; finished_at: string | null; scheduled_by_schedule_id: string | null; code_mode: 'code' | null }

export function createConversation(client: ApiClient, input: CreateConversationInput, intent = client.createMutationIntent()): Promise<ConversationReceipt> {
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
      // Omitting the default keeps older servers and integrations compatible;
      // the API itself defaults to automatic classification.
      ...(input.code_mode && input.code_mode !== 'auto' ? { code_mode: input.code_mode } : {}),
    },
    parse: parseConversationReceipt,
  })
}

function parseConversationReceipt(value: unknown): ConversationReceipt {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  return { conversation_id: string(data.conversation_id), title: string(data.title), turn_id: string(data.turn_id), message_id: string(data.message_id), state: string(data.state) }
}

export function getConversation(client: ApiClient, id: string): Promise<Conversation> {
  return client.request({ path: `/v1/conversations/${encodeURIComponent(id)}`, parse: parseConversation })
}

export function listConversations(client: ApiClient): Promise<{ items: Array<Pick<Conversation, 'conversation_id' | 'title' | 'state'>> }> {
  return client.request({ path: '/v1/conversations', parse: (value) => {
    if (typeof value !== 'object' || value === null || !Array.isArray((value as { items?: unknown }).items)) throw invalidResponseError()
    return { items: (value as { items: unknown[] }).items.map((item) => { const data = item as Record<string, unknown>; return { conversation_id: string(data.conversation_id), title: string(data.title), state: string(data.state) } }) }
  } })
}

export function sendConversationMessage(client: ApiClient, id: string, message: string, attachments: string[] = [], selection?: Pick<CreateConversationInput, 'provider' | 'model_id'>, codeMode: CodeModeChoice = 'auto', intent = client.createMutationIntent()): Promise<ConversationReceipt> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(id)}/messages`, method: 'POST', expectedStatus: 201, intent,
    body: { message, attachments, ...(codeMode !== 'auto' ? { code_mode: codeMode } : {}), ...(selection ? { selection } : {}) }, parse: parseConversationReceipt,
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

  const event: ConversationActivityEvent = {
    eventId: publicId(item.event_id ?? item.id ?? cursor),
    cursor: publicId(cursor),
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
  assign('path', optionalText(payload.path, 512))
  assign('screenshotPath', optionalWorkspacePath(payload.screenshot_path))
  assign('codeStage', optionalText(payload.stage, 64))
  if (payload.code_approval === true) event.codeApproval = true
  assign('memoryId', optionalText(payload.memory_id, 255))
  assign('memoryProjectId', optionalText(payload.project_id, 255))
  if (payload.scope === 'user' || payload.scope === 'project') event.memoryScope = payload.scope
  assign('occurredAt', optionalText(item.occurred_at, 64))
  const contextUsage = parseContextUsage(payload)
  if (contextUsage) event.contextUsage = contextUsage
  const questions = parseUserQuestions(payload.questions)
  if (questions) event.questions = questions
  const mcpApproval = parseMcpApprovalRequest(payload)
  if (mcpApproval) event.mcpApproval = mcpApproval
  const pluginApproval = parsePluginApprovalRequest(payload)
  if (pluginApproval) event.pluginApproval = pluginApproval
  const toolPermission = parseToolPermissionRequest(payload, event.turnId)
  if (toolPermission) {
    event.toolPermission = toolPermission
    event.kind = 'tool'
    event.state = 'waiting_user'
  }
  const structuredPatch = parseStructuredPatch(payload.structured_patch)
  if (structuredPatch) event.structuredPatch = structuredPatch
  if (typeof payload.added_lines === 'number' && Number.isFinite(payload.added_lines) && payload.added_lines >= 0) {
    event.addedLines = payload.added_lines
  }
  if (typeof payload.removed_lines === 'number' && Number.isFinite(payload.removed_lines) && payload.removed_lines >= 0) {
    event.removedLines = payload.removed_lines
  }
  if (typeof payload.output_tail === 'string' && payload.output_tail.length > 0 && payload.output_tail.length <= 4_000) {
    event.outputTail = payload.output_tail
  }
  if (typeof payload.elapsed_ms === 'number' && Number.isFinite(payload.elapsed_ms) && payload.elapsed_ms >= 0) {
    event.elapsedMs = payload.elapsed_ms
  }
  const todos = parseTodoItems(payload.todos)
  if (todos) event.todos = todos
  if (errorCode) event.errorCode = errorCode
  if (typeof payload.content === 'string' && payload.content.length <= 16_000) event.content = payload.content
  return event
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
    items.push({ id, content, status, activeForm })
  }
  return items.length > 0 ? items : undefined
}

function parseToolPermissionRequest(payload: Record<string, unknown>, fallbackTurnId?: string): ToolPermissionRequest | undefined {
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
  return {
    requestId,
    turnId: optionalText(source.turn_id, 255) ?? fallbackTurnId,
    toolName,
    ruleSuggestion: optionalText(source.rule_suggestion, 255) ?? optionalText(payload.rule_suggestion, 255),
    command: optionalText(source.command, 2000) ?? optionalText(payload.command, 2000),
    path: optionalText(source.path, 512) ?? optionalText(payload.path, 512),
    structuredPatch,
    addedLines,
    removedLines,
  }
}

function parseStructuredPatch(value: unknown): StructuredPatchHunk[] | undefined {
  if (!Array.isArray(value) || value.length === 0 || value.length > 50) return undefined
  const hunks: StructuredPatchHunk[] = []
  for (const entry of value) {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) continue
    const raw = entry as Record<string, unknown>
    const oldStart = typeof raw.oldStart === 'number' && Number.isFinite(raw.oldStart) ? raw.oldStart : null
    const oldLines = typeof raw.oldLines === 'number' && Number.isFinite(raw.oldLines) ? raw.oldLines : null
    const newStart = typeof raw.newStart === 'number' && Number.isFinite(raw.newStart) ? raw.newStart : null
    const newLines = typeof raw.newLines === 'number' && Number.isFinite(raw.newLines) ? raw.newLines : null
    if (oldStart === null || oldLines === null || newStart === null || newLines === null || !Array.isArray(raw.lines)) continue
    const lines = raw.lines.filter((line): line is string => typeof line === 'string').slice(0, 500)
    hunks.push({ oldStart, oldLines, newStart, newLines, lines })
  }
  return hunks.length > 0 ? hunks : undefined
}

export function parseConversation(value: unknown): Conversation {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  if (!Array.isArray(data.messages) || !Array.isArray(data.turns)) throw invalidResponseError()
  const activities = parseSnapshotActivities(data.activities)
  const activityCursor = data.activity_cursor === undefined ? (activities[activities.length - 1]?.cursor ?? '0') : publicId(data.activity_cursor)
  return { conversation_id: string(data.conversation_id), title: string(data.title), state: string(data.state), provider: string(data.provider), model_id: string(data.model_id), activities, activity_cursor: activityCursor, workspace: parseWorkspaceState(data.workspace ?? {}), context_usage: parseContextUsage(data.context_usage), messages: data.messages.map((item) => { const m = item as Record<string, unknown>; const role = string(m.role); if (role !== 'user' && role !== 'assistant') throw invalidResponseError(); return { message_id: string(m.message_id), role, content: typeof m.content === 'string' ? m.content : '', status: string(m.status), retryable: m.retryable === true, attachments: parseMessageAttachments(m.attachments), command: parseMessageCommand(m.command) } }), turns: data.turns.map((item) => { const t = item as Record<string, unknown>; return { turn_id: string(t.turn_id), state: string(t.state), created_at: string(t.created_at), started_at: typeof t.started_at === 'string' ? t.started_at : null, finished_at: typeof t.finished_at === 'string' ? t.finished_at : null, scheduled_by_schedule_id: typeof t.scheduled_by_schedule_id === 'string' ? t.scheduled_by_schedule_id : null, code_mode: t.code_mode === 'code' ? 'code' : null } }) }
}

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
  if (typeof value === 'string' && /^[A-Za-z0-9._:-]{1,255}$/.test(value)) return value
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
      return { id: optionId, label }
    })
    if (options.some((option) => option === null) || (mode === 'text' && options.length > 0) || (mode === 'checkbox' && options.length < 1) || (mode === 'single_choice' && options.length < 2)) return null
    ids.add(id)
    return { id, question, mode, options: options as Array<{ id: string; label: string }>, placeholder: optionalText(item.placeholder, 240) }
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

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}
