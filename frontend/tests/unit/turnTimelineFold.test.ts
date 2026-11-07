import { describe, expect, it } from 'vitest'
import { buildMessageTimelines, buildTurnTimeline, resolveTurnId } from '../../src/features/conversations/turnTimelineFold'
import { kindFor, stateFor, type ConversationActivityEvent } from '../../src/features/conversations/activityTypes'

let sequence = 0

function event(partial: Partial<ConversationActivityEvent> & { type: string }): ConversationActivityEvent {
  sequence += 1
  const toolKind = partial.toolKind
  return {
    eventId: partial.eventId ?? `activity:turn-1:${sequence}`,
    cursor: `a.${sequence}`,
    kind: partial.kind ?? kindFor(partial.type, toolKind),
    state: partial.state ?? stateFor(partial.type, partial.status, partial.errorCode),
    agentId: partial.agentId ?? 'agent:c:main',
    summary: partial.summary ?? partial.type,
    turnId: partial.turnId ?? 'turn-1',
    ...partial,
  }
}

describe('buildTurnTimeline', () => {
  it('interleaves narration text with the action that happened in between', () => {
    const events = [
      event({ type: 'assistant.delta', messageId: 'msg-1', content: 'Vou criar o subagente. ' }),
      event({ type: 'agent.created', label: 'Analista', summary: 'Criou o agente Analista', agentId: 'agent:c:sub' }),
      event({ type: 'assistant.delta', messageId: 'msg-1', content: 'Pronto, agora vou consultar o site.' }),
    ]

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1')

    expect(timeline.map((item) => item.kind)).toEqual(['text', 'activity', 'text'])
    expect(timeline[0]).toMatchObject({ kind: 'text', content: 'Vou criar o subagente. ' })
    if (timeline[1].kind === 'activity') expect(timeline[1].group.label).toBe('Criou o agente Analista')
    expect(timeline[2]).toMatchObject({ kind: 'text', content: 'Pronto, agora vou consultar o site.' })
  })

  it('does not merge two tool cards separated by narration text', () => {
    const events = [
      event({ type: 'tool.started', toolName: 'read_file', toolKind: 'filesystem', invocationId: 'call-1' }),
      event({ type: 'tool.finished', toolName: 'read_file', toolKind: 'filesystem', invocationId: 'call-1', status: 'succeeded', summary: 'Leu a.txt' }),
      event({ type: 'assistant.delta', messageId: 'msg-1', content: 'Agora o outro arquivo.' }),
      event({ type: 'tool.started', toolName: 'read_file', toolKind: 'filesystem', invocationId: 'call-2' }),
      event({ type: 'tool.finished', toolName: 'read_file', toolKind: 'filesystem', invocationId: 'call-2', status: 'succeeded', summary: 'Leu b.txt' }),
    ]

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1')

    expect(timeline.map((item) => item.kind)).toEqual(['activity', 'text', 'activity'])
    if (timeline[0].kind === 'activity') expect(timeline[0].group.count).toBe(1)
    if (timeline[2].kind === 'activity') expect(timeline[2].group.count).toBe(1)
  })

  it('pins one Code mode card at its first event while later stages update it', () => {
    const events = [
      event({ type: 'code_mode.activated', codeStage: 'planning', summary: 'Modo Code ativado' }),
      event({ type: 'tool.finished', toolName: 'write_file', toolKind: 'filesystem', invocationId: 'plan', status: 'succeeded', summary: 'Plano salvo' }),
      event({ type: 'code_mode.plan_ready', codeStage: 'planning', summary: 'Plano pronto' }),
      event({ type: 'code_mode.validation_started', codeStage: 'validating', summary: 'Executando validação' }),
    ]

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1')
    const codeItems = timeline.filter((item) => item.kind === 'activity' && item.group.id === 'code-mode:turn-1')
    expect(codeItems).toHaveLength(1)
    if (codeItems[0]?.kind === 'activity') expect(codeItems[0].group.events.at(-1)?.codeStage).toBe('validating')
  })

  it('ignores events from other turns', () => {
    const events = [
      event({ type: 'assistant.delta', messageId: 'msg-1', content: 'Deste turno.' }),
      event({ type: 'assistant.delta', messageId: 'msg-9', content: 'De outro turno.', turnId: 'turn-9' }),
    ]

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1')

    expect(timeline).toEqual([{ id: expect.any(String), kind: 'text', content: 'Deste turno.' }])
  })

  it('prepends text that fell out of the bounded activity window', () => {
    const events = [
      event({ type: 'assistant.delta', messageId: 'msg-1', content: 'parte-2' }),
      event({ type: 'tool.finished', toolName: 'read_file', toolKind: 'filesystem', invocationId: 'call-1', status: 'succeeded', summary: 'Leu a.txt' }),
      event({ type: 'assistant.delta', messageId: 'msg-1', content: 'parte-3' }),
    ]

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1', 'parte-1parte-2parte-3')

    expect(timeline[0]).toMatchObject({ kind: 'text', content: 'parte-1parte-2' })
    expect(timeline[1].kind).toBe('activity')
    expect(timeline[2]).toMatchObject({ kind: 'text', content: 'parte-3' })
  })

  it('drops orphaned Preparando (generating_arguments) tool.progressed placeholders when turnCompleted is true', () => {
    const events = [
      event({
        eventId: 'evt-read-done',
        type: 'tool.finished',
        toolName: 'read_file',
        toolKind: 'filesystem',
        invocationId: 'call-1',
        status: 'succeeded',
        summary: 'Leu Dashboard.tsx',
      }),
      event({
        eventId: 'evt-prep-orphan',
        type: 'tool.progressed',
        toolName: 'read_file',
        toolKind: 'filesystem',
        invocationId: 'conv:99',
        phase: 'generating_arguments',
        summary: 'Preparando read_file...',
      }),
      event({
        type: 'assistant.delta',
        messageId: 'msg-1',
        content: 'Análise concluída.',
      }),
    ]

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1', 'Análise concluída.', true)
    const activityItems = timeline.filter((item) => item.kind === 'activity')
    expect(activityItems).toHaveLength(1)
    if (activityItems[0]?.kind === 'activity') {
      expect(activityItems[0].group.label).toBe('Leu Dashboard.tsx')
      expect(
        activityItems[0].group.events.some((e) => (e.summary ?? '').startsWith('Preparando')),
      ).toBe(false)
    }
  })

  it('settles Preparando (generating_arguments) when tool.started/finished uses a different invocationId for the same toolName', async () => {
    const { summarizeActivities } = await import('../../src/features/conversations/activitySummary')
    const events = [
      event({
        eventId: 'evt-prep-mismatch',
        type: 'tool.progressed',
        toolName: 'read_file',
        toolKind: 'filesystem',
        invocationId: 'item_codex_1',
        phase: 'generating_arguments',
        summary: 'Preparando read_file...',
      }),
      event({
        eventId: 'evt-start-mcp',
        type: 'tool.started',
        toolName: 'read_file',
        toolKind: 'filesystem',
        invocationId: 'mcp_1_1',
        summary: 'Lendo Dashboard.tsx',
      }),
      event({
        eventId: 'evt-finish-mcp',
        type: 'tool.finished',
        toolName: 'read_file',
        toolKind: 'filesystem',
        invocationId: 'mcp_1_1',
        status: 'succeeded',
        summary: 'Leu Dashboard.tsx',
      }),
    ]

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1', '', false)
    const activityItems = timeline.filter((item) => item.kind === 'activity')
    expect(activityItems).toHaveLength(1)
    if (activityItems[0]?.kind === 'activity') {
      expect(activityItems[0].group.label).toBe('Leu Dashboard.tsx')
      expect(activityItems[0].group.count).toBe(1)
    }

    const summaryGroups = summarizeActivities(events)
    expect(summaryGroups).toHaveLength(1)
    expect(summaryGroups[0].label).toBe('Leu Dashboard.tsx')
    expect(summaryGroups[0].count).toBe(1)
  })
})

