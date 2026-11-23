/**
 * A Code session's tools beyond sending a message (W7): the message queue, /compact, /context, the file history and
 * per-message rewind, background tasks, the command catalog and the status line.
 */
import type { ApiClient } from './client'
import { parseApiErrorResponse } from './errors'
import { bool, list, num, optStr, rec, str, strings } from './parse'

const base = (conversationId: string) => `/v1/conversations/${encodeURIComponent(conversationId)}`

export type QueuedInput = { queueId: string; content: string; createdAt: string | null }

function queued(value: unknown): QueuedInput | null {
  const item = rec(value)
  return str(item.queue_id) ? { queueId: str(item.queue_id), content: str(item.content), createdAt: optStr(item.created_at) } : null
}

export function listQueue(client: ApiClient, conversationId: string): Promise<QueuedInput[]> {
  return client.request({ path: `${base(conversationId)}/queue`, parse: (value) => list(rec(value).items, queued) })
}

/** Sends now when the conversation is idle, or queues the text for the running turn. */
export function enqueueMessage(client: ApiClient, conversationId: string, message: string, intent = client.createMutationIntent()):
  Promise<{ queued: boolean; queueId: string | null; turnId: string | null }> {
  return client.request({
    path: `${base(conversationId)}/queue`, method: 'POST', intent, body: { message },
    parse: (value) => {
      const data = rec(value)
      return { queued: bool(data.queued), queueId: optStr(data.queue_id), turnId: optStr(data.turn_id) }
    },
  })
}

export function removeQueued(client: ApiClient, conversationId: string, queueId: string, intent = client.createMutationIntent()): Promise<boolean> {
  return client.request({
    path: `${base(conversationId)}/queue/${encodeURIComponent(queueId)}`, method: 'DELETE', intent,
    parse: (value) => bool(rec(value).cancelled),
  })
}

export function compactConversation(client: ApiClient, conversationId: string, instructions?: string, intent = client.createMutationIntent()):
  Promise<{ turnId: string | null }> {
  return client.request({
    path: `${base(conversationId)}/compact`, method: 'POST', intent,
    body: instructions?.trim() ? { instructions: instructions.trim() } : {},
    parse: (value) => ({ turnId: optStr(rec(value).turn_id) }),
  })
}

export type ContextCategory = { key: string; tokens: number }
export type ContextReport = {
  available: boolean
  limitTokens: number
  usedTokens: number
  percentage: number
  compactionCount: number
  categories: ContextCategory[]
  measuredAt: string | null
}

export function parseContextReport(value: unknown): ContextReport {
  const data = rec(value)
  return {
    available: bool(data.available), limitTokens: num(data.limit_tokens), usedTokens: num(data.used_tokens),
    percentage: num(data.percentage), compactionCount: num(data.compaction_count),
    categories: list(data.categories, (item) => { const category = rec(item); return str(category.key) ? { key: str(category.key), tokens: Math.max(0, num(category.tokens)) } : null }),
    measuredAt: optStr(data.measured_at),
  }
}

export function getContext(client: ApiClient, conversationId: string): Promise<ContextReport> {
  return client.request({ path: `${base(conversationId)}/context`, parse: parseContextReport })
}

export type FileHistoryItem = { messageId: string; turnId: string; kind: string; createdAt: string | null; rewoundAt: string | null; files: string[] }

export function getFileHistory(client: ApiClient, conversationId: string): Promise<FileHistoryItem[]> {
  return client.request({
    path: `${base(conversationId)}/file-history`,
    parse: (value) => list(rec(value).items, (item) => {
      const entry = rec(item)
      return str(entry.message_id) ? {
        messageId: str(entry.message_id), turnId: str(entry.turn_id), kind: str(entry.kind, 'message'),
        createdAt: optStr(entry.created_at), rewoundAt: optStr(entry.rewound_at), files: strings(entry.files),
      } : null
    }),
  })
}

export type CodeRewindResult = {
  messageId: string
  restored: string[]
  deleted: string[]
  unchanged: string[]
  skipped: Array<{ path: string; reason: string }>
  blocked: boolean
  undoId: string | null
  /** W7: files rewound without an undo copy, because the person chose to (`forceUnbacked`). */
  unbacked: string[]
}

