import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { ConversationActivityEvent } from '../activityTypes'
import { summarizeActivities } from '../activitySummary'
import { buildTurnTimeline } from '../turnTimelineFold'
import { TurnTimeline } from '../TurnTimeline'

afterEach(() => {
  cleanup()
})

const MAIN = 'agent:main'

function makeMessageSent(
  seq: number,
  toAgentId: string,
  agentName: string,
): ConversationActivityEvent {
  return {
    eventId: `sent-${seq}`,
    cursor: String(seq),
    turnId: 'turn-1',
    agentId: MAIN,
    agentName,
    label: agentName,
    kind: 'agent',
    type: 'agent.message_sent',
    state: 'waiting_agent',
    summary: `Enviou uma tarefa para ${agentName}`,
    content: `Tarefa para ${agentName}`,
    payload: {
      to_agent_id: toAgentId,
      agent_name: agentName,
      recipient_name: agentName,
      sender_name: 'Main',
      label: agentName,
      tool_kind: 'agent',
    },
    occurredAt: `2026-11-15T12:00:${String(seq).padStart(2, '0')}Z`,
  }
}

function makeSubagentTool(
  seq: number,
  agentId: string,
  agentName: string,
  overrides: Partial<ConversationActivityEvent> = {},
): ConversationActivityEvent {
  return {
    eventId: `tool-${seq}`,
    cursor: String(seq),
    turnId: 'turn-1',
    agentId,
    agentName,
    parentAgentId: MAIN,
    kind: 'tool',
    type: 'tool.finished',
    state: 'completed',
    status: 'succeeded',
    toolKind: 'filesystem',
    toolName: 'read_file',
    path: `src/${agentName}.ts`,
    summary: `Leu src/${agentName}.ts`,
    occurredAt: `2026-11-15T12:00:${String(seq).padStart(2, '0')}Z`,
    ...overrides,
  }
}

