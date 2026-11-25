import { describe, expect, it } from 'vitest'
import type { ConversationActivityEvent } from '../activityTypes'
import { rootTerminalTurns } from '../turnLiveness'

function event(id: number, type: string, extra: Record<string, unknown> = {}): ConversationActivityEvent {
  return { eventId: `ev:${id}`, type, turnId: 'turn-1', ...extra } as unknown as ConversationActivityEvent
}

describe('rootTerminalTurns', () => {
  it('a turn that stopped on exit_plan_mode is ended (waiting for the plan decision)', () => {
    const result = rootTerminalTurns([
      event(1, 'tool.started', { toolName: 'exit_plan_mode' }),
      event(2, 'tool.finished', { toolName: 'exit_plan_mode', planApproval: true }),
    ])
    expect(result.ids.has('turn-1')).toBe(true)
  })

  it('a turn that moved past exit_plan_mode to ask_user in the turn is still live', () => {
    const result = rootTerminalTurns([
      event(1, 'tool.finished', { toolName: 'exit_plan_mode', planApproval: true }),
      event(2, 'tool.started', { toolName: 'ask_user' }),
      event(3, 'tool.finished', { toolName: 'ask_user' }),
      event(4, 'turn.waiting_user', { payload: { in_turn: true } }),
    ])
    expect(result.ids.has('turn-1')).toBe(false)
    expect(result.anyTurn).toBe(false)
  })

  it('bookkeeping after the plan (context, speech) does not revive the turn', () => {
    const result = rootTerminalTurns([
      event(1, 'tool.finished', { toolName: 'exit_plan_mode', planApproval: true }),
      event(2, 'context.updated'),
      event(3, 'speak.sentence'),
    ])
    expect(result.ids.has('turn-1')).toBe(true)
  })

  it('completed, failed and cancelled stay final even if events trail them', () => {
    const result = rootTerminalTurns([
      event(1, 'turn.completed'),
      event(2, 'assistant.delta'),
    ])
    expect(result.ids.has('turn-1')).toBe(true)
  })

  it('a subagent plan never ends the leader turn', () => {
    const result = rootTerminalTurns([
      event(1, 'tool.finished', { toolName: 'exit_plan_mode', planApproval: true, parentAgentId: 'agent:main' }),
    ])
    expect(result.ids.size).toBe(0)
  })

  it('a terminal event without a turn id marks anyTurn', () => {
    const result = rootTerminalTurns([event(1, 'turn.completed', { turnId: undefined })])
    expect(result.anyTurn).toBe(true)
  })
})
