/**
 * Typed client for pipelines, pipeline runs and `/goal` (0.13 Package C, WS-C10 API, consumed by WS-C11).
 *
 * Nothing here carries the output of a model except `getStepOutput`, which reads one artifact the person asked for.
 * Every reader is tolerant: an unknown field is dropped, a missing one gets a neutral default, and no number is
 * invented (a cost the server did not know stays `null`, it is never shown as zero).
 */
import type { ApiClient } from './client'
import { ApiError } from './errors'
import { bool, list, num, optStr, rec, str } from './parse'

export type PipelineSource = 'builtin' | 'user' | 'project'
export type PipelineInputType = 'string' | 'integer' | 'boolean' | 'models'

export type PipelineIssue = {
  code: string
  message: string
  line: number | null
  column: number | null
  path: string | null
}

export type PipelineInputSpec = {
  name: string
  type: PipelineInputType
  required: boolean
  defaultValue: unknown
  maxLength: number | null
  /** False when the value is a whole gate command: only the person may set it (an agent never can). */
  agentSettable: boolean
  wholeCommand: boolean
}

export type PipelineStepInfo = { id: string; name: string; kind: 'agent' | 'gate' }

export type PipelineSummary = {
  id: string
  source: PipelineSource
  definitionHash: string
  valid: boolean
  errors: PipelineIssue[]
  warnings: PipelineIssue[]
  name: string
  description: string
  inputs: PipelineInputSpec[]
  steps: PipelineStepInfo[]
  approvalRequired: boolean
  approved: boolean
  agentApproved: boolean
  gateCommands: number
}

export type GateCommandReview = {
  stepId: string
  command: string
  expect: string
  timeoutSeconds: number | null
  inputs: string[]
  wholeValue: boolean
}

export type PipelineReview = {
  pipelineId: string
  definitionHash: string
  notice: string
  commands: GateCommandReview[]
}

export type PipelineList = {
  enabled: boolean
  pipelines: PipelineSummary[]
  shadowed: Array<{ id: string; source: PipelineSource; definitionHash: string }>
  /** Pipelines of the project the list left out because the folder is not trusted. */
  projectSkipped: number
  errors: PipelineIssue[]
}

export type PipelineDetail = PipelineSummary & {
  text: string
  review: PipelineReview | null
  contextErrors: PipelineIssue[]
  contextWarnings: PipelineIssue[]
}

export type PipelineValidation = {
  valid: boolean
  id: string | null
  errors: PipelineIssue[]
  warnings: PipelineIssue[]
}

export type EstimateStep = {
  id: string
  name: string
  kind: 'agent' | 'gate'
  agent: string | null
  provider: string | null
  model: string | null
  effort: string | null
  isolation: string | null
  map: boolean
  maxParallel: number
}

export type PipelineBudget = {
  maxTokens: number | null
  maxCostUsd: number | null
  maxMinutes: number | null
  isDefault: boolean
  loweredByUserCeiling: boolean
}

export type PipelineQuota = { available: boolean; warn: string[]; blocked: string[] }
export type DegradedNote = { reason: string; detail: string; effect: string }

export type PipelineEstimate = {
  pipelineId: string
  definitionHash: string
  source: string
  steps: EstimateStep[]
  subscriptions: Array<{ provider: string; model: string; steps: string[] }>
  maxParallel: number
  budget: PipelineBudget
  quota: PipelineQuota
  inputs: Record<string, unknown>
  degraded: DegradedNote[]
  permissionMode: string
  notices: string[]
}

export type PipelineRunState = 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted' | 'budget_exceeded'
export type PipelineStepState =
  | 'pending' | 'running' | 'succeeded' | 'failed' | 'stalled' | 'skipped' | 'interrupted' | 'gate_failed'

export type PipelineRunSummaryRow = {
  runId: string
  pipelineId: string
  source: string
  state: PipelineRunState
  stopReason: string | null
  conversationId: string
  turnId: string | null
  definitionHash: string
  inputs: Record<string, unknown>
  usedTokens: number
  usedCostUsd: number | null
  usedSeconds: number
  startedAt: string | null
  finishedAt: string | null
  resumable: boolean
  needsBudgetOverride: boolean
  liveHere: boolean
}

export type PipelineStepRow = {
  stepId: string
  itemKey: string
  attempt: number
  state: PipelineStepState
  agentId: string | null
  loopCount: number
  inputTokens: number
  outputTokens: number
  costUsd: number | null
  error: string | null
  hasOutput: boolean
  outputRef: string | null
  startedAt: string | null
  finishedAt: string | null
}

