/**
 * Folds `pipeline.*` activity events into one model per run (0.13 WS-C11).
 *
 * The fold is pure and idempotent: events are de-duplicated by id, ordered by cursor when every cursor is numeric, and a
 * later phase of a step never goes back to an earlier one, so a duplicated or late event cannot undo what is known.
 * A cost the server did not know is `null` and `costKnown = false`: it is shown as "sem preço", never as zero.
 */
import type { ConversationActivityEvent } from '../conversations/activityTypes'
import type { DegradedNote } from '../../api/pipelines'

export type StepItemState =
  | 'running'
  | 'retrying'
  | 'succeeded'
  | 'failed'
  | 'gate_failed'
  | 'stalled'
  | 'interrupted'
  | 'skipped'
  | 'reused'
  | 'pending'

export type GateModel = { kind: string; passed: boolean; exitCode: number | null; verdict: string | null; findingsCount: number }

export type StepItemModel = {
  itemKey: string
  loopCount: number
  attempt: number
  state: StepItemState
  reused: boolean
  retry: { nextAttempt: number; reason: string; delayMs: number } | null
  agent: string | null
  provider: string | null
  model: string | null
  effort: string | null
  inputTokens: number
  outputTokens: number
  costUsd: number | null
  costKnown: boolean
  durationMs: number | null
  outputRef: string | null
  errorCode: string | null
  gate: GateModel | null
  stalledForSeconds: number | null
}

export type StepModel = {
  stepId: string
  items: StepItemModel[]
  /** `count` is the highest "volta" seen for this step; `max` comes from the `pipeline.loop` event. */
  loop: { count: number; max: number | null; reason: string } | null
  state: StepItemState
  inputTokens: number
  outputTokens: number
  costUsd: number | null
  costKnown: boolean
  reusedItems: number
  attempts: number
}

export type PipelineRunPhase = 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted' | 'budget_exceeded'

export type BudgetExceededNote = { scope: string; limitKind: string; limit: number | null; used: number | null }

export type PipelineRunModel = {
  runId: string
  pipelineId: string
  source: string
  definitionHash: string
  stepsTotal: number
  inputNames: string[]
  turnId: string | null
  state: PipelineRunPhase
  stopReason: string | null
  steps: StepModel[]
  usedTokens: number
  usedCostUsd: number | null
  costKnown: boolean
  durationMs: number | null
  stepsSucceeded: number | null
  stepsFailed: number | null
  degraded: DegradedNote[]
  budgetExceeded: BudgetExceededNote | null
  resumed: { reusedSteps: number; pendingSteps: number; definitionChanged: boolean } | null
}

export type PipelineProjection = Record<string, PipelineRunModel>

const FINAL_ITEM: ReadonlySet<StepItemState> = new Set(['succeeded', 'failed', 'gate_failed', 'skipped', 'reused', 'interrupted'])

function text(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null
}

function count(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0
}

function maybeNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function payloadOf(event: ConversationActivityEvent): Record<string, unknown> {
  return event.payload && typeof event.payload === 'object' ? event.payload : {}
}

export function isPipelineEvent(event: ConversationActivityEvent): boolean {
  return event.type.startsWith('pipeline.')
}

export function isGoalEvent(event: ConversationActivityEvent): boolean {
  return event.type.startsWith('goal.')
}

function emptyRun(runId: string): PipelineRunModel {
  return {
    runId, pipelineId: '', source: '', definitionHash: '', stepsTotal: 0, inputNames: [], turnId: null, state: 'running', stopReason: null,
    steps: [], usedTokens: 0, usedCostUsd: null, costKnown: true, durationMs: null, stepsSucceeded: null, stepsFailed: null,
    degraded: [], budgetExceeded: null, resumed: null,
  }
}

