import { describe, expect, it } from 'vitest'
import type { ConversationActivityEvent } from '../../conversations/activityTypes'
import { orderSteps, projectPipelineEvents } from '../pipelineProjection'

let counter = 0
function event(type: string, payload: Record<string, unknown>, extra: Partial<ConversationActivityEvent> = {}): ConversationActivityEvent {
  counter += 1
  return {
    eventId: `evt-${counter}`,
    cursor: String(counter),
    type,
    kind: 'lifecycle',
    state: 'working',
    agentId: 'agent:main',
    turnId: 'turn-1',
    summary: type,
    payload: { run_id: 'run-1', ...payload },
    ...extra,
  }
}

function loopedRun(): ConversationActivityEvent[] {
  return [
    event('pipeline.started', { pipeline_id: 'implementar-verificar', source: 'builtin', definition_hash: 'h', steps_total: 4, input_names: ['objetivo'] }),
    event('pipeline.step_started', { step_id: 'plano', item_key: '', attempt: 1, agent: 'plan', provider: 'claude', model: 'claude-sonnet-5-5', loop_count: 0 }),
    event('pipeline.step_finished', { step_id: 'plano', item_key: '', state: 'succeeded', input_tokens: 1000, output_tokens: 200, cost_usd: 0.5, cost_known: true, duration_ms: 4000 }),
    event('pipeline.step_started', { step_id: 'verificar', item_key: '', attempt: 1, loop_count: 0 }),
    event('pipeline.gate_evaluated', { step_id: 'verificar', item_key: '', gate_kind: 'command', passed: false, exit_code: 1, findings_count: 0 }),
    event('pipeline.step_finished', { step_id: 'verificar', item_key: '', state: 'gate_failed', input_tokens: 0, output_tokens: 0, cost_usd: null, cost_known: true }),
    event('pipeline.loop', { step_id: 'verificar', loop_count: 1, max_loops: 2, reason: 'gate reprovado' }),
    event('pipeline.step_started', { step_id: 'verificar', item_key: '', attempt: 1, loop_count: 1 }),
    event('pipeline.step_finished', { step_id: 'verificar', item_key: '', state: 'succeeded', input_tokens: 0, output_tokens: 0, cost_usd: null, cost_known: true }),
    event('pipeline.finished', { state: 'succeeded', stop_reason: null, used_tokens: 1200, used_cost_usd: 0.5, cost_known: true, duration_ms: 90000, steps_succeeded: 3, steps_failed: 0 }),
  ]
}

