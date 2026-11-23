import type { ConversationActivityEvent } from './activityTypes'

/**
 * The delegation tree of a turn's subagents (a subagent may start its own). Items come back depth-first — each
 * parent followed by the agents it started — with their depth: 0 for an agent the main agent started.
 * An item whose parent is not in the list (the main agent, or an agent not shown) is a root.
 */
export function orderAgentTree<T>(
  items: readonly T[],
  idOf: (item: T) => string,
  parentOf: (item: T) => string | null | undefined,
): Array<{ item: T; depth: number }> {
  const ids = new Set(items.map(idOf))
  const children = new Map<string, T[]>()
  const roots: T[] = []
  for (const item of items) {
    const parent = parentOf(item)
    if (parent && parent !== idOf(item) && ids.has(parent)) {
      const siblings = children.get(parent) ?? []
      siblings.push(item)
      children.set(parent, siblings)
    } else {
      roots.push(item)
    }
  }
  const ordered: Array<{ item: T; depth: number }> = []
  const visited = new Set<string>()
  const visit = (item: T, depth: number) => {
    const id = idOf(item)
    if (visited.has(id)) return
    visited.add(id)
    ordered.push({ item, depth })
    for (const child of children.get(id) ?? []) visit(child, depth + 1)
  }
  for (const root of roots) visit(root, 0)
  // A cycle in bad data has no root: its agents still show, at the top level.
  for (const item of items) visit(item, 0)
  return ordered
}

export type AgentRunSection<T> = { runId: string | null; entries: Array<{ item: T; depth: number }> }

/**
 * 0.13 WS-C11: the children of a pipeline run are shown together, under the run, in the order the tree already gives
 * (a parent before the agents it started). `ordered` comes from `orderAgentTree`; an agent that is not part of a run
 * stays in the `null` section, which keeps its own relative order and comes first.
 */
export function groupAgentTreeByPipelineRun<T>(
  ordered: ReadonlyArray<{ item: T; depth: number }>,
  runOf: (item: T) => string | null | undefined,
): Array<AgentRunSection<T>> {
  const plain: AgentRunSection<T> = { runId: null, entries: [] }
  const runs = new Map<string, AgentRunSection<T>>()
  for (const entry of ordered) {
    const runId = runOf(entry.item)
    if (!runId) {
      plain.entries.push(entry)
      continue
    }
    const section = runs.get(runId) ?? { runId, entries: [] }
    section.entries.push(entry)
    runs.set(runId, section)
  }
  return [...(plain.entries.length ? [plain] : []), ...runs.values()]
}

export type AgentRunState =
  | 'queued'
  | 'running'
  | 'stalled'
  | 'interrupted'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type AgentMergeState =
  | 'pending'
  | 'applied'
  | 'conflict'
  | 'check_failed'
  | 'discarded'
  | 'empty'

export type AgentProgressData = {
  agentId: string
  state: AgentRunState
  stalled: boolean
  interrupted: boolean
  elapsedSeconds: number
  lastActivityAt: string | null
  currentTool: string | null
  toolCalls: number
  inputTokens: number
  outputTokens: number
  costUsd: number | null
  mergeState: AgentMergeState | null
  parentAgentId: string | null
  resultRef: string | null
}

export type AgentProgressMap = Record<string, AgentProgressData>

export type AgentRunSummary = {
  agent_id: string
  parent_agent_id?: string | null
  state: string
  elapsed_seconds?: number | null
  last_activity_at?: string | null
  current_tool?: string | null
  tool_calls?: number | null
  input_tokens?: number | null
  output_tokens?: number | null
  cost_usd?: number | null
  stalled?: boolean | null
  merge_state?: string | null
  result_ref?: string | null
}

function normalizeRunState(raw: unknown, fallback: AgentRunState = 'running'): AgentRunState {
  const s = typeof raw === 'string' ? raw.trim().toLowerCase() : ''
  switch (s) {
    case 'queued':
    case 'running':
    case 'stalled':
    case 'interrupted':
    case 'completed':
    case 'failed':
    case 'cancelled':
      return s
    case 'working':
      return 'running'
    case 'stopped':
      return 'cancelled'
    default:
      return fallback
  }
}

function normalizeMergeState(raw: unknown): AgentMergeState | null {
  const s = typeof raw === 'string' ? raw.trim().toLowerCase() : ''
  switch (s) {
    case 'pending':
    case 'applied':
    case 'conflict':
    case 'check_failed':
    case 'discarded':
    case 'empty':
      return s
    default:
      return null
  }
}