describe('TurnTimeline parallel subagents immediate spawn & wave preservation', () => {
  it('renders 3 running SubagentTaskContainer cards inside ParallelSubagentsGroup immediately on 3 agent.message_sent with to_agent_id before any tool calls', () => {
    const events: ConversationActivityEvent[] = [
      makeMessageSent(1, 'agent:sub-1', 'antigravity-1'),
      makeMessageSent(2, 'agent:sub-2', 'antigravity-2'),
      makeMessageSent(3, 'agent:sub-3', 'antigravity-3'),
    ]

    const items = buildTurnTimeline(events, 'turn-1', 'msg-1', '', false)
    render(<TurnTimeline items={items} events={events} mode="code" turnCompleted={false} />)

    const groups = screen.getAllByTestId('parallel-subagents-group')
    expect(groups).toHaveLength(1)
    expect(groups[0].getAttribute('data-count')).toBe('3')
    expect(groups[0].textContent).toContain('3 subagentes em paralelo')
    expect(groups[0].textContent).toContain('Em execução')

    const cards = within(groups[0]).getAllByTestId('subagent-task-group')
    expect(cards).toHaveLength(3)
    expect(cards.map((c) => c.getAttribute('data-agent-id'))).toEqual([
      'agent:sub-1',
      'agent:sub-2',
      'agent:sub-3',
    ])
    for (const card of cards) {
      expect(card.getAttribute('data-status')).toBe('running')
      expect(card.textContent).toContain('0 ações')
      expect(card.textContent).toContain('Em execução')
      expect(card.textContent).toContain('Iniciando subagente…')
    }
  })

  it('reuses the exact same SubagentTaskContainer cards when turn.started and 1st tool calls arrive (never duplicating cards)', () => {
    const spawnOnly: ConversationActivityEvent[] = [
      makeMessageSent(1, 'agent:sub-1', 'antigravity-1'),
      makeMessageSent(2, 'agent:sub-2', 'antigravity-2'),
      makeMessageSent(3, 'agent:sub-3', 'antigravity-3'),
    ]

    const withTurnStartedAndTools: ConversationActivityEvent[] = [
      ...spawnOnly,
      {
        eventId: 'sub1-turn-started',
        cursor: '4',
        turnId: 'turn-1',
        agentId: 'agent:sub-1',
        agentName: 'antigravity-1',
        parentAgentId: MAIN,
        kind: 'lifecycle',
        type: 'turn.started',
        state: 'working',
        status: 'running',
        summary: 'Iniciando turno do subagente',
      },
      makeSubagentTool(5, 'agent:sub-1', 'antigravity-1', {
        type: 'tool.started',
        state: 'waiting_tool',
        status: 'running',
        summary: 'Lendo src/antigravity-1.ts',
      }),
      makeSubagentTool(6, 'agent:sub-2', 'antigravity-2'),
    ]

    const { rerender } = render(
      <TurnTimeline
        items={buildTurnTimeline(spawnOnly, 'turn-1', 'msg-1', '', false)}
        events={spawnOnly}
        mode="code"
        turnCompleted={false}
      />,
    )

    expect(screen.getAllByTestId('subagent-task-group')).toHaveLength(3)

    rerender(
      <TurnTimeline
        items={buildTurnTimeline(withTurnStartedAndTools, 'turn-1', 'msg-1', '', false)}
        events={withTurnStartedAndTools}
        mode="code"
        turnCompleted={false}
      />,
    )

    const cards = screen.getAllByTestId('subagent-task-group')
    expect(cards).toHaveLength(3)
    expect(cards[0].getAttribute('data-agent-id')).toBe('agent:sub-1')
    expect(cards[0].getAttribute('data-status')).toBe('running')
    expect(cards[0].textContent).toContain('1 ação')
    expect(cards[0].textContent).not.toContain('Iniciando subagente…')

    expect(cards[1].getAttribute('data-agent-id')).toBe('agent:sub-2')
    expect(cards[1].getAttribute('data-status')).toBe('completed')
    expect(cards[1].textContent).toContain('1 ação')

    expect(cards[2].getAttribute('data-agent-id')).toBe('agent:sub-3')
    expect(cards[2].getAttribute('data-status')).toBe('running')
    expect(cards[2].textContent).toContain('0 ações')
    expect(cards[2].textContent).toContain('Iniciando subagente…')

    // Also verify summarizeActivities path (when events prop is used directly)
    const summaryGroups = summarizeActivities(withTurnStartedAndTools)
    const subGroups = summaryGroups.filter((g) => g.id.startsWith('tool:agent:sub-'))
    expect(subGroups).toHaveLength(3)
    expect(subGroups.map((g) => g.count)).toEqual([1, 1, 0])
  })

  it('does not fragment ParallelSubagentsGroup when main agent emits check_subagent, waiting_subagents, waiting_model, thought_delta, or context.updated, and closes wave on visible main text', () => {
    const events: ConversationActivityEvent[] = [
      makeMessageSent(1, 'agent:sub-1', 'antigravity-1'),
      makeMessageSent(2, 'agent:sub-2', 'antigravity-2'),
      makeMessageSent(3, 'agent:sub-3', 'antigravity-3'),
      // Main agent thought delta while waiting
      {
        eventId: 'main-thought-1',
        cursor: '4',
        turnId: 'turn-1',
        agentId: MAIN,
        kind: 'lifecycle',
        type: 'agent.thought_delta',
        state: 'working',
        detail: 'Aguardando os 3 subagentes terminarem...',
        payload: { delta: 'Aguardando os 3 subagentes terminarem...' },
        summary: 'Pensando',
        occurredAt: '2026-11-15T12:00:04Z',
      },
      // Main agent context.updated
      {
        eventId: 'main-ctx-1',
        cursor: '5',
        turnId: 'turn-1',
        agentId: MAIN,
        kind: 'lifecycle',
        type: 'context.updated',
        state: 'working',
        summary: 'Contexto atualizado',
      },
      // Main agent waiting_model
      {
        eventId: 'main-wait-model',
        cursor: '6',
        turnId: 'turn-1',
        agentId: MAIN,
        kind: 'tool',
        type: 'tool.progressed',
        state: 'working',
        status: 'running',
        phase: 'waiting_model',
        payload: { phase: 'waiting_model', waiting_model: true },
        summary: 'pensando há 15 s',
      },
      // Main agent waiting_subagents lifecycle
      {
        eventId: 'main-wait-subs',
        cursor: '7',
        turnId: 'turn-1',
        agentId: MAIN,
        kind: 'lifecycle',
        type: 'turn.started',
        state: 'waiting_agent',
        waitingSubagents: true,
        waitingAgents: ['antigravity-1', 'antigravity-2', 'antigravity-3'],
        payload: { state: 'waiting_subagents', waiting_subagents: true },
        summary: 'Aguardando subagentes',
      },
      // Main agent CheckSubagent tool call
      {
        eventId: 'main-check-sub',
        cursor: '8',
        turnId: 'turn-1',
        agentId: MAIN,
        kind: 'tool',
        type: 'tool.finished',
        state: 'completed',
        status: 'succeeded',
        toolName: 'check_subagent',
        summary: 'Verificou subagentes em background',
      },
      // Subagents emit tool calls AFTER the main agent's coordination/waiting events
      makeSubagentTool(9, 'agent:sub-1', 'antigravity-1'),
      makeSubagentTool(10, 'agent:sub-2', 'antigravity-2'),
      makeSubagentTool(11, 'agent:sub-3', 'antigravity-3'),
      // Main agent emits visible response text -> closes Wave 1
      {
        eventId: 'main-text-1',
        cursor: '12',
        turnId: 'turn-1',
        messageId: 'msg-1',
        agentId: MAIN,
        kind: 'message',
        type: 'assistant.delta',
        state: 'working',
        content: 'Os 3 subagentes da primeira onda concluíram.',
        summary: 'Os 3 subagentes da primeira onda concluíram.',
      },
      // Wave 2 after visible text
      makeMessageSent(13, 'agent:sub-4', 'antigravity-4'),
      makeMessageSent(14, 'agent:sub-5', 'antigravity-5'),
    ]

    const items = buildTurnTimeline(events, 'turn-1', 'msg-1', '', false)
    render(<TurnTimeline items={items} events={events} mode="code" turnCompleted={false} />)

    const parallelGroups = screen.getAllByTestId('parallel-subagents-group')
    expect(parallelGroups).toHaveLength(2)
    expect(parallelGroups[0].getAttribute('data-count')).toBe('3')
    expect(within(parallelGroups[0]).getAllByTestId('subagent-task-group')).toHaveLength(3)

    expect(parallelGroups[1].getAttribute('data-count')).toBe('2')
    expect(within(parallelGroups[1]).getAllByTestId('subagent-task-group')).toHaveLength(2)

    expect(screen.getByText('Os 3 subagentes da primeira onda concluíram.')).toBeInTheDocument()
  })

  it('creates running SubagentTaskContainers on subagent turn.started (with parentAgentId) even without agent.message_sent and keeps wave unified when coordination events fall between spawns', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'sub1-turn-start',
        cursor: '1',
        turnId: 'turn-2',
        agentId: 'agent:sub-1',
        agentName: 'codex-1',
        parentAgentId: MAIN,
        kind: 'lifecycle',
        type: 'turn.started',
        state: 'working',
        status: 'running',
        summary: 'Iniciando codex-1',
      },
      // Interstitial main agent thought_delta + CheckSubagent between sub-1 and sub-2 spawns
      {
        eventId: 'main-thought-between',
        cursor: '2',
        turnId: 'turn-2',
        agentId: MAIN,
        kind: 'lifecycle',
        type: 'agent.thought_delta',
        state: 'working',
        detail: 'Disparando os próximos subagentes...',
        payload: { delta: 'Disparando os próximos subagentes...' },
        summary: 'Pensando',
      },
      {
        eventId: 'main-check-between',
        cursor: '3',
        turnId: 'turn-2',
        agentId: MAIN,
        kind: 'tool',
        type: 'tool.finished',
        state: 'completed',
        status: 'succeeded',
        toolName: 'CheckSubagent',
        summary: 'CheckSubagent',
      },
      {
        eventId: 'sub2-turn-start',
        cursor: '4',
        turnId: 'turn-2',
        agentId: 'agent:sub-2',
        agentName: 'codex-2',
        parentAgentId: MAIN,
        kind: 'lifecycle',
        type: 'turn.started',
        state: 'working',
        status: 'running',
        summary: 'Iniciando codex-2',
      },
      {
        eventId: 'sub3-turn-start',
        cursor: '5',
        turnId: 'turn-2',
        agentId: 'agent:sub-3',
        agentName: 'codex-3',
        parentAgentId: MAIN,
        kind: 'lifecycle',
        type: 'turn.started',
        state: 'working',
        status: 'running',
        summary: 'Iniciando codex-3',
      },
    ]

    // Render using events prop directly (exercises summarizeActivities)
    render(<TurnTimeline events={events} mode="code" turnCompleted={false} />)

    const groups = screen.getAllByTestId('parallel-subagents-group')
    expect(groups).toHaveLength(1)
    expect(groups[0].getAttribute('data-count')).toBe('3')

    const cards = within(groups[0]).getAllByTestId('subagent-task-group')
    expect(cards).toHaveLength(3)
    expect(cards.map((c) => c.getAttribute('data-agent-name'))).toEqual([
      'codex-1',
      'codex-2',
      'codex-3',
    ])
    for (const card of cards) {
      expect(card.getAttribute('data-status')).toBe('running')
      expect(card.textContent).toContain('0 ações')
      expect(card.textContent).toContain('Iniciando subagente…')
    }

    // Achado #5: The interstitial ThoughtBlock between sub-1 and sub-2 is preserved in the DOM
    expect(screen.getByTestId('thought-block-completed')).toBeInTheDocument()
  })

  it('preserves ThoughtBlock in the DOM and starts a separate 2nd ParallelSubagentsGroup when Wave 1 is already settled before main agent thinks and spawns Wave 2', () => {
    const events: ConversationActivityEvent[] = [
      // Wave 1: sub-1 and sub-2 spawn and complete
      makeMessageSent(1, 'agent:sub-1', 'antigravity-1'),
      makeMessageSent(2, 'agent:sub-2', 'antigravity-2'),
      makeSubagentTool(3, 'agent:sub-1', 'antigravity-1'),
      makeSubagentTool(4, 'agent:sub-2', 'antigravity-2'),
      {
        eventId: 'w1-sub1-recv',
        cursor: '5',
        turnId: 'turn-1',
        agentId: MAIN,
        kind: 'agent',
        type: 'agent.message_received',
        state: 'completed',
        label: 'antigravity-1',
        payload: { from_agent_id: 'agent:sub-1', sender_name: 'antigravity-1' },
        summary: 'Recebeu a resposta de antigravity-1',
      },
      {
        eventId: 'w1-sub2-recv',
        cursor: '6',
        turnId: 'turn-1',
        agentId: MAIN,
        kind: 'agent',
        type: 'agent.message_received',
        state: 'completed',
        label: 'antigravity-2',
        payload: { from_agent_id: 'agent:sub-2', sender_name: 'antigravity-2' },
        summary: 'Recebeu a resposta de antigravity-2',
      },
      // Main agent thinks WITHOUT emitting assistant.delta text, then spawns Wave 2
      {
        eventId: 'main-thought-wave-sep',
        cursor: '7',
        turnId: 'turn-1',
        agentId: MAIN,
        kind: 'lifecycle',
        type: 'agent.thought_delta',
        state: 'working',
        detail: 'Onda 1 concluída; iniciando Onda 2...',
        payload: { delta: 'Onda 1 concluída; iniciando Onda 2...' },
        summary: 'Pensando',
        occurredAt: '2026-11-15T12:00:07Z',
      },
      makeMessageSent(8, 'agent:sub-3', 'antigravity-3'),
      makeMessageSent(9, 'agent:sub-4', 'antigravity-4'),
    ]

    const items = buildTurnTimeline(events, 'turn-1', 'msg-1', '', false)
    render(<TurnTimeline items={items} events={events} mode="code" turnCompleted={false} />)

    // ThoughtBlock must be present in the DOM
    expect(screen.getByTestId('thought-block-completed')).toBeInTheDocument()

    // Wave 1 (completed) and Wave 2 (running) must be 2 separate ParallelSubagentsGroup containers
    const parallelGroups = screen.getAllByTestId('parallel-subagents-group')
    expect(parallelGroups).toHaveLength(2)
    expect(parallelGroups[0].getAttribute('data-count')).toBe('2')
    expect(parallelGroups[0].textContent).toContain('Concluídos')
    expect(within(parallelGroups[0]).getAllByTestId('subagent-task-group')).toHaveLength(2)

    expect(parallelGroups[1].getAttribute('data-count')).toBe('2')
    expect(parallelGroups[1].textContent).toContain('Em execução')
    expect(within(parallelGroups[1]).getAllByTestId('subagent-task-group')).toHaveLength(2)

    // Also verify when rendered via events prop directly and with turnCompleted={true}
    cleanup()
    render(<TurnTimeline events={events} mode="code" turnCompleted={true} />)
    expect(screen.getByTestId('thought-block-completed')).toBeInTheDocument()
    expect(screen.getAllByTestId('parallel-subagents-group')).toHaveLength(2)
  })

  it('settles open subagent tool.started on delegation.failed/completed, handles parent tool.finished with payload.agent_id, and updates 0-tool placeholder summary', () => {
    const events: ConversationActivityEvent[] = [
      makeMessageSent(1, 'agent:sub-1', 'antigravity-1'),
      makeMessageSent(2, 'agent:sub-2', 'antigravity-2'),
      makeMessageSent(3, 'agent:sub-3', 'antigravity-3'),
      // sub-1 starts a tool that gets aborted without tool.finished before delegation.failed
      makeSubagentTool(4, 'agent:sub-1', 'antigravity-1', {
        type: 'tool.started',
        state: 'waiting_tool',
        status: 'running',
        invocationId: 'inv-aborted-1',
        summary: 'Executando: bash',
      }),
      {
        eventId: 'sub1-delegation-failed',
        cursor: '5',
        turnId: 'turn-1',
        agentId: 'agent:sub-1',
        parentAgentId: MAIN,
        agentName: 'antigravity-1',
        kind: 'agent',
        type: 'delegation.failed',
        state: 'failed',
        status: 'failed',
        label: 'antigravity-1',
        summary: 'antigravity-1 não concluiu a tarefa',
      },
      // sub-2 fails with 0 tools and ONLY parent tool.finished (no delegation.failed event)
      {
        eventId: 'parent-agent-call-sub2-failed',
        cursor: '6',
        turnId: 'turn-1',
        agentId: MAIN,
        kind: 'tool',
        type: 'tool.finished',
        state: 'failed',
        status: 'failed',
        toolName: 'run_subagent',
        payload: {
          agent_id: 'agent:sub-2',
          agent_name: 'antigravity-2',
          status: 'failed',
        },
        summary: 'Subagente falhou',
      },
      // sub-3 completes with 0 tools via delegation.completed
      {
        eventId: 'sub3-delegation-completed',
        cursor: '7',
        turnId: 'turn-1',
        agentId: 'agent:sub-3',
        parentAgentId: MAIN,
        agentName: 'antigravity-3',
        kind: 'agent',
        type: 'delegation.completed',
        state: 'completed',
        status: 'succeeded',
        label: 'antigravity-3',
        summary: 'antigravity-3 concluiu a tarefa',
      },
    ]

    const items = buildTurnTimeline(events, 'turn-1', 'msg-1', '', false)
    render(<TurnTimeline items={items} events={events} mode="code" turnCompleted={false} />)

    // ParallelSubagentsGroup must NOT be stuck in "Em execução" now that all 3 subagents settled
    const parallelGroup = screen.getByTestId('parallel-subagents-group')
    expect(parallelGroup.textContent).toContain('Concluídos')
    expect(parallelGroup.textContent).not.toContain('Em execução')

    const cards = within(parallelGroup).getAllByTestId('subagent-task-group')
    expect(cards).toHaveLength(3)

    // sub-1: failed after aborted tool.started -> status failed, summary is coherent (not stuck running)
    expect(cards[0].getAttribute('data-agent-id')).toBe('agent:sub-1')
    expect(cards[0].getAttribute('data-status')).toBe('failed')
    expect(cards[0].textContent).toContain('Falhou')
    expect(cards[0].textContent).not.toContain('Iniciando subagente…')

    // sub-2: failed via parent tool.finished with payload.agent_id (0 tools)
    expect(cards[1].getAttribute('data-agent-id')).toBe('agent:sub-2')
    expect(cards[1].getAttribute('data-status')).toBe('failed')
    expect(cards[1].textContent).toContain('Falhou')
    expect(cards[1].textContent).not.toContain('Iniciando subagente…')

    // sub-3: completed with 0 tools -> status completed, coherent summary (not "Iniciando subagente…")
    expect(cards[2].getAttribute('data-agent-id')).toBe('agent:sub-3')
    expect(cards[2].getAttribute('data-status')).toBe('completed')
    expect(cards[2].textContent).toContain('Concluído')
    expect(cards[2].textContent).not.toContain('Iniciando subagente…')
  })
})