export type UsageRow = {
  key: string
  attempts: number
  inputTokens: number
  outputTokens: number
  totalTokens: number
  costUsd: number | null
  costKnown: boolean
  latestStates: Record<string, number>
}

export type PipelineRunDetail = PipelineRunSummaryRow & {
  steps: PipelineStepRow[]
  total: UsageRow | null
  byStep: UsageRow[]
  byAgent: UsageRow[]
  byProvider: UsageRow[]
  consistent: boolean
}

export type StepOutput = {
  stepId: string
  itemKey: string
  attempt: number
  state: string
  bytes: number
  /** The artifact, already parsed. `null` with `truncated` means it passed 256 KB: only `preview` is sent. */
  output: unknown
  truncated: boolean
  preview: string | null
}

export type StartedRun = {
  runId: string
  turnId: string
  pipelineId: string
  state: string
  inputs: Record<string, unknown>
  degraded: DegradedNote[]
  budget: PipelineBudget
  quota: PipelineQuota
}

export type GoalEvaluation = {
  turnIndex: number | null
  done: boolean | null
  reason: string
  model: string | null
  fallback: boolean
}

export type GoalState = 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted' | 'budget_exceeded'

export type GoalStatus = {
  runId: string
  state: GoalState
  stopReason: string | null
  objective: string
  maxTokens: number
  usedTokens: number
  windowUsedTokens: number
  remainingTokens: number
  turns: number
  windowTurns: number
  maxTurns: number
  lastEvaluation: GoalEvaluation | null
  evaluatorModel: string | null
  resumable: boolean
  startedAt: string | null
  finishedAt: string | null
}

export type ResumeOptions = { definition?: 'original' | 'new_run'; rerunGates?: boolean; budgetOverride?: boolean }

/** The two ways a resume can come back refused in a way the person decides about. */
export type DefinitionChangedInfo = {
  options: Array<{ id: 'original' | 'new_run'; label: string }>
  storedHash: string | null
  currentHash: string | null
}

const SOURCES: readonly PipelineSource[] = ['builtin', 'user', 'project']
const RUN_STATES: readonly PipelineRunState[] = ['running', 'succeeded', 'failed', 'cancelled', 'interrupted', 'budget_exceeded']
const STEP_STATES: readonly PipelineStepState[] = [
  'pending', 'running', 'succeeded', 'failed', 'stalled', 'skipped', 'interrupted', 'gate_failed',
]
const INPUT_TYPES: readonly PipelineInputType[] = ['string', 'integer', 'boolean', 'models']

function optNum(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value) ? (value as T) : fallback
}

function strList(value: unknown): string[] {
  return list(value, (item) => (typeof item === 'string' ? item : null), 200)
}

export function parseIssue(raw: unknown): PipelineIssue | null {
  const data = rec(raw)
  const message = str(data.message)
  const code = str(data.code)
  if (!message && !code) return null
  return { code, message, line: optNum(data.line), column: optNum(data.column), path: optStr(data.path) }
}

function issues(value: unknown): PipelineIssue[] {
  return list(value, parseIssue, 200)
}

function parseInput(raw: unknown): PipelineInputSpec | null {
  const data = rec(raw)
  const name = str(data.name)
  if (!name) return null
  return {
    name,
    type: oneOf(data.type, INPUT_TYPES, 'string'),
    required: bool(data.required),
    defaultValue: data.default ?? null,
    maxLength: optNum(data.max_length),
    agentSettable: data.agent_settable !== false,
    wholeCommand: bool(data.whole_command),
  }
}

function parseStepInfo(raw: unknown): PipelineStepInfo | null {
  const data = rec(raw)
  const id = str(data.id)
  if (!id) return null
  return { id, name: str(data.name) || id, kind: data.kind === 'gate' ? 'gate' : 'agent' }
}

function parseSummary(raw: unknown): PipelineSummary | null {
  const data = rec(raw)
  const id = str(data.id)
  if (!id) return null
  return {
    id,
    source: oneOf(data.source, SOURCES, 'user'),
    definitionHash: str(data.definition_hash),
    valid: bool(data.valid),
    errors: issues(data.errors),
    warnings: issues(data.warnings),
    name: str(data.name) || id,
    description: str(data.description),
    inputs: list(data.inputs, parseInput, 100),
    steps: list(data.steps, parseStepInfo, 200),
    approvalRequired: bool(data.approval_required),
    approved: bool(data.approved),
    agentApproved: bool(data.agent_approved),
    gateCommands: num(data.gate_commands),
  }
}