function emptyItem(itemKey: string, loopCount: number): StepItemModel {
  return {
    itemKey, loopCount, attempt: 0, state: 'pending', reused: false, retry: null, agent: null, provider: null, model: null, effort: null,
    inputTokens: 0, outputTokens: 0, costUsd: null, costKnown: true, durationMs: null, outputRef: null, errorCode: null, gate: null,
    stalledForSeconds: null,
  }
}

function numericCursor(event: ConversationActivityEvent): number | null {
  const value = Number(event.cursor)
  return event.cursor !== '' && Number.isFinite(value) ? value : null
}

/** Cursor order when every cursor is numeric; the given order otherwise. Duplicated event ids are dropped. */
export function orderedPipelineEvents(events: readonly ConversationActivityEvent[]): ConversationActivityEvent[] {
  const seen = new Set<string>()
  const unique: ConversationActivityEvent[] = []
  for (const event of events) {
    if (!isPipelineEvent(event) || seen.has(event.eventId)) continue
    seen.add(event.eventId)
    unique.push(event)
  }
  if (unique.every((event) => numericCursor(event) !== null)) {
    return unique
      .map((event, index) => ({ event, index, cursor: numericCursor(event) as number }))
      .sort((a, b) => a.cursor - b.cursor || a.index - b.index)
      .map((entry) => entry.event)
  }
  return unique
}

function stepOf(run: PipelineRunModel, stepId: string): StepModel {
  let step = run.steps.find((entry) => entry.stepId === stepId)
  if (!step) {
    step = {
      stepId, items: [], loop: null, state: 'pending', inputTokens: 0, outputTokens: 0, costUsd: null, costKnown: true,
      reusedItems: 0, attempts: 0,
    }
    run.steps.push(step)
  }
  return step
}

function itemOf(step: StepModel, itemKey: string, loopCount: number): StepItemModel {
  let item = step.items.find((entry) => entry.itemKey === itemKey && entry.loopCount === loopCount)
  if (!item) {
    item = emptyItem(itemKey, loopCount)
    step.items.push(item)
  }
  return item
}

function aggregate(step: StepModel): void {
  const latestLoop = step.items.reduce((highest, item) => Math.max(highest, item.loopCount), 0)
  const visit = step.items.filter((item) => item.loopCount === latestLoop)
  const states = visit.map((item) => item.state)
  step.state =
    states.includes('running') ? 'running'
      : states.includes('retrying') ? 'retrying'
        : states.includes('gate_failed') ? 'gate_failed'
          : states.includes('failed') ? 'failed'
            : states.includes('stalled') ? 'stalled'
              : states.includes('interrupted') ? 'interrupted'
                : states.length > 0 && states.every((state) => state === 'skipped') ? 'skipped'
                  : states.length > 0 && states.every((state) => state === 'reused') ? 'reused'
                    : states.length > 0 ? 'succeeded' : 'pending'
  step.inputTokens = step.items.reduce((sum, item) => sum + item.inputTokens, 0)
  step.outputTokens = step.items.reduce((sum, item) => sum + item.outputTokens, 0)
  const known = step.items.filter((item) => item.costUsd !== null)
  step.costUsd = known.length ? known.reduce((sum, item) => sum + (item.costUsd ?? 0), 0) : null
  step.costKnown = step.items.every((item) => item.costKnown)
  step.reusedItems = step.items.filter((item) => item.reused).length
  step.attempts = step.items.reduce((sum, item) => sum + item.attempt, 0)
  if (step.loop) step.loop.count = Math.max(step.loop.count, latestLoop)
  else if (latestLoop > 0) step.loop = { count: latestLoop, max: null, reason: '' }
}

