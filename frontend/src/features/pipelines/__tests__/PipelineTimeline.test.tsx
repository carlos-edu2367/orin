import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { activityReducer, createActivityState } from '../../conversations/activityReducer'
import type { ConversationActivityEvent } from '../../conversations/activityTypes'
import { groupAgentTreeByPipelineRun, orderAgentTree } from '../../conversations/agentTree'
import { TurnTimeline } from '../../conversations/TurnTimeline'
import { buildTurnTimeline } from '../../conversations/turnTimelineFold'

let counter = 0
function event(type: string, payload: Record<string, unknown>, extra: Partial<ConversationActivityEvent> = {}): ConversationActivityEvent {
  counter += 1
  return {
    eventId: `tl-${counter}`,
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

function runEvents(): ConversationActivityEvent[] {
  return [
    event('pipeline.started', { pipeline_id: 'implementar-verificar', source: 'builtin', definition_hash: 'h', steps_total: 2, input_names: [] }),
    event('pipeline.step_started', { step_id: 'plano', item_key: '', attempt: 1, loop_count: 0 }),
    event('pipeline.step_finished', { step_id: 'plano', item_key: '', state: 'succeeded', input_tokens: 800, output_tokens: 200, cost_usd: 0.2, cost_known: true }),
    event('pipeline.step_started', { step_id: 'verificar', item_key: '', attempt: 1, loop_count: 0 }),
    event('pipeline.step_finished', { step_id: 'verificar', item_key: '', state: 'gate_failed', input_tokens: 0, output_tokens: 0, cost_usd: null, cost_known: true }),
    event('pipeline.loop', { step_id: 'verificar', loop_count: 1, max_loops: 2, reason: 'gate reprovado' }),
    event('pipeline.step_started', { step_id: 'verificar', item_key: '', attempt: 1, loop_count: 1 }),
    event('pipeline.step_finished', { step_id: 'verificar', item_key: '', state: 'succeeded', input_tokens: 0, output_tokens: 0, cost_usd: null, cost_known: true }),
    event('pipeline.finished', { state: 'succeeded', stop_reason: null, used_tokens: 1000, used_cost_usd: 0.2, cost_known: true, duration_ms: 60000, steps_succeeded: 2, steps_failed: 0 }),
  ]
}

describe('pipeline events in the conversation timeline', () => {
  it('the reducer keeps a pipelines projection in step with the events', () => {
    let state = createActivityState()
    for (const item of runEvents()) state = activityReducer(state, { type: 'event', event: item })
    expect(Object.keys(state.pipelines)).toEqual(['run-1'])
    expect(state.pipelines['run-1'].state).toBe('succeeded')
    expect(state.pipelines['run-1'].steps.map((step) => step.stepId)).toEqual(['plano', 'verificar'])
  })

  it('a non-pipeline event does not recompute the projection', () => {
    let state = createActivityState()
    for (const item of runEvents()) state = activityReducer(state, { type: 'event', event: item })
    const before = state.pipelines
    state = activityReducer(state, {
      type: 'event',
      event: { ...event('assistant.message', {}), payload: { text: 'oi' } },
    })
    expect(state.pipelines).toBe(before)
  })

  it('a snapshot replaces the projection from its own events', () => {
    let state = createActivityState()
    state = activityReducer(state, { type: 'snapshot', events: runEvents(), cursor: '99' })
    expect(state.pipelines['run-1']?.state).toBe('succeeded')
    state = activityReducer(state, { type: 'snapshot', events: [], cursor: '100' })
    expect(state.pipelines).toEqual({})
  })

  it('folds every pipeline.* event of a run into one card, with the loop visible', () => {
    const items = buildTurnTimeline(runEvents(), 'turn-1', 'msg-1', 'Pronto.', true)
    const pipelineItems = items.filter((item) => item.kind === 'activity' && item.group.id === 'pipeline:run-1')
    expect(pipelineItems).toHaveLength(1)
    const only = pipelineItems[0]
    expect(only.kind === 'activity' ? only.group.events : []).toHaveLength(runEvents().length)
    render(
      <MemoryRouter>
        <TurnTimeline mode="code" items={items} />
      </MemoryRouter>,
    )
    const cards = screen.getAllByTestId('pipeline-run-card')
    expect(cards).toHaveLength(1)
    const card = cards[0]
    expect(within(card).getByText('implementar-verificar')).toBeInTheDocument()
    expect(within(card).getByTestId('pipeline-step-verificar')).toHaveTextContent('volta 1 de 2')
    expect(within(card).getByRole('link', { name: 'Abrir execução' })).toHaveAttribute('href', '/pipelines/runs/run-1')
  })

  it('two runs in the same turn are two cards', () => {
    const second = runEvents().map((item) => ({ ...item, eventId: `${item.eventId}-b`, payload: { ...item.payload, run_id: 'run-2' } }))
    const items = buildTurnTimeline([...runEvents(), ...second], 'turn-1', 'msg-1', 'Pronto.', true)
    render(
      <MemoryRouter>
        <TurnTimeline mode="code" items={items} />
      </MemoryRouter>,
    )
    expect(screen.getAllByTestId('pipeline-run-card')).toHaveLength(2)
  })
})

describe('agent tree grouped by pipeline run', () => {
  type Agent = { id: string; parent: string | null; run: string | null }
  const agents: Agent[] = [
    { id: 'a', parent: null, run: null },
    { id: 'p1', parent: null, run: 'run-1' },
    { id: 'p1c', parent: 'p1', run: 'run-1' },
    { id: 'b', parent: null, run: null },
    { id: 'p2', parent: null, run: 'run-2' },
  ]

  it('keeps plain agents first and lists the children of each run together, parent before child', () => {
    const ordered = orderAgentTree(agents, (agent) => agent.id, (agent) => agent.parent)
    const sections = groupAgentTreeByPipelineRun(ordered, (agent) => agent.run)
    expect(sections.map((section) => section.runId)).toEqual([null, 'run-1', 'run-2'])
    expect(sections[0].entries.map((entry) => entry.item.id)).toEqual(['a', 'b'])
    expect(sections[1].entries.map((entry) => [entry.item.id, entry.depth])).toEqual([['p1', 0], ['p1c', 1]])
  })

  it('without runs it returns one plain section, and nothing for an empty tree', () => {
    const plain = agents.filter((agent) => !agent.run)
    const ordered = orderAgentTree(plain, (agent) => agent.id, (agent) => agent.parent)
    expect(groupAgentTreeByPipelineRun(ordered, (agent) => agent.run)).toHaveLength(1)
    expect(groupAgentTreeByPipelineRun([], () => null)).toEqual([])
  })
})