function numberOr(raw: unknown, fallback: number): number {
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : fallback
}

function nullableNumberOr(raw: unknown, fallback: number | null): number | null {
  if (raw === null) return null
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : fallback
}

function defaultProgress(agentId: string): AgentProgressData {
  return {
    agentId,
    state: 'running',
    stalled: false,
    interrupted: false,
    elapsedSeconds: 0,
    lastActivityAt: null,
    currentTool: null,
    toolCalls: 0,
    inputTokens: 0,
    outputTokens: 0,
    costUsd: null,
    mergeState: null,
    parentAgentId: null,
    resultRef: null,
  }
}

export function mergeAgentProgress(
  existing: AgentProgressData | undefined,
  patch: Partial<AgentProgressData> & { agentId: string },
): AgentProgressData {
  const base = existing ?? defaultProgress(patch.agentId)
  const nextState = patch.state ?? base.state
  const nextStalled = patch.stalled !== undefined ? patch.stalled : nextState === 'stalled'
  const nextInterrupted = patch.interrupted !== undefined ? patch.interrupted : nextState === 'interrupted'
  return {
    agentId: patch.agentId,
    state: nextState,
    stalled: nextStalled,
    interrupted: nextInterrupted,
    elapsedSeconds: patch.elapsedSeconds !== undefined ? Math.max(0, patch.elapsedSeconds) : base.elapsedSeconds,
    lastActivityAt: patch.lastActivityAt !== undefined ? patch.lastActivityAt : base.lastActivityAt,
    currentTool: patch.currentTool !== undefined ? patch.currentTool : base.currentTool,
    toolCalls: patch.toolCalls !== undefined ? Math.max(0, patch.toolCalls) : base.toolCalls,
    inputTokens: patch.inputTokens !== undefined ? Math.max(0, patch.inputTokens) : base.inputTokens,
    outputTokens: patch.outputTokens !== undefined ? Math.max(0, patch.outputTokens) : base.outputTokens,
    costUsd: patch.costUsd !== undefined ? patch.costUsd : base.costUsd,
    mergeState: patch.mergeState !== undefined ? patch.mergeState : base.mergeState,
    parentAgentId: patch.parentAgentId !== undefined ? patch.parentAgentId : base.parentAgentId,
    resultRef: patch.resultRef !== undefined ? patch.resultRef : base.resultRef,
  }
}

const AUTHORITATIVE_NON_RUNNING_STATES: ReadonlySet<AgentRunState> = new Set([
  'interrupted',
  'completed',
  'failed',
  'cancelled',
])