describe('resolveTurnId', () => {
  it("finds the turn from the message's own delta event", () => {
    const events = [event({ type: 'assistant.delta', messageId: 'msg-1', content: 'oi', turnId: 'turn-7' })]
    expect(resolveTurnId(events, 'msg-1')).toBe('turn-7')
  })

  it('returns null when no event names this message', () => {
    const events = [event({ type: 'assistant.delta', messageId: 'msg-2', content: 'oi' })]
    expect(resolveTurnId(events, 'msg-1')).toBeNull()
  })
})

describe('buildMessageTimelines', () => {
  it('claims a turn only for the message whose timeline is non-empty', () => {
    const events = [
      event({ type: 'assistant.delta', messageId: 'msg-1', content: 'Oi' }),
      event({ type: 'tool.finished', toolName: 'read_file', toolKind: 'filesystem', invocationId: 'call-1', status: 'succeeded', summary: 'Leu a.txt', turnId: 'turn-2' }),
    ]
    const messages = [
      { message_id: 'msg-1', role: 'assistant' as const },
      { message_id: 'msg-0', role: 'user' as const },
    ]

    const { timelines, claimedTurnIds } = buildMessageTimelines(messages, events)

    expect(timelines.get('msg-1')?.map((item) => item.kind)).toEqual(['text'])
    expect(claimedTurnIds).toEqual(new Set(['turn-1']))
  })
})
