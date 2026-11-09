import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { ConversationActivityEvent } from '../activityTypes'
import { buildTurnTimeline } from '../turnTimelineFold'
import { TurnTimeline } from '../TurnTimeline'

afterEach(() => cleanup())

let seq = 0
function toolEvent(agentId: string, parentAgentId: string | undefined, toolName: string): ConversationActivityEvent {
  seq += 1
  return {
    eventId: `ev-${seq}`,
    cursor: String(seq),
    turnId: 'turn-1',
    agentId,
    agentName: agentId === 'agent:main' ? 'Main' : agentId.replace('agent:', ''),
    parentAgentId,
    kind: 'tool',
    type: 'tool.finished',
    state: 'completed',
    status: 'succeeded',
    toolKind: 'filesystem',
    toolName,
    path: `src/file-${seq}.ts`,
    summary: `${toolName} ${seq}`,
    occurredAt: `2026-11-09T12:00:${String(seq % 60).padStart(2, '0')}Z`,
  }
}

describe('parallel subagents header count', () => {
  it('counts real subagents, not the main agent, when a subagent event arrives first without a parent id', () => {
    seq = 0
    const events: ConversationActivityEvent[] = [
      // A subagent lifecycle event recorded without parent_agent_id comes first
      // (code_mode.validation_started did this): it must not make "claude" the primary.
      toolEvent('agent:claude', undefined, 'read_file'),
      ...Array.from({ length: 12 }, (_, i) => toolEvent('agent:main', undefined, i % 2 ? 'read_file' : 'search_files')),
      toolEvent('agent:claude', 'agent:main', 'read_file'),
      toolEvent('agent:claude-2', 'agent:main', 'read_file'),
      toolEvent('agent:claude', 'agent:main', 'search_files'),
      toolEvent('agent:claude-2', 'agent:main', 'search_files'),
      ...Array.from({ length: 6 }, () => toolEvent('agent:main', undefined, 'edit_file')),
    ]

    const items = buildTurnTimeline(events, 'turn-1', 'msg-1', '', true)
    render(<TurnTimeline items={items} mode="code" turnCompleted={true} />)

    const groups = screen.queryAllByTestId('parallel-subagents-group')
    for (const group of groups) {
      expect(Number(group.getAttribute('data-count'))).toBeLessThanOrEqual(2)
      const names = Array.from(group.querySelectorAll('[data-agent-name]')).map((n) => n.getAttribute('data-agent-name'))
      expect(names).not.toContain('Main')
    }
    expect(screen.queryByText(/\b(1[0-9]|[3-9]) subagentes em paralelo/)).toBeNull()
  })

  it('counts one subagent split into several stretches once', () => {
    seq = 0
    const events: ConversationActivityEvent[] = [
      toolEvent('agent:main', undefined, 'read_file'),
      toolEvent('agent:sub-1', 'agent:main', 'read_file'),
      toolEvent('agent:sub-2', 'agent:main', 'read_file'),
      toolEvent('agent:sub-1', 'agent:main', 'edit_file'),
      toolEvent('agent:sub-2', 'agent:main', 'edit_file'),
      toolEvent('agent:sub-1', 'agent:main', 'search_files'),
    ]

    const items = buildTurnTimeline(events, 'turn-1', 'msg-1', '', true)
    render(<TurnTimeline items={items} mode="code" turnCompleted={true} />)

    const groups = screen.getAllByTestId('parallel-subagents-group')
    expect(groups).toHaveLength(1)
    expect(groups[0].getAttribute('data-count')).toBe('2')
  })
})