/** W7: the one reason a person may waive for a file (its current content is too large to back up). */
export const TOO_LARGE_TO_BACK_UP = 'its current content is too large to keep an undo copy'

function rewindBody(mode: string, forceUnbacked: readonly string[]) {
  return forceUnbacked.length > 0 ? { mode, force_unbacked: forceUnbacked.slice(0, 50) } : { mode }
}

export function rewindCode(client: ApiClient, conversationId: string, messageId: string, forceUnbacked: readonly string[] = [],
  intent = client.createMutationIntent()): Promise<CodeRewindResult> {
  return client.request({
    path: `${base(conversationId)}/messages/${encodeURIComponent(messageId)}/rewind`, method: 'POST', intent, body: rewindBody('code', forceUnbacked),
    parse: (value) => {
      const data = rec(value)
      return {
        messageId: str(data.message_id, messageId), restored: strings(data.restored), deleted: strings(data.deleted),
        unchanged: strings(data.unchanged),
        skipped: list(data.skipped, (item) => { const entry = rec(item); return { path: str(entry.path), reason: str(entry.reason) } }),
        blocked: bool(data.blocked), undoId: optStr(data.undo_id), unbacked: strings(data.unbacked),
      }
    },
  })
}

export type ShellTask = {
  taskId: string
  kind: 'shell'
  pid: number
  command: string
  description: string
  startedAt: string | null
  url: string | null
  state: 'running' | 'finished' | 'unknown'
  stoppable: boolean
  logTail: string
}

function shellTask(value: unknown): ShellTask | null {
  const item = rec(value)
  if (!str(item.task_id) || item.kind !== 'shell') return null
  const state = item.state === 'running' || item.state === 'finished' ? item.state : 'unknown'
  return {
    taskId: str(item.task_id), kind: 'shell', pid: num(item.pid), command: str(item.command), description: str(item.description),
    startedAt: optStr(item.started_at), url: optStr(item.url), state, stoppable: bool(item.stoppable), logTail: str(item.log_tail),
  }
}

export function listTasks(client: ApiClient, conversationId: string): Promise<ShellTask[]> {
  return client.request({ path: `${base(conversationId)}/tasks`, parse: (value) => list(rec(value).tasks, shellTask) })
}

export function stopTask(client: ApiClient, conversationId: string, taskId: string, intent = client.createMutationIntent()):
  Promise<{ stopped: boolean; tasks: ShellTask[] }> {
  return client.request({
    path: `${base(conversationId)}/tasks/${encodeURIComponent(taskId)}/stop`, method: 'POST', intent, body: {},
    parse: (value) => { const data = rec(value); return { stopped: bool(data.stopped), tasks: list(data.tasks, shellTask) } },
  })
}

export type CatalogCommand = { name: string; description: string; kind: string; origin: string; argumentHint: string; context: string | null }

export function listCommandCatalog(client: ApiClient, conversationId: string): Promise<{ commands: CatalogCommand[]; trusted: boolean }> {
  return client.request({
    path: `${base(conversationId)}/commands`,
    parse: (value) => {
      const data = rec(value)
      return {
        trusted: bool(data.trusted),
        commands: list(data.commands, (item) => {
          const command = rec(item)
          return str(command.name) ? {
            name: str(command.name), description: str(command.description), kind: str(command.kind), origin: str(command.origin),
            argumentHint: str(command.argument_hint), context: optStr(command.context),
          } : null
        }, 500),
      }
    },
  })
}

export type StatusLine = { enabled: boolean; lines: string[]; error: string | null }

export function runStatusLine(client: ApiClient, conversationId: string, signal?: AbortSignal, intent = client.createMutationIntent()): Promise<StatusLine> {
  return client.request({
    path: `${base(conversationId)}/statusline`, method: 'POST', intent, body: {}, signal,
    parse: (value) => {
      const data = rec(value)
      return { enabled: bool(data.enabled), lines: strings(data.lines, 5), error: optStr(data.error) }
    },
  })
}

