/**
 * The public activity contract as the backend emits it.
 *
 * The server already bounds and redacts every payload, so the client's job is to
 * normalize the event into something the UI can group and label, not to re-derive
 * a policy. Unknown event types stay renderable as a plain lifecycle line rather
 * than failing the whole stream.
 */

export const activityStates = [
  'queued',
  'working',
  'waiting_tool',
  'waiting_agent',
  'waiting_user',
  'retrying',
  'failed',
  'cancelled',
  'completed',
  'unknown',
] as const

export type ActivityState = (typeof activityStates)[number]

/** Drives the icon, grouping rule and copy for an activity card. */
export type ActivityKind = 'lifecycle' | 'message' | 'tool' | 'agent' | 'artifact'

export type StructuredPatchHunk = {
  oldStart: number
  oldLines: number
  newStart: number
  newLines: number
  lines: string[]
}

export type TodoItem = {
  id: string
  content: string
  status: 'pending' | 'in_progress' | 'completed'
  activeForm: string
}

export type ConversationActivityEvent = {
  eventId: string
  cursor: string
  type: string
  kind: ActivityKind
  state: ActivityState
  agentId: string
  agentName?: string
  parentAgentId?: string | null
  turnId?: string
  messageId?: string
  toolName?: string
  toolKind?: string
  invocationId?: string
  status?: string
  label?: string
  path?: string
  /** Private workspace image created from a browser observation. */
  screenshotPath?: string
  summary: string
  /** Streamed assistant text, or the preview of an agent-to-agent message. */
  content?: string
  errorCode?: string | null
  questions?: UserQuestion[]
  mcpApproval?: McpApprovalRequest
  pluginApproval?: PluginApprovalRequest
  occurredAt?: string
  contextUsage?: ContextUsage
  codeStage?: string
  codeApproval?: boolean
  /** Set only on `memory.learned`: what the agent stored, and where. */
  memoryId?: string
  memoryScope?: 'user' | 'project'
  memoryProjectId?: string
  /** Structured unified diff hunks emitted by write_file / edit_file. */
  structuredPatch?: StructuredPatchHunk[]
  addedLines?: number
  removedLines?: number
  /** Interactive tool permission request emitted in Code Mode. */
  toolPermission?: ToolPermissionRequest
  /** Live terminal stdout/stderr tail emitted by `tool.progressed`. */
  outputTail?: string
  elapsedMs?: number
  /** Live task list emitted by `todo_write`. */
  todos?: TodoItem[]
}

export type ToolPermissionDecision = 'allow_once' | 'allow_always' | 'deny'

export type ToolPermissionRequest = {
  requestId: string
  turnId?: string
  toolName: string
  ruleSuggestion?: string
  command?: string
  path?: string
  structuredPatch?: StructuredPatchHunk[]
  addedLines?: number
  removedLines?: number
}

export type ContextUsage = {
  used_tokens: number
  limit_tokens: number
  percentage: number
  system_prompt_tokens: number
  history_tokens: number
  input_tokens: number
  tools_tokens: number
  skills_tokens: number
  mcps_tokens: number
  omitted_messages: number
  compaction_count: number
  compaction_enabled: boolean
}

export type UserQuestionMode = 'checkbox' | 'single_choice' | 'text'
export type UserQuestionOption = { id: string; label: string }
export type UserQuestion = { id: string; question: string; mode: UserQuestionMode; options: UserQuestionOption[]; placeholder?: string }

export type McpApprovalRequest = {
  server_id: string
  display_name: string
  transport: string
  secret_names: string[]
  catalog_id: string | null
}
export type PluginApprovalRequest = {
  plugin_id: string
  version: string
  display_name: string
  description: string
  author: string
  warnings: string[]
  skills: Array<{ skill_id: string; name: string; description?: string }>
  mcp_servers: Array<{ slug: string; display_name: string; transport: string; secret_names?: string[] }>
  agents: Array<{ agent_id: string; name: string; role?: string }>
  commands: Array<{ command_id: string; slug: string; description?: string }>
  hooks: Array<{ hook_id: string; event: string; matcher: string; command: string; timeout_seconds: number }>
  contribution_count: number
}

/** One rendered row in the chat: a single event or a collapsed run of them. */
export type ActivityGroup = {
  id: string
  kind: ActivityKind
  state: ActivityState
  label: string
  count: number
  events: ConversationActivityEvent[]
  agentId: string
  agentName?: string
  failed: boolean
}

export type AgentNode = {
  agentId: string
  name: string
  role?: string
  parentAgentId?: string | null
  state: ActivityState
  provider?: string
  modelId?: string
  tokenUsage?: {
    input_tokens: number | null
    output_tokens: number | null
    total_tokens: number | null
    usage_reported: boolean
  }
}

export type AgentEdge = {
  id: string
  from: string
  to: string
  fact: 'delegation' | 'message' | 'result'
}

export const TOOL_KINDS = ['filesystem', 'terminal', 'web', 'browser', 'memory', 'agent', 'artifact', 'user_input'] as const
export type ToolKind = (typeof TOOL_KINDS)[number]

const AGENT_TYPES = new Set([
  'agent.created',
  'agent.message_sent',
  'agent.message_received',
  'delegation.created',
  'delegation.waiting',
  'delegation.completed',
  'delegation.failed',
])

export function kindFor(type: string, toolKind?: string): ActivityKind {
  if (type === 'assistant.delta' || type === 'assistant.completed') return 'message'
  if (AGENT_TYPES.has(type)) return 'agent'
  if (type === 'artifact.created') return 'artifact'
  if (type === 'tool.permission_required') return 'tool'
  if (type.startsWith('tool.')) return toolKind === 'agent' ? 'agent' : 'tool'
  if (type.startsWith('browser.') || type.startsWith('terminal.') || type.startsWith('filesystem.')) return 'tool'
  return 'lifecycle'
}

export function stateFor(type: string, status?: string, errorCode?: string | null): ActivityState {
  if (type === 'code_mode.completed_with_caveats') return 'completed'
  if (type === 'code_mode.blocked') return 'failed'
  if (type === 'turn.completed') return 'completed'
  if (type === 'turn.failed' || type === 'delegation.failed') {
    return errorCode === 'TURN_CANCELLED' ? 'cancelled' : 'failed'
  }
  if (type === 'turn.waiting_user' || type === 'tool.permission_required') return 'waiting_user'
  if (type === 'tool.requested' || type === 'tool.started' || type === 'tool.progressed') return 'waiting_tool'
  if (type === 'tool.finished') {
    if (status === 'cancelled') return 'cancelled'
    return status && status !== 'succeeded' ? 'failed' : 'completed'
  }
  if (type === 'agent.message_sent' || type === 'delegation.waiting') return 'waiting_agent'
  if (type === 'agent.created' || type === 'agent.message_received' || type === 'delegation.completed') return 'completed'
  // Only ever emitted after its tool call already succeeded (see the backend
  // ARTIFACT_CREATED emission), so there is no "in progress" state for it to
  // pass through.
  if (type === 'artifact.created') return 'completed'
  if (type === 'memory.learned') return 'completed'
  if (type === 'turn.started') return 'working'
  return 'working'
}
