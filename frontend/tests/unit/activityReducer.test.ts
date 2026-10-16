import { describe, expect, it } from 'vitest'
import { applyActivityEvent, createActivityState, activityReducer } from '../../src/features/conversations/activityReducer'
import { parseConversation, parseConversationActivityEvent } from '../../src/api/conversations'
import type { ConversationActivityEvent } from '../../src/features/conversations/activityTypes'

const event = (eventId: string, cursor: string, type: ConversationActivityEvent['type'] = 'turn.started'): ConversationActivityEvent => ({
  eventId,
  cursor,
  type,
  kind: 'lifecycle' as const,
  state: 'working' as const,
  agentId: 'agent:conversation-1:main',
  summary: 'Turn started',
  turnId: 'turn-1',
})

describe('conversation activity reducer', () => {
  it('applies each event_id once and advances the opaque cursor from heartbeats', () => {
    const first = applyActivityEvent(createActivityState(), event('event-1', '1'))
    const duplicate = applyActivityEvent(first, event('event-1', '1'))
    const next = activityReducer(duplicate, { type: 'cursor', cursor: '2' })

    expect(next.events).toHaveLength(1)
    expect(next.cursor).toBe('2')
  })

  it('ignores cursor and event regressions instead of moving state backwards', () => {
    const first = applyActivityEvent(createActivityState(), event('event-2', '2'))
    const olderEvent = applyActivityEvent(first, event('event-1', '1'))
    const olderHeartbeat = activityReducer(olderEvent, { type: 'cursor', cursor: '1' })

    expect(olderEvent.events).toHaveLength(1)
    expect(olderEvent.cursor).toBe('2')
    expect(olderHeartbeat.cursor).toBe('2')
  })

  it('enters explicit resync state without fabricating an activity', () => {
    const state = activityReducer(createActivityState(), { type: 'resync' })

    expect(state.connection).toBe('resyncing')
    expect(state.events).toEqual([])
    expect(state.resyncRequired).toBe(true)
  })

  it('keeps an unrecognized event renderable instead of dropping the stream', () => {
    const parsed = parseConversationActivityEvent(
      { event_id: 'activity:turn-1:9', event_type: 'future.event', summary: 'Algo novo', payload: { unexpected: true }, agent_id: 'agent:c:main' },
      'a.cursor',
    )

    expect(parsed).toMatchObject({ eventId: 'activity:turn-1:9', type: 'future.event', kind: 'lifecycle', summary: 'Algo novo' })
  })

  it('normalizes a tool event into the fields the activity UI groups on', () => {
    const parsed = parseConversationActivityEvent({
      event_id: 'activity:turn-1:5',
      event_type: 'tool.finished',
      summary: 'Leu hello.txt',
      agent_id: 'agent:c:main',
      payload: { tool_name: 'read_file', tool_kind: 'filesystem', status: 'succeeded', invocation_id: 'call-1', label: 'hello.txt' },
    }, 'a.cursor')

    expect(parsed).toMatchObject({
      kind: 'tool', state: 'completed', toolName: 'read_file', toolKind: 'filesystem',
      invocationId: 'call-1', label: 'hello.txt',
    })
  })

  it('keeps a bounded private browser screenshot path and rejects traversal', () => {
    const accepted = parseConversationActivityEvent({
      event_id: 'browser-1', event_type: 'tool.finished', sequence: 1, summary: 'Abriu example.com',
      payload: { tool_name: 'browse_page', tool_kind: 'browser', status: 'succeeded', screenshot_path: 'browser-captures/one.png' },
    }, 'a.1')
    const rejected = parseConversationActivityEvent({
      event_id: 'browser-2', event_type: 'tool.finished', sequence: 2, summary: 'Abriu example.com',
      payload: { tool_name: 'browse_page', tool_kind: 'browser', status: 'succeeded', screenshot_path: '../secret.png' },
    }, 'a.2')

    expect(accepted.toolKind).toBe('browser')
    expect(accepted.screenshotPath).toBe('browser-captures/one.png')
    expect(rejected.screenshotPath).toBeUndefined()
  })

  it('marks a failed tool so the card can surface it', () => {
    const parsed = parseConversationActivityEvent({
      event_id: 'activity:turn-1:6', event_type: 'tool.finished', summary: 'run_command falhou', agent_id: 'agent:c:main',
      payload: { tool_name: 'run_command', tool_kind: 'terminal', status: 'failed', error_code: 'TOOL_FAILED' },
    }, 'a.cursor')

    expect(parsed.state).toBe('failed')
    expect(parsed.errorCode).toBe('TOOL_FAILED')
  })

  it('carries streamed assistant text on a delta event', () => {
    const parsed = parseConversationActivityEvent({
      event_id: 'activity:turn-1:8', event_type: 'assistant.delta', summary: 'Resposta em andamento', agent_id: 'agent:c:main',
      payload: { content: 'texto parcial', message_id: 'msg-1' },
    }, 'a.cursor')

    expect(parsed).toMatchObject({ kind: 'message', content: 'texto parcial', messageId: 'msg-1' })
  })

  it('recognizes a cancelled turn as its own terminal state', () => {
    const parsed = parseConversationActivityEvent({
      event_id: 'activity:turn-1:9', event_type: 'turn.failed', summary: 'Execução cancelada', agent_id: 'agent:c:main',
      payload: { state: 'cancelled', error_code: 'TURN_CANCELLED' },
    }, 'a.cursor')

    expect(parsed.state).toBe('cancelled')
  })

  it('hydrates activities and the cursor from a conversation snapshot', () => {
    const snapshot = parseConversation({
      conversation_id: 'conversation-1', title: 'Teste', state: 'running', provider: 'openrouter', model_id: 'model',
      messages: [], turns: [], activity_cursor: 'a.cursor',
      has_older_activities: true,
      oldest_activity_id: 42,
      activities: [{ event_id: 'activity:turn-1:1', event_type: 'turn.started', summary: 'Turn started', agent_id: 'agent:c:main', payload: {}, cursor: 'a.cursor' }],
    })

    expect(snapshot.activity_cursor).toBe('a.cursor')
    expect(snapshot.has_older_activities).toBe(true)
    expect(snapshot.oldest_activity_id).toBe(42)
    expect(snapshot.activities).toHaveLength(1)
    expect(snapshot.activities[0]).toMatchObject({ eventId: 'activity:turn-1:1', state: 'working' })
  })

  it('prepends older lazy-loaded activities in chronological order without duplicates', () => {
    const initial = activityReducer(createActivityState(), {
      type: 'snapshot',
      cursor: '10',
      events: [event('event-3', '3'), event('event-4', '4', 'turn.completed')],
    })
    const prepended = activityReducer(initial, {
      type: 'prepend_older',
      events: [event('event-1', '1'), event('event-2', '2'), event('event-3', '3')],
    })

    expect(prepended.events.map((item) => item.eventId)).toEqual(['event-1', 'event-2', 'event-3', 'event-4'])
    expect(prepended.events[0].state).toBe('completed')
  })

  it('ignores literal [REDACTED] stdout/stderr and falls back to output_tail while suppressing exitCode 0 on failed tools', () => {
    const redactedEvent = parseConversationActivityEvent(
      {
        event_id: 'evt-redacted-1',
        event_type: 'tool.finished',
        summary: '$ npm test',
        agent_id: 'agent:c:main',
        payload: {
          tool_name: 'run_command',
          status: 'succeeded',
          command: 'npm test',
          stdout: '[REDACTED]',
          stderr: '[REDACTED]',
          output_tail: 'PASS tests/unit/app.test.ts',
          exit_code: 0,
        },
      },
      'a.11',
    )
    expect(redactedEvent.outputTail).toBe('PASS tests/unit/app.test.ts')
    expect(redactedEvent.bashOutput?.stdout).toBe('PASS tests/unit/app.test.ts')
    expect(redactedEvent.bashOutput?.stderr).toBeUndefined()

    const cancelledCmd = parseConversationActivityEvent(
      {
        event_id: 'evt-cancel-cmd',
        event_type: 'tool.finished',
        summary: 'run_command falhou',
        agent_id: 'agent:c:main',
        payload: {
          tool_name: 'run_command',
          status: 'failed',
          error_code: 'COMMAND_CANCELLED_INTERACTIVE',
          command: 'npx create-vite@latest',
          exit_code: 0,
        },
      },
      'a.12',
    )
    expect(cancelledCmd.state).toBe('failed')
    expect(cancelledCmd.exitCode).toBeUndefined()
    expect(cancelledCmd.bashOutput?.exit_code).toBeUndefined()
  })

  it('keeps pending and in_progress todos as pending when a turn fails or is blocked even if turn.completed follows', () => {
    let state = applyActivityEvent(createActivityState(), {
      eventId: 'evt-todo-init',
      cursor: '1',
      turnId: 'turn-fail-1',
      type: 'todo.updated',
      kind: 'tool',
      state: 'working',
      agentId: 'agent:c:main',
      summary: 'Updated todos',
      todos: [
        { id: '1', content: 'Done step', status: 'completed' },
        { id: '2', content: 'Running step', status: 'in_progress' },
        { id: '3', content: 'Pending step', status: 'pending' },
      ],
    })

    state = applyActivityEvent(state, {
      eventId: 'evt-blocked',
      cursor: '2',
      turnId: 'turn-fail-1',
      type: 'code_mode.blocked',
      kind: 'lifecycle',
      state: 'failed',
      agentId: 'agent:c:main',
      summary: 'Blocked by tool failure',
    })
    expect(state.todos.map((t) => t.status)).toEqual(['completed', 'pending', 'pending'])

    state = applyActivityEvent(state, {
      eventId: 'evt-turn-complete-after-blocked',
      cursor: '3',
      turnId: 'turn-fail-1',
      type: 'turn.completed',
      kind: 'lifecycle',
      state: 'completed',
      agentId: 'agent:c:main',
      summary: 'Turn ended',
    })
    expect(state.status).toBe('failed')
    expect(state.todos.map((t) => t.status)).toEqual(['completed', 'pending', 'pending'])
  })
})