describe('projectPipelineEvents', () => {
  it('folds a run into steps with state, loop, tokens and cost', () => {
    const run = projectPipelineEvents(loopedRun())['run-1']
    expect(run.pipelineId).toBe('implementar-verificar')
    expect(run.state).toBe('succeeded')
    expect(run.steps.map((step) => step.stepId)).toEqual(['plano', 'verificar'])
    const plano = run.steps[0]
    expect(plano.state).toBe('succeeded')
    expect(plano.inputTokens + plano.outputTokens).toBe(1200)
    expect(plano.costUsd).toBe(0.5)
    const verificar = run.steps[1]
    expect(verificar.loop).toEqual({ count: 1, max: 2, reason: 'gate reprovado' })
    expect(verificar.items).toHaveLength(2)
    expect(verificar.items[0].state).toBe('gate_failed')
    expect(verificar.items[0].gate?.passed).toBe(false)
    expect(verificar.state).toBe('succeeded')
    expect(run.usedTokens).toBe(1200)
    expect(run.durationMs).toBe(90000)
  })

  it('is idempotent for duplicated and out-of-order events', () => {
    const ordered = loopedRun()
    const shuffled = [...ordered].reverse()
    const duplicated = [...ordered, ...ordered.slice(2, 6)]
    expect(projectPipelineEvents(shuffled)).toEqual(projectPipelineEvents(ordered))
    expect(projectPipelineEvents(duplicated)).toEqual(projectPipelineEvents(ordered))
  })

  it('never shows an unknown cost as zero', () => {
    const run = projectPipelineEvents([
      event('pipeline.started', { pipeline_id: 'x', steps_total: 1 }),
      event('pipeline.step_started', { step_id: 'a', item_key: '', attempt: 1, loop_count: 0 }),
      event('pipeline.step_finished', { step_id: 'a', item_key: '', state: 'succeeded', input_tokens: 500, output_tokens: 50, cost_usd: null, cost_known: false }),
    ])['run-1']
    expect(run.steps[0].costUsd).toBeNull()
    expect(run.steps[0].costKnown).toBe(false)
    expect(run.usedCostUsd).toBeNull()
    expect(run.costKnown).toBe(false)
    expect(run.state).toBe('running')
  })

  it('keeps map items apart, marks reuse, retry, degradation and budget exhaustion', () => {
    const run = projectPipelineEvents([
      event('pipeline.started', { pipeline_id: 'x', steps_total: 2 }),
      event('pipeline.resumed', { reused_steps: 1, pending_steps: 2, definition_changed: true }),
      event('pipeline.step_reused', { step_id: 'plano', item_key: '', loop_count: 0, from_attempt: 1 }),
      event('pipeline.step_started', { step_id: 'implementar', item_key: '000-aa', attempt: 1, loop_count: 0 }),
      event('pipeline.step_started', { step_id: 'implementar', item_key: '001-bb', attempt: 1, loop_count: 0 }),
      event('pipeline.step_finished', { step_id: 'implementar', item_key: '000-aa', state: 'failed', input_tokens: 10, output_tokens: 1, cost_usd: 0.01, cost_known: true, error_code: 'SUBAGENT_FAILED' }),
      event('pipeline.step_retrying', { step_id: 'implementar', item_key: '000-aa', attempt: 1, next_attempt: 2, reason: 'failed', delay_ms: 5000 }),
      event('pipeline.degraded', { reason: 'models_auto_partial', detail: 'só 2 famílias', effect: 'revisão com 2 modelos' }),
      event('pipeline.budget_exceeded', { scope: 'pipeline', limit_kind: 'max_tokens', limit: 1000, used: 1200 }),
    ])['run-1']
    const implementar = run.steps.find((step) => step.stepId === 'implementar')
    expect(implementar?.items.map((item) => item.itemKey)).toEqual(['000-aa', '001-bb'])
    expect(implementar?.items[0].state).toBe('retrying')
    expect(implementar?.items[0].retry?.nextAttempt).toBe(2)
    expect(implementar?.items[1].state).toBe('running')
    expect(implementar?.state).toBe('running')
    expect(run.steps.find((step) => step.stepId === 'plano')?.state).toBe('reused')
    expect(run.resumed).toEqual({ reusedSteps: 1, pendingSteps: 2, definitionChanged: true })
    expect(run.degraded).toEqual([{ reason: 'models_auto_partial', detail: 'só 2 famílias', effect: 'revisão com 2 modelos' }])
    expect(run.budgetExceeded).toEqual({ scope: 'pipeline', limitKind: 'max_tokens', limit: 1000, used: 1200 })
  })

  it('ignores events that are not pipeline events and orders steps by the definition', () => {
    const events = [
      event('agent.progress', { agent_id: 'sub-1', input_tokens: 5 }),
      ...loopedRun().slice(0, 4),
    ]
    const run = projectPipelineEvents(events)['run-1']
    expect(run.steps.map((step) => step.stepId)).toEqual(['plano', 'verificar'])
    expect(orderSteps(run.steps, ['verificar', 'plano']).map((step) => step.stepId)).toEqual(['verificar', 'plano'])
    expect(orderSteps(run.steps, null).map((step) => step.stepId)).toEqual(['plano', 'verificar'])
  })
})