function applyEvent(run: PipelineRunModel, event: ConversationActivityEvent): void {
  const p = payloadOf(event)
  const stepId = text(p.step_id)
  const itemKey = text(p.item_key) ?? ''
  const loopCount = count(p.loop_count)
  switch (event.type) {
    case 'pipeline.started':
      run.pipelineId = text(p.pipeline_id) ?? run.pipelineId
      run.source = text(p.source) ?? run.source
      run.definitionHash = text(p.definition_hash) ?? run.definitionHash
      run.stepsTotal = count(p.steps_total) || run.stepsTotal
      run.inputNames = Array.isArray(p.input_names) ? p.input_names.filter((name): name is string => typeof name === 'string') : run.inputNames
      run.turnId = event.turnId ?? run.turnId
      if (run.state !== 'running' && run.stopReason === null) run.state = 'running'
      break
    case 'pipeline.step_started': {
      if (!stepId) break
      const step = stepOf(run, stepId)
      const item = itemOf(step, itemKey, loopCount)
      const attempt = count(p.attempt) || 1
      if (attempt >= item.attempt && !(FINAL_ITEM.has(item.state) && attempt === item.attempt)) {
        item.attempt = attempt
        item.state = 'running'
        item.retry = null
        item.errorCode = null
      }
      item.agent = text(p.agent) ?? item.agent
      item.provider = text(p.provider) ?? item.provider
      item.model = text(p.model) ?? item.model
      item.effort = text(p.effort) ?? item.effort
      aggregate(step)
      break
    }
    case 'pipeline.step_finished': {
      if (!stepId) break
      const step = stepOf(run, stepId)
      // The journal does not send `loop_count` on a finish: it belongs to the visit that is open for this item.
      const open = [...step.items].reverse().find((entry) => entry.itemKey === itemKey)
      const item = open ?? itemOf(step, itemKey, loopCount)
      const state = text(p.state) as StepItemState | null
      item.state = state && state !== 'running' ? state : 'succeeded'
      item.retry = null
      item.inputTokens = count(p.input_tokens)
      item.outputTokens = count(p.output_tokens)
      item.costUsd = maybeNumber(p.cost_usd)
      item.costKnown = p.cost_known !== false && (item.costUsd !== null || item.inputTokens + item.outputTokens === 0 || p.cost_known === true)
      item.durationMs = maybeNumber(p.duration_ms)
      item.outputRef = text(p.output_ref)
      item.errorCode = text(p.error_code)
      aggregate(step)
      break
    }
    case 'pipeline.step_reused': {
      if (!stepId) break
      const step = stepOf(run, stepId)
      const item = itemOf(step, itemKey, loopCount)
      item.state = 'reused'
      item.reused = true
      item.attempt = Math.max(item.attempt, count(p.from_attempt))
      aggregate(step)
      break
    }
    case 'pipeline.step_retrying': {
      if (!stepId) break
      const step = stepOf(run, stepId)
      const open = [...step.items].reverse().find((entry) => entry.itemKey === itemKey)
      const item = open ?? itemOf(step, itemKey, loopCount)
      const next = count(p.next_attempt)
      if (item.state !== 'running' || item.attempt <= count(p.attempt)) {
        item.state = 'retrying'
        item.retry = { nextAttempt: next, reason: text(p.reason) ?? '', delayMs: count(p.delay_ms) }
      }
      aggregate(step)
      break
    }
    case 'pipeline.gate_evaluated': {
      if (!stepId) break
      const step = stepOf(run, stepId)
      const open = [...step.items].reverse().find((entry) => entry.itemKey === itemKey)
      const item = open ?? itemOf(step, itemKey, loopCount)
      item.gate = {
        kind: text(p.gate_kind) ?? 'gate',
        passed: p.passed === true,
        exitCode: maybeNumber(p.exit_code),
        verdict: text(p.verdict),
        findingsCount: count(p.findings_count),
      }
      aggregate(step)
      break
    }
    case 'pipeline.loop': {
      if (!stepId) break
      const step = stepOf(run, stepId)
      const current = count(p.loop_count)
      step.loop = {
        count: Math.max(step.loop?.count ?? 0, current),
        max: maybeNumber(p.max_loops) ?? step.loop?.max ?? null,
        reason: text(p.reason) ?? step.loop?.reason ?? '',
      }
      break
    }
    case 'pipeline.stalled': {
      if (!stepId) break
      const step = stepOf(run, stepId)
      const open = [...step.items].reverse().find((entry) => entry.itemKey === itemKey)
      if (open) {
        open.stalledForSeconds = count(p.idle_seconds) || null
        if (open.state === 'running') open.state = 'stalled'
      }
      aggregate(step)
      break
    }
    case 'pipeline.degraded': {
      const reason = text(p.reason)
      if (reason && !run.degraded.some((note) => note.reason === reason && note.detail === (text(p.detail) ?? ''))) {
        run.degraded.push({ reason, detail: text(p.detail) ?? '', effect: text(p.effect) ?? '' })
      }
      break
    }
    case 'pipeline.budget_exceeded':
      run.budgetExceeded = {
        scope: text(p.scope) ?? '',
        limitKind: text(p.limit_kind) ?? '',
        limit: maybeNumber(p.limit),
        used: maybeNumber(p.used),
      }
      break
    case 'pipeline.resumed':
      run.state = 'running'
      run.stopReason = null
      run.resumed = {
        reusedSteps: count(p.reused_steps),
        pendingSteps: count(p.pending_steps),
        definitionChanged: p.definition_changed === true,
      }
      break
    case 'pipeline.finished': {
      const state = text(p.state) as PipelineRunPhase | null
      if (state) run.state = state
      run.stopReason = text(p.stop_reason)
      run.usedTokens = count(p.used_tokens)
      run.usedCostUsd = maybeNumber(p.used_cost_usd)
      run.costKnown = p.cost_known !== false
      run.durationMs = maybeNumber(p.duration_ms)
      run.stepsSucceeded = maybeNumber(p.steps_succeeded)
      run.stepsFailed = maybeNumber(p.steps_failed)
      break
    }
    default:
      break
  }
}

