import { describe, expect, it } from 'vitest'
import { trimActivityEvents } from '../activityReducer'
import { buildTurnTimeline } from '../turnTimelineFold'
import type { ConversationActivityEvent } from '../activityTypes'

function makeEvent(partial: Partial<ConversationActivityEvent> & { eventId: string; type: ConversationActivityEvent['type'] }): ConversationActivityEvent {
  return {
    cursor: partial.cursor ?? 'a.1',
    turnId: partial.turnId ?? 'turn-1',
    kind: partial.kind ?? 'tool',
    state: partial.state ?? 'completed',
    agentId: partial.agentId ?? 'agent:main',
    summary: partial.summary ?? '',
    ...partial,
  }
}

describe('turnTimelineFold — multi-subagent interleaving with Tier 1 / Tier 2 snapshots', () => {
  it('renders interleaved text blocks and subagent cards when subagents have only message_sent + message_received or partial Tier 2 tools', () => {
    const events: ConversationActivityEvent[] = [
      // Round 1 narration
      makeEvent({
        eventId: 'delta-1',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        messageId: 'msg-1',
        content: 'Rodada 1: disparando planejador e revisor.',
      }),
      // Subagent A: Tier 1 milestones ONLY (no Tier 2 tool events survived)
      makeEvent({
        eventId: 'sent-a',
        type: 'agent.message_sent',
        kind: 'agent',
        state: 'completed',
        agentId: 'agent:main',
        summary: 'Enviou tarefa para Planejador',
        payload: { to_agent_id: 'agent:sub-a', agent_name: 'Planejador' },
      }),
      makeEvent({
        eventId: 'recv-a',
        type: 'agent.message_received',
        kind: 'agent',
        state: 'completed',
        agentId: 'agent:sub-a',
        agentName: 'Planejador',
        parentAgentId: 'agent:main',
        summary: 'Recebeu resposta de Planejador',
        payload: { from_agent_id: 'agent:sub-a', agent_name: 'Planejador' },
      }),
      // Round 2 narration
      makeEvent({
        eventId: 'delta-2',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        messageId: 'msg-1',
        content: 'Rodada 2: executando revisor isolado com 2 ações Tier 2.',
      }),
      // Subagent B: message_sent + last 2 Tier 2 tool.finished + message_received
      makeEvent({
        eventId: 'sent-b',
        type: 'agent.message_sent',
        kind: 'agent',
        state: 'completed',
        agentId: 'agent:main',
        summary: 'Enviou tarefa para Revisor',
        payload: { to_agent_id: 'agent:sub-b', agent_name: 'Revisor' },
      }),
      makeEvent({
        eventId: 'tool-b-1',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:sub-b',
        agentName: 'Revisor',
        parentAgentId: 'agent:main',
        toolName: 'read_file',
        path: 'docs/PLAN.md',
        summary: 'Leu docs/PLAN.md',
      }),
      makeEvent({
        eventId: 'tool-b-2',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:sub-b',
        agentName: 'Revisor',
        parentAgentId: 'agent:main',
        toolName: 'search_files',
        summary: 'Buscou contratos',
      }),
      makeEvent({
        eventId: 'recv-b',
        type: 'agent.message_received',
        kind: 'agent',
        state: 'completed',
        agentId: 'agent:sub-b',
        agentName: 'Revisor',
        parentAgentId: 'agent:main',
        summary: 'Recebeu resposta de Revisor',
        payload: { from_agent_id: 'agent:sub-b', agent_name: 'Revisor' },
      }),
      // Round 3 narration + failed subagent
      makeEvent({
        eventId: 'delta-3',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        messageId: 'msg-1',
        content: 'Rodada 3: verificando build.',
      }),
      makeEvent({
        eventId: 'sent-c',
        type: 'agent.message_sent',
        kind: 'agent',
        state: 'completed',
        agentId: 'agent:main',
        summary: 'Enviou tarefa para Verificador',
        payload: { to_agent_id: 'agent:sub-c', agent_name: 'Verificador' },
      }),
      makeEvent({
        eventId: 'fail-c',
        type: 'delegation.failed',
        kind: 'agent',
        state: 'failed',
        agentId: 'agent:sub-c',
        agentName: 'Verificador',
        parentAgentId: 'agent:main',
        summary: 'Verificador falhou',
        payload: { agent_id: 'agent:sub-c', agent_name: 'Verificador' },
      }),
      // Final summary
      makeEvent({
        eventId: 'delta-4',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        messageId: 'msg-1',
        content: 'Resumo final concluído.',
      }),
      makeEvent({
        eventId: 'turn-done',
        type: 'turn.completed',
        kind: 'lifecycle',
        state: 'completed',
        agentId: 'agent:main',
        summary: 'Concluído',
      }),
    ]

    const completeContent =
      'Rodada 1: disparando planejador e revisor.' +
      'Rodada 2: executando revisor isolado com 2 ações Tier 2.' +
      'Rodada 3: verificando build.' +
      'Resumo final concluído.'

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1', completeContent, true)

    // All 4 text blocks remain separated instead of collapsing into one wall of text
    const textItems = timeline.filter((item) => item.kind === 'text')
    expect(textItems.map((item) => item.content)).toEqual([
      'Rodada 1: disparando planejador e revisor.',
      'Rodada 2: executando revisor isolado com 2 ações Tier 2.',
      'Rodada 3: verificando build.',
      'Resumo final concluído.',
    ])

    // Check subagent tool-wave cards exist between the text blocks with right state and counts
    const subAIdx = timeline.findIndex(
      (item) => item.kind === 'activity' && item.group.kind === 'tool' && item.group.agentId === 'agent:sub-a',
    )
    const subBIdx = timeline.findIndex(
      (item) => item.kind === 'activity' && item.group.kind === 'tool' && item.group.agentId === 'agent:sub-b',
    )
    const subCIdx = timeline.findIndex(
      (item) => item.kind === 'activity' && item.group.kind === 'tool' && item.group.agentId === 'agent:sub-c',
    )
    const text1Idx = timeline.findIndex((item) => item.kind === 'text' && item.content.startsWith('Rodada 1'))
    const text2Idx = timeline.findIndex((item) => item.kind === 'text' && item.content.startsWith('Rodada 2'))
    const text3Idx = timeline.findIndex((item) => item.kind === 'text' && item.content.startsWith('Rodada 3'))
    const text4Idx = timeline.findIndex((item) => item.kind === 'text' && item.content.startsWith('Resumo final'))

    expect(text1Idx).toBeLessThan(subAIdx)
    expect(subAIdx).toBeLessThan(text2Idx)
    expect(text2Idx).toBeLessThan(subBIdx)
    expect(subBIdx).toBeLessThan(text3Idx)
    expect(text3Idx).toBeLessThan(subCIdx)
    expect(subCIdx).toBeLessThan(text4Idx)

    const cardA = timeline[subAIdx]!
    const cardB = timeline[subBIdx]!
    const cardC = timeline[subCIdx]!
    expect(cardA.kind === 'activity' && cardA.group.state).toBe('completed')
    expect(cardA.kind === 'activity' && cardA.group.failed).toBe(false)
    expect(cardA.kind === 'activity' && cardA.group.count).toBe(0)

    expect(cardB.kind === 'activity' && cardB.group.state).toBe('completed')
    expect(cardB.kind === 'activity' && cardB.group.failed).toBe(false)
    expect(cardB.kind === 'activity' && cardB.group.count).toBe(2)

    expect(cardC.kind === 'activity' && cardC.group.state).toBe('failed')
    expect(cardC.kind === 'activity' && cardC.group.failed).toBe(true)
  })
})

