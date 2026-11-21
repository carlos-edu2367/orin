import { describe, expect, it } from 'vitest'

import { kindFor, stateFor, type ConversationActivityEvent } from '../activityTypes'
import {
  hydrateAgentRuns,
  orderAgentTree,
  projectAgentTreeNodes,
  reduceAgentProgressEvents,
  type AgentProgressData,
  type AgentRunSummary,
} from '../agentTree'

function makeEvent(
  eventType: ConversationActivityEvent['type'],
  agentId: string,
  payload: Record<string, unknown> = {},
  occurredAt = '2026-11-21T16:00:00Z',
): ConversationActivityEvent {
  return {
    eventId: `evt-${agentId}-${eventType}-${occurredAt}`,
    cursor: '1',
    type: eventType,
    kind: kindFor(eventType),
    state: stateFor(eventType, typeof payload.state === 'string' ? payload.state : undefined),
    agentId,
    turnId: 'turn-1',
    summary: 'progress',
    payload,
    occurredAt,
  }
}

describe('agentTree progress projection & events (WS-B5)', () => {
  it('preserves orderAgentTree depth-first ordering untouched', () => {
    const items = [
      { id: 'c2', parent: 'p1' },
      { id: 'p1', parent: null },
      { id: 'c1', parent: 'p1' },
      { id: 'gc1', parent: 'c1' },
    ]
    const ordered = orderAgentTree(
      items,
      (x) => x.id,
      (x) => x.parent,
    )
    expect(ordered.map((x) => [x.item.id, x.depth])).toEqual([
      ['p1', 0],
      ['c2', 1],
      ['c1', 1],
      ['gc1', 2],
    ])
  })

  it('reduces agent.progress, agent.stalled and agent.interrupted events accurately', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent('agent.progress', 'agent:1', {
        state: 'running',
        elapsed_seconds: 42,
        last_activity_at: '2026-11-21T16:00:42Z',
        current_tool: 'Bash',
        tool_calls: 3,
        input_tokens: 1200,
        output_tokens: 450,
        cost_usd: 0.032,
        stalled: false,
        merge_state: 'pending',
      }),
      makeEvent('agent.stalled', 'agent:1', {
        elapsed_seconds: 660,
        last_activity_at: '2026-11-21T16:00:42Z',
        stalled: true,
      }),
      makeEvent('agent.interrupted', 'agent:2', {
        state: 'interrupted',
        tool_calls: 2,
        input_tokens: 500,
        output_tokens: 100,
        cost_usd: 0.01,
      }),
    ]

    const map = reduceAgentProgressEvents(events)
    expect(map['agent:1']).toMatchObject({
      agentId: 'agent:1',
      state: 'stalled',
      stalled: true,
      interrupted: false,
      elapsedSeconds: 660,
      lastActivityAt: '2026-11-21T16:00:42Z',
      currentTool: 'Bash',
      toolCalls: 3,
      inputTokens: 1200,
      outputTokens: 450,
      costUsd: 0.032,
      mergeState: 'pending',
    } satisfies Partial<AgentProgressData>)

    expect(map['agent:2']).toMatchObject({
      agentId: 'agent:2',
      state: 'interrupted',
      stalled: false,
      interrupted: true,
      toolCalls: 2,
      inputTokens: 500,
      outputTokens: 100,
      costUsd: 0.01,
    })
  })

  it('hydrates from /agent-runs snapshots and projects enriched tree nodes', () => {
    const runs: AgentRunSummary[] = [
      {
        agent_id: 'agent:root',
        parent_agent_id: null,
        state: 'running',
        last_activity_at: '2026-11-21T16:05:00Z',
        tool_calls: 4,
        input_tokens: 900,
        output_tokens: 300,
        cost_usd: 0.025,
        stalled: false,
        merge_state: 'applied',
      },
      {
        agent_id: 'agent:child',
        parent_agent_id: 'agent:root',
        state: 'interrupted',
        last_activity_at: '2026-11-21T16:02:00Z',
        tool_calls: 1,
        input_tokens: 300,
        output_tokens: 100,
        cost_usd: 0.009,
        stalled: false,
      },
    ]

    const hydrated = hydrateAgentRuns(runs)
    const nodes = projectAgentTreeNodes(
      [
        { id: 'agent:root', parent: null },
        { id: 'agent:child', parent: 'agent:root' },
      ],
      (x) => x.id,
      (x) => x.parent,
      hydrated,
    )

    expect(nodes).toHaveLength(2)
    expect(nodes[0].depth).toBe(0)
    expect(nodes[0].progress?.mergeState).toBe('applied')
    expect(nodes[0].progress?.inputTokens).toBe(900)
    expect(nodes[1].depth).toBe(1)
    expect(nodes[1].progress?.interrupted).toBe(true)
    expect(nodes[1].progress?.state).toBe('interrupted')
  })

  it('maps agent.progress, agent.stalled and agent.interrupted in activityTypes helpers', () => {
    expect(kindFor('agent.progress')).toBe('agent')
    expect(kindFor('agent.stalled')).toBe('agent')
    expect(kindFor('agent.interrupted')).toBe('agent')

    expect(stateFor('agent.stalled')).toBe('stalled')
    expect(stateFor('agent.interrupted')).toBe('interrupted')
    expect(stateFor('agent.progress', 'running')).toBe('working')
    expect(stateFor('agent.progress', 'stalled')).toBe('stalled')
    expect(stateFor('agent.progress', 'completed')).toBe('completed')
  })
})