function parseReview(raw: unknown): PipelineReview | null {
  const data = rec(raw)
  if (!Object.keys(data).length) return null
  return {
    pipelineId: str(data.pipeline_id),
    definitionHash: str(data.definition_hash),
    notice: str(data.notice),
    commands: list(data.commands, (item) => {
      const command = rec(item)
      const text = str(command.command)
      if (!text) return null
      return {
        stepId: str(command.step_id),
        command: text,
        expect: str(command.expect),
        timeoutSeconds: optNum(command.timeout_seconds),
        inputs: strList(command.inputs),
        wholeValue: bool(command.whole_value),
      }
    }, 100),
  }
}

export function parsePipelineList(raw: unknown): PipelineList {
  const data = rec(raw)
  return {
    enabled: data.enabled !== false,
    pipelines: list(data.pipelines, parseSummary, 500),
    shadowed: list(data.shadowed, (item) => {
      const row = rec(item)
      const id = str(row.id)
      return id ? { id, source: oneOf(row.source, SOURCES, 'user'), definitionHash: str(row.definition_hash) } : null
    }, 500),
    projectSkipped: num(data.project_skipped),
    errors: issues(data.errors),
  }
}

export function parsePipelineDetail(raw: unknown): PipelineDetail {
  const data = rec(raw)
  const summary = parseSummary(data)
  if (!summary) throw new ApiError({ status: 0, code: 'invalid_response', category: 'INTERNAL' })
  const context = rec(data.context)
  return {
    ...summary,
    text: str(data.text),
    review: parseReview(data.review),
    contextErrors: issues(context.errors),
    contextWarnings: issues(context.warnings),
  }
}

function parseValidation(raw: unknown): PipelineValidation {
  const data = rec(raw)
  return { valid: bool(data.valid), id: optStr(data.id), errors: issues(data.errors), warnings: issues(data.warnings) }
}

function parseBudget(raw: unknown): PipelineBudget {
  const data = rec(raw)
  return {
    maxTokens: optNum(data.max_tokens),
    maxCostUsd: optNum(data.max_cost_usd),
    maxMinutes: optNum(data.max_minutes),
    isDefault: bool(data.is_default),
    loweredByUserCeiling: bool(data.lowered_by_user_ceiling),
  }
}

function parseQuota(raw: unknown): PipelineQuota {
  const data = rec(raw)
  return { available: bool(data.available), warn: strList(data.warn), blocked: strList(data.blocked) }
}

function parseDegraded(raw: unknown): DegradedNote | null {
  const data = rec(raw)
  const reason = str(data.reason)
  return reason ? { reason, detail: str(data.detail), effect: str(data.effect) } : null
}

export function parseEstimate(raw: unknown): PipelineEstimate {
  const data = rec(raw)
  return {
    pipelineId: str(data.pipeline_id),
    definitionHash: str(data.definition_hash),
    source: str(data.source),
    steps: list(data.steps, (item) => {
      const row = rec(item)
      const id = str(row.id)
      if (!id) return null
      return {
        id,
        name: str(row.name) || id,
        kind: row.kind === 'gate' ? 'gate' : 'agent',
        agent: optStr(row.agent),
        provider: optStr(row.provider),
        model: optStr(row.model),
        effort: optStr(row.effort),
        isolation: optStr(row.isolation),
        map: bool(row.map),
        maxParallel: Math.max(1, num(row.max_parallel, 1)),
      }
    }, 200),
    subscriptions: list(data.subscriptions, (item) => {
      const row = rec(item)
      const provider = str(row.provider)
      return provider ? { provider, model: str(row.model), steps: strList(row.steps) } : null
    }, 100),
    maxParallel: Math.max(1, num(data.max_parallel, 1)),
    budget: parseBudget(data.budget),
    quota: parseQuota(data.quota),
    inputs: rec(data.inputs),
    degraded: list(data.degraded, parseDegraded, 50),
    permissionMode: str(data.permission_mode),
    notices: strList(data.notices),
  }
}

