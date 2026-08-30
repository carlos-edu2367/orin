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
  if (type === 'turn.waiting_user') return 'waiting_user'
  if (type === 'tool.requested' || type === 'tool.started') return 'waiting_tool'
  if (type === 'tool.finished') {
    if (status === 'cancelled') return 'cancelled'
    return status && status !== 'succeeded' ? 'failed' : 'completed'
  }
  if (type === 'agent.message_sent' || type === 'delegation.waiting') return 'waiting_agent'
  if (type === 'agent.created' || type === 'agent.message_received' || type === 'delegation.completed') return 'completed'
  if (type === 'turn.started') return 'working'
  return 'working'
}
