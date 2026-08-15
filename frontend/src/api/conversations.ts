import type { ApiClient } from './client'
import { invalidResponseError, parseApiErrorResponse } from './errors'
import type { ProviderName } from './providers'
import { parseWorkspaceState, type WorkspaceState } from './workspace'
import { kindFor, stateFor, type ConversationActivityEvent } from '../features/conversations/activityTypes'

export type CreateConversationInput = {
  message: string
  provider: ProviderName
  model_id: string
  workspace_id?: string | null
  attachments?: string[]
}

export type ConversationReceipt = {
  conversation_id: string
  title: string
  turn_id: string
  message_id: string
  state: string
}

export type MessageAttachment = { path: string; original_name: string; media_type: string; kind: string; bytes: number }

export type Conversation = { conversation_id: string; title: string; state: string; messages: ConversationMessage[]; turns: ConversationTurn[]; provider: string; model_id: string; activities: ConversationActivityEvent[]; activity_cursor: string; workspace: WorkspaceState }
export type ConversationMessage = { message_id: string; role: 'user' | 'assistant'; content: string; status: string; retryable: boolean; attachments: MessageAttachment[] }
export type ConversationTurn = { turn_id: string; state: string; created_at: string; started_at: string | null; finished_at: string | null; scheduled_by_schedule_id: string | null }

export function createConversation(client: ApiClient, input: CreateConversationInput, intent = client.createMutationIntent()): Promise<ConversationReceipt> {
  return client.request({
    path: '/v1/conversations', method: 'POST', expectedStatus: 201, intent,
    body: { message: input.message, selection: { provider: input.provider, model_id: input.model_id }, workspace_id: input.workspace_id ?? null, attachments: input.attachments ?? [] },
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

export function sendConversationMessage(client: ApiClient, id: string, message: string, attachments: string[] = [], intent = client.createMutationIntent()): Promise<ConversationReceipt> {
  return client.request({ path: `/v1/conversations/${encodeURIComponent(id)}/messages`, method: 'POST', expectedStatus: 201, intent, body: { message, attachments }, parse: parseConversationReceipt })
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
  assign('occurredAt', optionalText(item.occurred_at, 64))
  const questions = parseUserQuestions(payload.questions)
  if (questions) event.questions = questions
  const mcpApproval = parseMcpApprovalRequest(payload)
  if (mcpApproval) event.mcpApproval = mcpApproval
  const pluginApproval = parsePluginApprovalRequest(payload)
  if (pluginApproval) event.pluginApproval = pluginApproval
  if (errorCode) event.errorCode = errorCode
  if (typeof payload.content === 'string' && payload.content.length <= 16_000) event.content = payload.content
  return event
}

export function parseConversation(value: unknown): Conversation {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  if (!Array.isArray(data.messages) || !Array.isArray(data.turns)) throw invalidResponseError()
  const activities = parseSnapshotActivities(data.activities)
  const activityCursor = data.activity_cursor === undefined ? (activities[activities.length - 1]?.cursor ?? '0') : publicId(data.activity_cursor)
  return { conversation_id: string(data.conversation_id), title: string(data.title), state: string(data.state), provider: string(data.provider), model_id: string(data.model_id), activities, activity_cursor: activityCursor, workspace: parseWorkspaceState(data.workspace ?? {}), messages: data.messages.map((item) => { const m = item as Record<string, unknown>; const role = string(m.role); if (role !== 'user' && role !== 'assistant') throw invalidResponseError(); return { message_id: string(m.message_id), role, content: typeof m.content === 'string' ? m.content : '', status: string(m.status), retryable: m.retryable === true, attachments: parseMessageAttachments(m.attachments) } }), turns: data.turns.map((item) => { const t = item as Record<string, unknown>; return { turn_id: string(t.turn_id), state: string(t.state), created_at: string(t.created_at), started_at: typeof t.started_at === 'string' ? t.started_at : null, finished_at: typeof t.finished_at === 'string' ? t.finished_at : null, scheduled_by_schedule_id: typeof t.scheduled_by_schedule_id === 'string' ? t.scheduled_by_schedule_id : null } }) }
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
    contribution_count: typeof plugin.contribution_count === 'number' ? plugin.contribution_count : 0,
  }
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}