function parseRunRow(raw: unknown): PipelineRunSummaryRow | null {
  const data = rec(raw)
  const runId = str(data.run_id)
  if (!runId) return null
  return {
    runId,
    pipelineId: str(data.pipeline_id),
    source: str(data.source),
    state: oneOf(data.state, RUN_STATES, 'running'),
    stopReason: optStr(data.stop_reason),
    conversationId: str(data.conversation_id),
    turnId: optStr(data.turn_id),
    definitionHash: str(data.definition_hash),
    inputs: rec(data.inputs),
    usedTokens: num(data.used_tokens),
    usedCostUsd: optNum(data.used_cost_usd),
    usedSeconds: num(data.used_seconds),
    startedAt: optStr(data.started_at),
    finishedAt: optStr(data.finished_at),
    resumable: bool(data.resumable),
    needsBudgetOverride: bool(data.needs_budget_override),
    liveHere: bool(data.live_here),
  }
}

function parseStepRow(raw: unknown): PipelineStepRow | null {
  const data = rec(raw)
  const stepId = str(data.step_id)
  if (!stepId) return null
  return {
    stepId,
    itemKey: str(data.item_key),
    attempt: Math.max(1, num(data.attempt, 1)),
    state: oneOf(data.state, STEP_STATES, 'pending'),
    agentId: optStr(data.agent_id),
    loopCount: Math.max(0, num(data.loop_count)),
    inputTokens: num(data.input_tokens),
    outputTokens: num(data.output_tokens),
    costUsd: optNum(data.cost_usd),
    error: optStr(data.error),
    hasOutput: bool(data.has_output),
    outputRef: optStr(data.output_ref),
    startedAt: optStr(data.started_at),
    finishedAt: optStr(data.finished_at),
  }
}

function parseUsageRow(raw: unknown): UsageRow | null {
  const data = rec(raw)
  const key = str(data.key)
  if (!key && !Object.keys(data).length) return null
  const input = num(data.input_tokens)
  const output = num(data.output_tokens)
  const latest: Record<string, number> = {}
  for (const [name, value] of Object.entries(rec(data.latest_states))) {
    if (typeof value === 'number') latest[name] = value
  }
  return {
    key,
    attempts: num(data.attempts),
    inputTokens: input,
    outputTokens: output,
    totalTokens: num(data.total_tokens, input + output),
    costUsd: optNum(data.cost_usd),
    costKnown: bool(data.cost_known),
    latestStates: latest,
  }
}

export function parseRunDetail(raw: unknown): PipelineRunDetail {
  const data = rec(raw)
  const row = parseRunRow(data)
  if (!row) throw new ApiError({ status: 0, code: 'invalid_response', category: 'INTERNAL' })
  const summary = rec(data.summary)
  return {
    ...row,
    steps: list(data.steps, parseStepRow, 5000),
    total: parseUsageRow(summary.total),
    byStep: list(summary.by_step, parseUsageRow, 500),
    byAgent: list(summary.by_agent, parseUsageRow, 500),
    byProvider: list(summary.by_provider, parseUsageRow, 50),
    consistent: summary.consistent !== false,
  }
}

function parseStarted(raw: unknown): StartedRun {
  const data = rec(raw)
  return {
    runId: str(data.run_id),
    turnId: str(data.turn_id),
    pipelineId: str(data.pipeline_id),
    state: str(data.state),
    inputs: rec(data.inputs),
    degraded: list(data.degraded, parseDegraded, 50),
    budget: parseBudget(data.budget),
    quota: parseQuota(data.quota),
  }
}

function parseStepOutput(raw: unknown): StepOutput {
  const data = rec(raw)
  return {
    stepId: str(data.step_id),
    itemKey: str(data.item_key),
    attempt: num(data.attempt, 1),
    state: str(data.state),
    bytes: num(data.bytes),
    output: data.output ?? null,
    truncated: bool(data.truncated),
    preview: optStr(data.preview),
  }
}

function parseGoal(raw: unknown): GoalStatus | null {
  const data = rec(raw)
  const runId = str(data.run_id)
  if (!runId) return null
  const last = rec(data.last_evaluation)
  return {
    runId,
    state: oneOf(data.state, RUN_STATES, 'running'),
    stopReason: optStr(data.stop_reason),
    objective: str(data.objective),
    maxTokens: num(data.max_tokens),
    usedTokens: num(data.used_tokens),
    windowUsedTokens: num(data.window_used_tokens),
    remainingTokens: num(data.remaining_tokens),
    turns: num(data.turns),
    windowTurns: num(data.window_turns),
    maxTurns: num(data.max_turns),
    lastEvaluation: Object.keys(last).length
      ? {
          turnIndex: optNum(last.turn_index),
          done: typeof last.done === 'boolean' ? last.done : null,
          reason: str(last.reason),
          model: optStr(last.model),
          fallback: bool(last.fallback),
        }
      : null,
    evaluatorModel: optStr(data.evaluator_model),
    resumable: bool(data.resumable),
    startedAt: optStr(data.started_at),
    finishedAt: optStr(data.finished_at),
  }
}