/** Totals of a run that has not sent `pipeline.finished` yet: the sum of what its steps reported. */
function liveTotals(run: PipelineRunModel): void {
  if (run.state !== 'running' && run.durationMs !== null) return
  const finished = run.steps.every((step) => step.items.length === 0) ? null : run.steps
  if (!finished) return
  run.usedTokens = run.steps.reduce((sum, step) => sum + step.inputTokens + step.outputTokens, 0)
  const known = run.steps.filter((step) => step.costUsd !== null)
  run.usedCostUsd = known.length ? known.reduce((sum, step) => sum + (step.costUsd ?? 0), 0) : null
  run.costKnown = run.steps.every((step) => step.costKnown)
}

export function projectPipelineEvents(events: readonly ConversationActivityEvent[]): PipelineProjection {
  const runs: PipelineProjection = {}
  for (const event of orderedPipelineEvents(events)) {
    const runId = text(payloadOf(event).run_id)
    if (!runId) continue
    const run = (runs[runId] ??= emptyRun(runId))
    run.turnId = run.turnId ?? event.turnId ?? null
    applyEvent(run, event)
  }
  for (const run of Object.values(runs)) liveTotals(run)
  return runs
}

/** The steps in the order of the definition when it is known, otherwise in the order they first ran. */
export function orderSteps(steps: readonly StepModel[], definitionOrder: readonly string[] | null): StepModel[] {
  if (!definitionOrder || definitionOrder.length === 0) return [...steps]
  const rank = new Map(definitionOrder.map((id, index) => [id, index]))
  return [...steps].sort((a, b) => (rank.get(a.stepId) ?? 9999) - (rank.get(b.stepId) ?? 9999))
}

export const STEP_STATE_LABEL: Record<StepItemState, string> = {
  pending: 'Pendente',
  running: 'Rodando',
  retrying: 'Nova tentativa',
  succeeded: 'Concluída',
  failed: 'Falhou',
  gate_failed: 'Gate reprovado',
  stalled: 'Sem progresso',
  interrupted: 'Interrompida',
  skipped: 'Pulada',
  reused: 'Reaproveitada',
}