export type InstanceInfo = { version: string; mode: string; capabilities: Record<string, boolean> }

export function getInstance(client: ApiClient, signal?: AbortSignal): Promise<InstanceInfo> {
  return client.request({
    path: '/v1/instance', signal,
    parse: (value) => {
      const data = rec(value)
      const capabilities = Object.fromEntries(Object.entries(rec(data.capabilities)).filter((entry): entry is [string, boolean] => typeof entry[1] === 'boolean'))
      return { version: str(data.version), mode: str(data.mode), capabilities }
    },
  })
}

export type AddedDirectory = { directoryId: string; path: string }

/** `/add-dir`: a folder this session may also read and write (local profile only). */
export function addDirectory(client: ApiClient, conversationId: string, path: string, intent = client.createMutationIntent()): Promise<AddedDirectory> {
  return client.request({
    path: `${base(conversationId)}/directories`, method: 'POST', intent, body: { path },
    parse: (value) => { const data = rec(value); return { directoryId: str(data.directory_id), path: str(data.path, path) } },
  })
}

/** `!command` (W7): a turn that runs the person's command in the Code session's folder, without the model. */
export function runShell(client: ApiClient, conversationId: string, command: string, intent = client.createMutationIntent()): Promise<{ turnId: string | null }> {
  return client.request({
    path: `${base(conversationId)}/shell`, method: 'POST', intent, body: { command },
    parse: (value) => ({ turnId: optStr(rec(value).turn_id) }),
  })
}

export type ForkResult = { conversationId: string; title: string; prompt: string | null; messages: number; projectId: string | null }

function forkResult(value: unknown): ForkResult {
  const data = rec(value)
  return {
    conversationId: str(data.conversation_id),
    title: str(data.title),
    prompt: optStr(data.prompt),
    messages: num(data.messages),
    projectId: optStr(data.project_id),
  }
}

/** /branch and /fork: a new conversation with this one's messages (or those before one of the person's messages). */
export function forkConversation(client: ApiClient, conversationId: string, options: { beforeMessageId?: string; title?: string } = {}):
  Promise<ForkResult> {
  return client.request({
    path: `${base(conversationId)}/fork`, method: 'POST',
    body: { ...(options.beforeMessageId ? { before_message_id: options.beforeMessageId } : {}), ...(options.title ? { title: options.title } : {}) },
    parse: forkResult,
  })
}

export function renameConversation(client: ApiClient, conversationId: string, title: string): Promise<{ title: string }> {
  return client.request({
    path: `${base(conversationId)}/title`, method: 'PATCH', body: { title },
    parse: (value) => ({ title: str(rec(value).title, title) }),
  })
}

/** Per-message rewind of the conversation (a fork before the message) or of both the files and the conversation. */
export function rewindMessage(client: ApiClient, conversationId: string, messageId: string, mode: 'conversation' | 'both',
  forceUnbacked: readonly string[] = [], intent = client.createMutationIntent()):
  Promise<{ fork: ForkResult; restored: string[]; deleted: string[]; unbacked: string[] }> {
  return client.request({
    path: `${base(conversationId)}/messages/${encodeURIComponent(messageId)}/rewind`, method: 'POST', intent,
    body: rewindBody(mode, mode === 'both' ? forceUnbacked : []),
    parse: (value) => {
      const data = rec(value)
      return { fork: forkResult(data.fork), restored: strings(data.restored), deleted: strings(data.deleted), unbacked: strings(data.unbacked) }
    },
  })
}

export type BtwUsage = {
  inputTokens: number | null
  outputTokens: number | null
  cachedInputTokens: number | null
}

export type BtwStreamEvent =
  | {
      type: 'start'
      btwId: string
      provider: string
      modelId: string
      effort: string | null
      activeTurnIncluded: boolean
      contextMessages: number
    }
  | { type: 'waiting'; position: number | null; limit: number | null; reason: string | null }
  | { type: 'thinking'; delta: string }
  | { type: 'delta'; delta: string }
  | {
      type: 'done'
      btwId: string
      text: string
      durationMs: number
      provider: string
      modelId: string
      effort: string | null
      usage: BtwUsage
    }
  | { type: 'error'; code: string; message: string; retryable: boolean }
  | { type: 'heartbeat' }

