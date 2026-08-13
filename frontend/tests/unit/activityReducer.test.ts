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
      activities: [{ event_id: 'activity:turn-1:1', event_type: 'turn.started', summary: 'Turn started', agent_id: 'agent:c:main', payload: {}, cursor: 'a.cursor' }],
    })

    expect(snapshot.activity_cursor).toBe('a.cursor')
    expect(snapshot.activities).toHaveLength(1)
    expect(snapshot.activities[0]).toMatchObject({ eventId: 'activity:turn-1:1', state: 'working' })
  })
})