// ----------------------------------------------------------------------------------------------- pipelines

export function listPipelines(client: ApiClient, conversationId?: string | null, signal?: AbortSignal): Promise<PipelineList> {
  return client.request({ path: '/v1/pipelines', query: { conversation_id: conversationId ?? undefined }, signal, parse: parsePipelineList })
}

export function getPipeline(client: ApiClient, pipelineId: string, conversationId?: string | null): Promise<PipelineDetail> {
  return client.request({
    path: `/v1/pipelines/${encodeURIComponent(pipelineId)}`,
    query: { conversation_id: conversationId ?? undefined },
    parse: parsePipelineDetail,
  })
}

export function validatePipelineText(
  client: ApiClient,
  text: string,
  options: { source?: 'user' | 'project'; conversationId?: string | null; signal?: AbortSignal } = {},
): Promise<PipelineValidation> {
  return client.request({
    path: '/v1/pipelines/validate',
    method: 'POST',
    body: { text, source: options.source ?? 'user', ...(options.conversationId ? { conversation_id: options.conversationId } : {}) },
    signal: options.signal,
    parse: parseValidation,
  })
}

export function savePipeline(
  client: ApiClient,
  pipelineId: string,
  text: string,
  options: { scope?: 'user' | 'project'; conversationId?: string | null; ifMatch?: string | null } = {},
): Promise<PipelineDetail> {
  return client.request({
    path: `/v1/pipelines/${encodeURIComponent(pipelineId)}`,
    method: 'PUT',
    body: {
      text,
      scope: options.scope ?? 'user',
      ...(options.conversationId ? { conversation_id: options.conversationId } : {}),
      ...(options.ifMatch ? { if_match: options.ifMatch } : {}),
    },
    parse: parsePipelineDetail,
  })
}

export function approvePipeline(
  client: ApiClient,
  pipelineId: string,
  definitionHash: string,
  conversationId?: string | null,
): Promise<{ approved: boolean }> {
  return client.request({
    path: `/v1/pipelines/${encodeURIComponent(pipelineId)}/approve`,
    method: 'POST',
    body: { definition_hash: definitionHash, ...(conversationId ? { conversation_id: conversationId } : {}) },
    parse: (raw) => ({ approved: bool(rec(raw).approved) }),
  })
}

export function revokePipeline(
  client: ApiClient,
  pipelineId: string,
  definitionHash: string,
  conversationId?: string | null,
): Promise<{ approved: boolean }> {
  return client.request({
    path: `/v1/pipelines/${encodeURIComponent(pipelineId)}/revoke`,
    method: 'POST',
    body: { definition_hash: definitionHash, ...(conversationId ? { conversation_id: conversationId } : {}) },
    parse: (raw) => ({ approved: bool(rec(raw).approved) }),
  })
}

export function estimatePipeline(
  client: ApiClient,
  pipelineId: string,
  inputs: Record<string, unknown>,
  conversationId?: string | null,
  signal?: AbortSignal,
): Promise<PipelineEstimate> {
  return client.request({
    path: `/v1/pipelines/${encodeURIComponent(pipelineId)}/estimate`,
    method: 'POST',
    body: { inputs, ...(conversationId ? { conversation_id: conversationId } : {}) },
    signal,
    parse: parseEstimate,
  })
}

// --------------------------------------------------------------------------------------------------- runs

export function startPipelineRun(
  client: ApiClient,
  pipelineId: string,
  conversationId: string,
  inputs: Record<string, unknown>,
): Promise<StartedRun> {
  return client.request({
    path: '/v1/pipeline-runs',
    method: 'POST',
    body: { pipeline_id: pipelineId, conversation_id: conversationId, inputs },
    parse: parseStarted,
  })
}

export function listPipelineRuns(
  client: ApiClient,
  options: { conversationId?: string | null; state?: string; limit?: number; signal?: AbortSignal } = {},
): Promise<PipelineRunSummaryRow[]> {
  return client.request({
    path: '/v1/pipeline-runs',
    query: { conversation_id: options.conversationId ?? undefined, state: options.state, limit: options.limit },
    signal: options.signal,
    parse: (raw) => list(rec(raw).runs, parseRunRow, 200),
  })
}