function timestampMs(iso: string | null | undefined): number | null {
  if (!iso) return null
  const parsed = Date.parse(iso)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * Reconcile authoritative polled ``agent_runs`` state with historical/live activity events (RT-B-08).
 * Historical ``running`` or ``worktree.created`` events must never downgrade an authoritative
 * ``interrupted`` / ``stalled`` / terminal state returned by ``listAgentRuns``.
 */
export function reconcileAgentProgress(
  authoritativeRun: AgentProgressData | undefined,
  liveEventProgress: AgentProgressData | undefined,
): AgentProgressData | undefined {
  if (!authoritativeRun) return liveEventProgress
  if (!liveEventProgress) return authoritativeRun

  const runTs = timestampMs(authoritativeRun.lastActivityAt)
  const liveTs = timestampMs(liveEventProgress.lastActivityAt)
  const liveIsStrictlyNewer = liveTs !== null && (runTs === null || liveTs > runTs)

  let state: AgentRunState = authoritativeRun.state
  let stalled = authoritativeRun.stalled
  let interrupted = authoritativeRun.interrupted
  let currentTool: string | null = null

  if (AUTHORITATIVE_NON_RUNNING_STATES.has(authoritativeRun.state)) {
    // Authoritative terminal or interrupted state from agent_runs always wins over historical events.
    state = authoritativeRun.state
    stalled = authoritativeRun.stalled
    interrupted = authoritativeRun.interrupted
  } else if (authoritativeRun.state === 'stalled' || authoritativeRun.stalled) {
    if (liveIsStrictlyNewer && liveEventProgress.state === 'running' && !liveEventProgress.stalled) {
      state = 'running'
      stalled = false
      interrupted = false
      currentTool = liveEventProgress.currentTool
    } else if (liveIsStrictlyNewer && AUTHORITATIVE_NON_RUNNING_STATES.has(liveEventProgress.state)) {
      state = liveEventProgress.state
      stalled = liveEventProgress.stalled
      interrupted = liveEventProgress.interrupted
    } else {
      state = 'stalled'
      stalled = true
      interrupted = false
      currentTool = liveEventProgress.currentTool ?? authoritativeRun.currentTool
    }
  } else if (authoritativeRun.state === 'queued') {
    if (liveIsStrictlyNewer && liveEventProgress.state !== 'running') {
      state = liveEventProgress.state
      stalled = liveEventProgress.stalled
      interrupted = liveEventProgress.interrupted
    }
  } else {
    // authoritativeRun.state === 'running'
    if (liveEventProgress.state === 'stalled' || liveEventProgress.stalled) {
      state = 'stalled'
      stalled = true
    } else if (liveIsStrictlyNewer && AUTHORITATIVE_NON_RUNNING_STATES.has(liveEventProgress.state)) {
      state = liveEventProgress.state
      stalled = liveEventProgress.stalled
      interrupted = liveEventProgress.interrupted
    }
    currentTool = liveEventProgress.currentTool ?? authoritativeRun.currentTool
  }

  const costUsd =
    authoritativeRun.costUsd !== null && liveEventProgress.costUsd !== null
      ? Math.max(authoritativeRun.costUsd, liveEventProgress.costUsd)
      : authoritativeRun.costUsd ?? liveEventProgress.costUsd

  return {
    agentId: authoritativeRun.agentId,
    state,
    stalled,
    interrupted,
    elapsedSeconds: Math.max(authoritativeRun.elapsedSeconds, liveEventProgress.elapsedSeconds),
    lastActivityAt: liveIsStrictlyNewer ? liveEventProgress.lastActivityAt : authoritativeRun.lastActivityAt ?? liveEventProgress.lastActivityAt,
    currentTool,
    toolCalls: Math.max(authoritativeRun.toolCalls, liveEventProgress.toolCalls),
    inputTokens: Math.max(authoritativeRun.inputTokens, liveEventProgress.inputTokens),
    outputTokens: Math.max(authoritativeRun.outputTokens, liveEventProgress.outputTokens),
    costUsd,
    mergeState: liveEventProgress.mergeState ?? authoritativeRun.mergeState,
    parentAgentId: authoritativeRun.parentAgentId ?? liveEventProgress.parentAgentId,
    resultRef: authoritativeRun.resultRef ?? liveEventProgress.resultRef,
  }
}

export function hydrateAgentRuns(
  runs: readonly AgentRunSummary[],
  current: AgentProgressMap = {},
): AgentProgressMap {
  const next: AgentProgressMap = { ...current }
  for (const run of runs) {
    if (!run.agent_id) continue
    const state = normalizeRunState(run.state, 'running')
    next[run.agent_id] = mergeAgentProgress(next[run.agent_id], {
      agentId: run.agent_id,
      state,
      stalled: typeof run.stalled === 'boolean' ? run.stalled : state === 'stalled',
      interrupted: state === 'interrupted',
      elapsedSeconds: numberOr(run.elapsed_seconds, next[run.agent_id]?.elapsedSeconds ?? 0),
      lastActivityAt: run.last_activity_at ?? next[run.agent_id]?.lastActivityAt ?? null,
      currentTool: run.current_tool !== undefined ? run.current_tool : next[run.agent_id]?.currentTool ?? null,
      toolCalls: numberOr(run.tool_calls, next[run.agent_id]?.toolCalls ?? 0),
      inputTokens: numberOr(run.input_tokens, next[run.agent_id]?.inputTokens ?? 0),
      outputTokens: numberOr(run.output_tokens, next[run.agent_id]?.outputTokens ?? 0),
      costUsd: nullableNumberOr(run.cost_usd, next[run.agent_id]?.costUsd ?? null),
      mergeState: run.merge_state !== undefined ? normalizeMergeState(run.merge_state) : next[run.agent_id]?.mergeState ?? null,
      parentAgentId: run.parent_agent_id !== undefined ? run.parent_agent_id : next[run.agent_id]?.parentAgentId ?? null,
      resultRef: run.result_ref !== undefined ? run.result_ref : next[run.agent_id]?.resultRef ?? null,
    })
  }
  return next
}

export function reduceAgentProgressEvents(
  events: readonly ConversationActivityEvent[],
  initial: AgentProgressMap = {},
): AgentProgressMap {
  let state: AgentProgressMap = { ...initial }
  for (const event of events) {
    const type = event.type
    const isMergeOrWorktree =
      type === 'merge.applied' ||
      type === 'merge.conflict' ||
      type === 'merge.check_failed' ||
      type === 'worktree.created' ||
      type === 'worktree.removed'
    if (
      type !== 'agent.progress' &&
      type !== 'agent.stalled' &&
      type !== 'agent.interrupted' &&
      !isMergeOrWorktree
    ) {
      continue
    }
    const payload = (event.payload ?? {}) as Record<string, unknown>
    const agentId =
      (typeof payload.agent_id === 'string' && payload.agent_id.trim()) ||
      (typeof event.agentId === 'string' && event.agentId.trim()) ||
      ''
    if (!agentId) continue

    const existing = state[agentId]
    if (isMergeOrWorktree) {
      const nextMergeState: AgentMergeState | null =
        type === 'merge.applied'
          ? 'applied'
          : type === 'merge.conflict'
            ? 'conflict'
            : type === 'merge.check_failed'
              ? 'check_failed'
              : type === 'worktree.created'
                ? 'pending'
                : normalizeMergeState(payload.merge_state) ?? existing?.mergeState ?? null
      state = {
        ...state,
        [agentId]: mergeAgentProgress(existing, {
          agentId,
          mergeState: nextMergeState,
          ...(event.parentAgentId ? { parentAgentId: event.parentAgentId } : {}),
        }),
      }
      continue
    }

    const fallbackState: AgentRunState =
      type === 'agent.stalled' ? 'stalled' : type === 'agent.interrupted' ? 'interrupted' : existing?.state ?? 'running'
    const nextState =
      type === 'agent.stalled'
        ? 'stalled'
        : type === 'agent.interrupted'
          ? 'interrupted'
          : normalizeRunState(payload.state ?? payload.status ?? event.status, fallbackState)

    const patch: Partial<AgentProgressData> & { agentId: string } = {
      agentId,
      state: nextState,
      stalled:
        type === 'agent.stalled'
          ? true
          : typeof payload.stalled === 'boolean'
            ? payload.stalled
            : nextState === 'stalled',
      interrupted: type === 'agent.interrupted' ? true : nextState === 'interrupted',
    }
    if (typeof payload.elapsed_seconds === 'number' && Number.isFinite(payload.elapsed_seconds)) {
      patch.elapsedSeconds = payload.elapsed_seconds
    }
    if (typeof payload.last_activity_at === 'string') {
      patch.lastActivityAt = payload.last_activity_at
    }
    if ('current_tool' in payload) {
      patch.currentTool = typeof payload.current_tool === 'string' && payload.current_tool.trim() ? payload.current_tool : null
    }
    if (typeof payload.tool_calls === 'number' && Number.isFinite(payload.tool_calls)) {
      patch.toolCalls = payload.tool_calls
    }
    if (typeof payload.input_tokens === 'number' && Number.isFinite(payload.input_tokens)) {
      patch.inputTokens = payload.input_tokens
    }
    if (typeof payload.output_tokens === 'number' && Number.isFinite(payload.output_tokens)) {
      patch.outputTokens = payload.output_tokens
    }
    if ('cost_usd' in payload) {
      patch.costUsd = nullableNumberOr(payload.cost_usd, existing?.costUsd ?? null)
    }
    if ('merge_state' in payload) {
      patch.mergeState = normalizeMergeState(payload.merge_state)
    } else if (payload.worktree && typeof payload.worktree === 'object') {
      const wt = payload.worktree as Record<string, unknown>
      if ('merge_state' in wt) {
        patch.mergeState = normalizeMergeState(wt.merge_state)
      }
    }
    if (typeof payload.result_ref === 'string') {
      patch.resultRef = payload.result_ref
    }
    if (event.parentAgentId) {
      patch.parentAgentId = event.parentAgentId
    }

    state = {
      ...state,
      [agentId]: mergeAgentProgress(existing, patch),
    }
  }
  return state
}

export function projectAgentTreeNodes<T>(
  items: readonly T[],
  idOf: (item: T) => string,
  parentOf: (item: T) => string | null | undefined,
  progressByAgent: AgentProgressMap,
): Array<{ item: T; depth: number; progress: AgentProgressData | null }> {
  return orderAgentTree(items, idOf, parentOf).map(({ item, depth }) => ({
    item,
    depth,
    progress: progressByAgent[idOf(item)] ?? null,
  }))
}