function optNum(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function parseBtwSseBlock(block: string): BtwStreamEvent | null {
  let eventName = 'message'
  const dataLines: string[] = []
  for (const rawLine of block.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(':')) continue
    if (rawLine.startsWith('event:')) {
      eventName = rawLine.slice(6).trim()
    } else if (rawLine.startsWith('data:')) {
      dataLines.push(rawLine.slice(5).trimStart())
    }
  }
  if (dataLines.length === 0) return null
  let parsed: Record<string, unknown>
  try {
    parsed = rec(JSON.parse(dataLines.join('\n')))
  } catch {
    return null
  }
  switch (eventName) {
    case 'start':
      return {
        type: 'start',
        btwId: str(parsed.btw_id),
        provider: str(parsed.provider),
        modelId: str(parsed.model_id),
        effort: optStr(parsed.effort),
        activeTurnIncluded: bool(parsed.active_turn_included),
        contextMessages: num(parsed.context_messages),
      }
    case 'waiting':
      return {
        type: 'waiting',
        position: optNum(parsed.position),
        limit: optNum(parsed.limit),
        reason: optStr(parsed.reason),
      }
    case 'thinking':
      return { type: 'thinking', delta: str(parsed.delta) }
    case 'delta':
      return { type: 'delta', delta: str(parsed.delta) }
    case 'done': {
      const usage = rec(parsed.usage)
      return {
        type: 'done',
        btwId: str(parsed.btw_id),
        text: str(parsed.text),
        durationMs: num(parsed.duration_ms),
        provider: str(parsed.provider),
        modelId: str(parsed.model_id),
        effort: optStr(parsed.effort),
        usage: {
          inputTokens: optNum(usage.input_tokens),
          outputTokens: optNum(usage.output_tokens),
          cachedInputTokens: optNum(usage.cached_input_tokens),
        },
      }
    }
    case 'error':
      return {
        type: 'error',
        code: str(parsed.code, 'btw_error'),
        message: str(parsed.message, 'Não foi possível responder à pergunta paralela.'),
        retryable: typeof parsed.retryable === 'boolean' ? parsed.retryable : true,
      }
    case 'heartbeat':
      return { type: 'heartbeat' }
    default:
      return null
  }
}

/** `/btw`: ephemeral parallel side-question streamed over SSE without writing to the conversation history. */
export async function streamBtwQuestion(
  client: ApiClient,
  conversationId: string,
  options: {
    question: string
    provider?: string | null
    modelId?: string | null
    signal?: AbortSignal
    onEvent: (event: BtwStreamEvent) => void
  },
): Promise<void> {
  const body: Record<string, unknown> = { question: options.question }
  if (options.provider) body.provider = options.provider
  if (options.modelId) body.model_id = options.modelId

  const response = await client.stream({
    path: `${base(conversationId)}/btw`,
    method: 'POST',
    body,
    signal: options.signal,
  })
  if (!response.ok) {
    throw await parseApiErrorResponse(response)
  }

  const dispatchBuffer = (raw: string): string => {
    const parts = raw.split(/\r?\n\r?\n/)
    const remainder = parts.pop() ?? ''
    for (const block of parts) {
      if (!block.trim()) continue
      const parsed = parseBtwSseBlock(block)
      if (parsed) options.onEvent(parsed)
    }
    return remainder
  }

  if (!response.body || typeof response.body.getReader !== 'function') {
    const text = await response.text()
    const tail = dispatchBuffer(text)
    if (tail.trim()) {
      const parsed = parseBtwSseBlock(tail)
      if (parsed) options.onEvent(parsed)
    }
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      if (value) {
        buffer += decoder.decode(value, { stream: true })
        buffer = dispatchBuffer(buffer)
      }
    }
    buffer += decoder.decode()
    if (buffer.trim()) {
      const parsed = parseBtwSseBlock(buffer)
      if (parsed) options.onEvent(parsed)
    }
  } finally {
    try {
      reader.releaseLock()
    } catch {
      // Reader already released.
    }
  }
}