export function getPipelineRun(client: ApiClient, runId: string, signal?: AbortSignal): Promise<PipelineRunDetail> {
  return client.request({ path: `/v1/pipeline-runs/${encodeURIComponent(runId)}`, signal, parse: parseRunDetail })
}

export function getStepOutput(
  client: ApiClient,
  runId: string,
  stepId: string,
  options: { itemKey?: string; attempt?: number } = {},
): Promise<StepOutput> {
  return client.request({
    path: `/v1/pipeline-runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(stepId)}/output`,
    query: { item_key: options.itemKey || undefined, attempt: options.attempt },
    parse: parseStepOutput,
  })
}

const ack = (raw: unknown): Record<string, unknown> => rec(raw)

export function pausePipelineRun(client: ApiClient, runId: string): Promise<Record<string, unknown>> {
  return client.request({ path: `/v1/pipeline-runs/${encodeURIComponent(runId)}/pause`, method: 'POST', parse: ack })
}

export function cancelPipelineRun(client: ApiClient, runId: string): Promise<Record<string, unknown>> {
  return client.request({ path: `/v1/pipeline-runs/${encodeURIComponent(runId)}/cancel`, method: 'POST', parse: ack })
}

export function resumePipelineRun(client: ApiClient, runId: string, options: ResumeOptions = {}): Promise<Record<string, unknown>> {
  return client.request({
    path: `/v1/pipeline-runs/${encodeURIComponent(runId)}/resume`,
    method: 'POST',
    body: {
      ...(options.definition ? { definition: options.definition } : {}),
      rerun_gates: options.rerunGates === true,
      budget_override: options.budgetOverride === true,
    },
    parse: ack,
  })
}

/** The "definition changed" refusal of a resume (409 `PIPELINE_DEFINITION_CHANGED`), or null for any other error. */
export function definitionChangedOf(error: unknown): DefinitionChangedInfo | null {
  if (!(error instanceof ApiError) || error.code !== 'PIPELINE_DEFINITION_CHANGED') return null
  const options = list<DefinitionChangedInfo['options'][number]>(error.details.options, (item) => {
    const row = rec(item)
    const id = row.id === 'original' || row.id === 'new_run' ? row.id : null
    return id ? { id, label: str(row.label) } : null
  }, 5)
  return { options, storedHash: optStr(error.details.stored_hash), currentHash: optStr(error.details.current_hash) }
}

/** The review a refused start carries (409 `PIPELINE_NOT_APPROVED`), or null. */
export function reviewOf(error: unknown): PipelineReview | null {
  return error instanceof ApiError && error.code === 'PIPELINE_NOT_APPROVED' ? parseReview(error.details.review) : null
}

/** The `line`/`column` problems of a refused save (422 `PIPELINE_INVALID`), or an empty list. */
export function issuesOf(error: unknown): PipelineIssue[] {
  return error instanceof ApiError ? issues(error.details.errors) : []
}

// ------------------------------------------------------------------------------------------------- /goal

export function getGoal(client: ApiClient, conversationId: string, signal?: AbortSignal): Promise<GoalStatus | null> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/goal`,
    signal,
    parse: (raw) => parseGoal(rec(raw).goal),
  })
}

export function startGoal(
  client: ApiClient,
  conversationId: string,
  objective: string,
  maxTokens?: number | null,
): Promise<{ runId: string; turnId: string; maxTokens: number }> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/goal`,
    method: 'POST',
    body: { objective, ...(maxTokens ? { max_tokens: maxTokens } : {}) },
    parse: (raw) => {
      const data = rec(raw)
      return { runId: str(data.run_id), turnId: str(data.turn_id), maxTokens: num(data.max_tokens) }
    },
  })
}

export function clearGoal(client: ApiClient, conversationId: string, cancelTurn = false): Promise<GoalStatus | null> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/goal/clear`,
    method: 'POST',
    body: { cancel_turn: cancelTurn },
    parse: (raw) => parseGoal(rec(raw).goal),
  })
}

export function resumeGoal(client: ApiClient, conversationId: string, maxTokens?: number | null): Promise<GoalStatus | null> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/goal/resume`,
    method: 'POST',
    body: maxTokens ? { max_tokens: maxTokens } : {},
    parse: (raw) => parseGoal(rec(raw).goal),
  })
}
