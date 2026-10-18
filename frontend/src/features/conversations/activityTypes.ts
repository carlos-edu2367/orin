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
  old_start?: number
  old_lines?: number
  new_start?: number
  new_lines?: number
  lines: string[]
}

export type ActivityDiffHunk = {
  old_start: number
  old_lines: number
  new_start: number
  new_lines: number
  oldStart: number
  oldLines: number
  newStart: number
  newLines: number
  lines: string[]
}

export type ActivityDiff = {
  path: string
  added_lines: number
  removed_lines: number
  structured_patch: ActivityDiffHunk[]
  summary?: string
}

export type ActivityBashOutput = {
  command?: string
  description?: string
  stdout?: string
  stderr?: string
  exit_code?: number | null
  duration_ms?: number
  background?: boolean
  auto_backgrounded?: boolean
  shell_id?: string
  detected_url?: string
}

export type ActivityPlanApproval = {
  plan_path?: string
  plan_content?: string
  allowed_prompts?: Array<{ tool?: string; prompt?: string }>
}

export type TodoItem = {
  id?: string
  content: string
  status: 'pending' | 'in_progress' | 'completed'
  activeForm?: string
  active_form?: string
}

export type AgentActivityEventType =
  | 'turn.started'
  | 'turn.completed'
  | 'turn.failed'
  | 'turn.cancelled'
  | 'turn.waiting_user'
  | 'assistant.delta'
  | 'assistant.completed'
  | 'agent.thought_delta'
  | 'agent.created'
  | 'agent.message_sent'
  | 'agent.message_received'
  | 'delegation.created'
  | 'delegation.waiting'
  | 'delegation.completed'
  | 'delegation.failed'
  | 'tool.requested'
  | 'tool.started'
  | 'tool.progressed'
  | 'tool.finished'
  | 'tool.completed'
  | 'tool.failed'
  | 'tool.permission_required'
  | 'artifact.created'
  | 'context.updated'
  | 'memory.learned'
  | 'workspace.trust_required'
  | 'todo.updated'
  | 'code_mode.activated'
  | 'code_mode.started'
  | 'code_mode.plan_ready'
  | 'code_mode.validation_started'
  | 'code_mode.completed'
  | 'code_mode.completed_with_caveats'
  | 'code_mode.blocked'
  | 'code_mode.file_edited'
  | 'code_mode.command_batch'
  | 'code_mode.decision_required'
  | (string & {})

export type ConversationActivityEvent = {
  eventId: string
  cursor: string
  type: AgentActivityEventType
  kind: ActivityKind
  state: ActivityState
  agentId: string
  agentName?: string
  parentAgentId?: string | null
  turnId?: string
  messageId?: string
  tool?: string
  toolName?: string
  toolKind?: string
  command?: string
  invocationId?: string
  status?: string
  label?: string
  title?: string
  detail?: string
  delta?: string
  phase?: string
  bytesReceived?: number
  payload?: Record<string, unknown>
  path?: string
  /** Normalized file path for file/plan tools. */
  filePath?: string
  /** Optional human-readable description (e.g., bash description). */
  description?: string
  /** True when edit_file / write_file produced no diff changes. */
  unchanged?: boolean
  /** Private workspace image created from a browser observation. */
  screenshotPath?: string
  summary: string
  /** Streamed assistant text, or the preview of an agent-to-agent message. */
  content?: string
  errorCode?: string | null
  questions?: UserQuestion[]
  mcpApproval?: McpApprovalRequest
  pluginApproval?: PluginApprovalRequest
  codeSessionProposal?: CodeSessionProposalRequest
  occurredAt?: string
  contextUsage?: ContextUsage
  codeStage?: string
  codeApproval?: boolean
  /** Set only on `memory.learned`: what the agent stored, and where. */
  memoryId?: string
  memoryScope?: 'user' | 'project'
  /** Set only on `workspace.trust_required`: what stays disabled until the folder is trusted. */
  workspaceTrust?: { path: string; items: string[] }
  memoryProjectId?: string
  /** Structured unified diff hunks emitted by write_file / edit_file. */
  structuredPatch?: StructuredPatchHunk[]
  addedLines?: number
  removedLines?: number
  /** Structured diff output for write_file / edit_file. */
  diff?: ActivityDiff
  /** Structured terminal output for bash / run_command. */
  bashOutput?: ActivityBashOutput
  /** Structured plan approval metadata emitted on exit_plan_mode / plan_approval. */
  planApproval?: ActivityPlanApproval
  planPath?: string
  planContent?: string
  autoBackgrounded?: boolean
  detectedUrl?: string
  shellId?: string
  exitCode?: number | null
  /** Interactive tool permission request emitted in Code Mode. */
  toolPermission?: ToolPermissionRequest
  /** Live terminal stdout/stderr tail emitted by `tool.progressed`. */
  outputTail?: string
  elapsedMs?: number
  /** Live task list emitted by `todo_write`. */
  todos?: TodoItem[]
}

export type ActivityEvent = ConversationActivityEvent
export type ActivityToolCall = ConversationActivityEvent

export type ToolPermissionDecision = 'allow_once' | 'allow_always' | 'deny'

export type ToolPermissionRequest = {
  requestId: string
  turnId?: string
  toolName: string
  ruleSuggestion?: string
  command?: string
  path?: string
  target?: string
  reason?: string
  state?: 'pending' | 'allowed' | 'denied'
  structuredPatch?: StructuredPatchHunk[]
  addedLines?: number
  removedLines?: number
  planApproval?: ActivityPlanApproval
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

export type CodeSessionProposalRequest = {
  proposal_id: string
  title: string
  prompt: string
  workspace_path: string | null
  provider: string | null
  model_id: string | null
  permission_mode: 'accept_edits' | 'plan_mode' | 'ask_permissions'
  project_id: string | null
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
    estimated_cost_usd?: number | null
    usage_reported: boolean
  }
}

export type AgentEdge = {
  id: string
  from: string
  to: string
  fact: 'delegation' | 'message' | 'result'
}

export const TOOL_KINDS = ['filesystem', 'terminal', 'web', 'browser', 'memory', 'agent', 'artifact', 'media', 'user_input'] as const
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
  if (type === 'turn.completed' || type === 'TURN_COMPLETED') return 'completed'
  if (
    type === 'turn.cancelled' ||
    type === 'execution.canceled' ||
    type === 'TURN_CANCELLED' ||
    type === 'EXECUTION_CANCELED'
  ) {
    return 'cancelled'
  }
  if (type === 'turn.failed' || type === 'TURN_FAILED' || type === 'delegation.failed') {
    return errorCode === 'TURN_CANCELLED' || status === 'cancelled' ? 'cancelled' : 'failed'
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
  if (type === 'memory.learned' || type === 'todo.updated' || type === 'TODO_UPDATED') return 'completed'
  if (type === 'turn.started' || type === 'TURN_STARTED') return 'working'
  return 'working'
}

export type TurnActivityState = {
  events: ConversationActivityEvent[]
  cursor: string
  connection: 'snapshot' | 'connecting' | 'live' | 'degraded' | 'resyncing'
  resyncRequired: boolean
  todos: TodoItem[]
  activePhase: string | null
  status: 'idle' | 'running' | 'completed' | 'failed' | 'cancelled'
}