describe('activityReducer.trimActivityEvents — noise-first eviction', () => {
  it('drops tool.requested, superseded heartbeat turn.started, and superseded agent.progress before touching structural events', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({
        eventId: 'delta-p1',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        messageId: 'msg-1',
        content: 'Parágrafo inicial.',
      }),
      makeEvent({
        eventId: 'ts-structural-queue',
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        summary: 'Mensagem da fila entregue',
        payload: { queue_id: 'q-1' },
      }),
    ]

    // Add heavy live noise: tool.requested, heartbeat turn.started, agent.progress
    for (let i = 0; i < 30; i += 1) {
      events.push(
        makeEvent({
          eventId: `req-${i}`,
          type: 'tool.requested',
          kind: 'tool',
          state: 'waiting_tool',
          agentId: 'agent:sub-1',
          parentAgentId: 'agent:main',
          summary: `Solicitou tool ${i}`,
        }),
        makeEvent({
          eventId: `hb-${i}`,
          type: 'turn.started',
          kind: 'lifecycle',
          state: 'working',
          agentId: 'agent:sub-1',
          parentAgentId: 'agent:main',
          summary: `sub-1 trabalhando ${i}`,
          payload: { state: 'working', seconds: i * 10 },
        }),
        makeEvent({
          eventId: `prog-${i}`,
          type: 'agent.progress',
          kind: 'agent',
          state: 'working',
          agentId: 'agent:sub-1',
          parentAgentId: 'agent:main',
          summary: `sub-1 passo ${i}`,
          payload: { tool_calls: i },
        }),
      )
    }

    events.push(
      makeEvent({
        eventId: 'tool-fin-1',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        toolName: 'read_file',
        summary: 'Leu README.md',
      }),
      makeEvent({
        eventId: 'delta-p2',
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        messageId: 'msg-1',
        content: 'Parágrafo final.',
      }),
    )

    const trimmed = trimActivityEvents(events, 10)
    const ids = new Set(trimmed.map((ev) => ev.eventId))

    // Structural events and paragraphs survive intact
    expect(ids.has('delta-p1')).toBe(true)
    expect(ids.has('ts-structural-queue')).toBe(true)
    expect(ids.has('tool-fin-1')).toBe(true)
    expect(ids.has('delta-p2')).toBe(true)

    // Latest live heartbeat and latest agent.progress for sub-1 survive
    expect(ids.has('hb-29')).toBe(true)
    expect(ids.has('prog-29')).toBe(true)

    // All tool.requested and superseded heartbeats/progress were dropped
    expect(trimmed.some((ev) => ev.type === 'tool.requested')).toBe(false)
    expect(trimmed.filter((ev) => ev.type === 'agent.progress')).toHaveLength(1)
    expect(
      trimmed.filter((ev) => ev.type === 'turn.started' && ev.agentId === 'agent:sub-1'),
    ).toHaveLength(1)
    expect(trimmed.length).toBeLessThanOrEqual(10)
  })
})
