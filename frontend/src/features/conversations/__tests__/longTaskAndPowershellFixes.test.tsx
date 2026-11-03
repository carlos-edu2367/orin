import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi, afterEach } from 'vitest';
import { ThemeProvider } from '../../../app/ThemeContext';
import { parseConversationActivityEvent } from '../../../api/conversations';
import { CodeToolCallBlock } from '../CodeToolCallBlock';
import { TeamActivityView } from '../TeamActivityView';
import { CodeWorkbenchPanel } from '../CodeWorkbenchPanel';
import { ChatPage } from '../ChatPage';
import { buildTurnTimeline } from '../turnTimelineFold';
import { computeSettledEventIds } from '../activitySummary';
import { activityReducer, initialActivityState } from '../activityReducer';
import type { ConversationActivityEvent } from '../activityTypes';

describe('Long task reconciliation, powershell tool blocks, and workbench/team counters', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('formats powershell tool signature cleanly without "Executando:" prefix and settles open tool events on completed turns', () => {
    const event: ConversationActivityEvent = {
      eventId: 'evt_3989',
      cursor: 'a.28',
      turnId: 'turn_1',
      agentId: 'agent:conv_1:main',
      occurredAt: '2026-04-20T12:00:00Z',
      kind: 'tool',
      type: 'tool.progressed',
      state: 'waiting_tool',
      status: 'running',
      label: 'PowerShell',
      toolName: 'powershell',
      summary: 'Executando: Find Ashy providers.json, mask API keys',
      command: 'Get-ChildItem providers.json',
      outputTail: 'Found providers.json\n{"openai": "***"}',
    };

    const timeline = buildTurnTimeline([event], true);
    expect(timeline.length).toBeGreaterThan(0);
    const first = timeline[0] as { event?: ConversationActivityEvent; toolCall?: ConversationActivityEvent };
    const settledEvent = first.event ?? first.toolCall;
    expect(settledEvent).toBeDefined();
    expect(settledEvent!.state).toBe('completed');
    expect(settledEvent!.summary).not.toMatch(/^Executando:/i);

    render(<CodeToolCallBlock event={settledEvent!} defaultExpanded={true} />);
    expect(screen.queryByText(/powershell\(Executando:/i)).toBeNull();
    expect(screen.getByText(/Find Ashy providers\.json, mask API keys/i)).toBeTruthy();
    expect(screen.getByText(/Found providers\.json/i)).toBeTruthy();
  });

  it('populates bashOutput.stdout from output_tail for powershell tool events in parseConversationActivityEvent', () => {
    const parsed = parseConversationActivityEvent(
      {
        event_id: 'evt_ps_1',
        event_type: 'tool.progressed',
        sequence: 28,
        summary: 'Executando: Find Ashy providers.json',
        turn_id: 'turn_1',
        agent_id: 'agent:conv_1:main',
        payload: {
          tool_name: 'powershell',
          tool_kind: 'terminal',
          command: 'Get-Content providers.json',
          output_tail: '{"provider":"openai"}',
        },
      },
      'evt_ps_1',
    );

    expect(parsed.toolName).toBe('powershell');
    expect(parsed.bashOutput?.command).toBe('Get-Content providers.json');
    expect(parsed.bashOutput?.stdout).toBe('{"provider":"openai"}');
  });

  it('settles open tool event IDs in computeSettledEventIds when turnCompleted is true', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'evt_start',
        cursor: 'a.1',
        turnId: 'turn_1',
        agentId: 'agent:conv_1:main',
        kind: 'tool',
        type: 'tool.started',
        state: 'waiting_tool',
        toolName: 'powershell',
        invocationId: 'call_1',
        label: 'PowerShell',
        summary: 'PowerShell',
        occurredAt: '2026-04-20T12:00:00Z',
      },
      {
        eventId: 'evt_prog',
        cursor: 'a.2',
        turnId: 'turn_1',
        agentId: 'agent:conv_1:main',
        kind: 'tool',
        type: 'tool.progressed',
        state: 'waiting_tool',
        toolName: 'powershell',
        invocationId: 'call_1',
        label: 'PowerShell',
        summary: 'Executando: Checking status',
        occurredAt: '2026-04-20T12:00:01Z',
      },
    ];

    const openSettled = computeSettledEventIds(events, false);
    expect(openSettled.has('evt_prog')).toBe(false);

    const completedSettled = computeSettledEventIds(events, true);
    expect(completedSettled.has('evt_start')).toBe(true);
    expect(completedSettled.has('evt_prog')).toBe(true);
  });

  it('settles open tool and lifecycle events in activityReducer when turn.completed or reconcile_snapshot arrives and recovers connection to live on events', () => {
    const degradedState = {
      ...initialActivityState(),
      connection: 'degraded' as const,
    };

    const afterProgressed = activityReducer(degradedState, {
      type: 'event',
      event: {
        eventId: 'evt_1',
        cursor: 'a.1',
        turnId: 'turn_1',
        agentId: 'agent:conv_1:main',
        kind: 'tool',
        type: 'tool.progressed',
        state: 'waiting_tool',
        status: 'running',
        toolName: 'powershell',
        label: 'PowerShell',
        summary: 'Executando: Inspect logs',
        occurredAt: '2026-04-20T12:00:01Z',
      },
    });

    expect(afterProgressed.connection).toBe('live');
    expect(afterProgressed.events[0]?.state).toBe('waiting_tool');

    // Simulate reconcile_snapshot when SSE missed TURN_COMPLETED but GET /v1/conversations/:id has state: 'completed'
    const afterReconcile = activityReducer(afterProgressed, {
      type: 'reconcile_snapshot',
      events: afterProgressed.events,
      cursor: 'a.10',
      conversationState: 'completed',
    });

    expect(afterReconcile.status).toBe('completed');
    expect(afterReconcile.events[0]?.state).toBe('completed');
    expect(afterReconcile.events[0]?.status).toBe('succeeded');
    expect(afterReconcile.events[0]?.summary).toBe('Inspect logs');

    // Also verify direct turn.completed event settlement
    const afterTurnCompleted = activityReducer(afterProgressed, {
      type: 'event',
      event: {
        eventId: 'evt_2',
        cursor: 'a.2',
        turnId: 'turn_1',
        agentId: 'agent:conv_1:main',
        kind: 'lifecycle',
        type: 'turn.completed',
        state: 'completed',
        label: 'Turno concluído',
        summary: 'Turno concluído',
        occurredAt: '2026-04-20T12:00:05Z',
      },
    });

    const settledTool = afterTurnCompleted.events.find((e) => e.eventId === 'evt_1');
    expect(settledTool?.state).toBe('completed');
    expect(settledTool?.status).toBe('succeeded');
    expect(settledTool?.summary).toBe('Inspect logs');
  });

  it('excludes main agent tool events from TeamActivityView and settles subagents when running is false', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'evt_1',
        cursor: 'a.1',
        turnId: 'turn_1',
        occurredAt: '2026-04-20T12:00:00Z',
        kind: 'tool',
        type: 'tool.started',
        state: 'waiting_tool',
        status: 'running',
        label: 'read_file',
        summary: 'read_file',
        toolName: 'read_file',
        agentId: 'agent:conv_1:main',
      },
      {
        eventId: 'evt_2',
        cursor: 'a.2',
        turnId: 'turn_1',
        occurredAt: '2026-04-20T12:00:01Z',
        kind: 'agent',
        type: 'agent.created',
        state: 'waiting_agent',
        status: 'running',
        label: 'Subagente: Researcher',
        summary: '',
        agentId: 'sub_1',
        agentName: 'Researcher',
      },
    ];

    render(<TeamActivityView events={events} running={false} />);
    expect(screen.queryByText(/agent:conv_1:main/i)).toBeNull();
    expect(screen.getByText(/Researcher/i)).toBeTruthy();
    expect(screen.queryByText(/Em andamento/i)).toBeNull();
  });

  it('reports 0 changed files in CodeWorkbenchPanel when all files are only inspected', () => {
    render(
      <CodeWorkbenchPanel
        files={[
          {
            path: 'src/index.ts',
            status: 'inspected',
          },
        ]}
        selectedPath="src/index.ts"
        onSelectFile={() => {}}
      />,
    );

    expect(screen.getByText(/0 arquivos alterados/i)).toBeTruthy();
  });

  it('renders final assistant item.content below TurnTimeline when final deltas were dropped and hides code-changed-files-card when only reads/searches ran', async () => {
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">';
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input);
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.28"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      if (url.includes('/v1/conversations/conv_ashy')) {
        return new Response(
          JSON.stringify({
            conversation_id: 'conv_ashy',
            title: 'Auditoria Ashy',
            state: 'completed',
            mode: 'code',
            permission_mode: 'accept_edits',
            provider: 'claude_cli',
            model_id: 'claude-sonnet-4-6',
            messages: [
              {
                message_id: 'msg-u1',
                role: 'user',
                content: 'Verifique os provedores do Ashy.',
                status: 'completed',
                retryable: false,
                turn_id: 'turn-ashy',
              },
              {
                message_id: 'msg-a1',
                role: 'assistant',
                content: 'Vou inspecionar o workspace.\n\nRelatório final: todos os provedores foram auditados com sucesso.',
                status: 'completed',
                retryable: false,
                turn_id: 'turn-ashy',
              },
            ],
            turns: [
              {
                turn_id: 'turn-ashy',
                state: 'completed',
                code_mode: 'code',
                created_at: '2026-10-30T14:00:00.000Z',
                started_at: '2026-10-30T14:00:00.000Z',
                finished_at: '2026-10-30T14:43:00.000Z',
              },
            ],
            activities: [
              {
                event_id: 'ev-delta-early',
                event_type: 'assistant.delta',
                sequence: 1,
                summary: 'Vou inspecionar o workspace.',
                payload: { message_id: 'msg-a1', content: 'Vou inspecionar o workspace.' },
                occurred_at: '2026-10-30T14:00:02.000Z',
                turn_id: 'turn-ashy',
                agent_id: 'agent:conv_ashy:main',
                cursor: 'a.1',
              },
              {
                event_id: 'ev-search-1',
                event_type: 'tool.finished',
                sequence: 2,
                summary: 'Buscou providers.json',
                payload: {
                  tool_name: 'search_files',
                  tool_kind: 'filesystem',
                  status: 'succeeded',
                  label: 'providers.json',
                },
                occurred_at: '2026-10-30T14:05:00.000Z',
                turn_id: 'turn-ashy',
                agent_id: 'agent:conv_ashy:main',
                cursor: 'a.2',
              },
              {
                event_id: 'ev-ps-28',
                event_type: 'tool.progressed',
                sequence: 28,
                summary: 'Executando: Find Ashy providers.json, mask API keys',
                payload: {
                  tool_name: 'powershell',
                  tool_kind: 'terminal',
                  command: 'Get-ChildItem providers.json',
                  output_tail: 'providers.json masked ok',
                },
                occurred_at: '2026-10-30T14:40:00.000Z',
                turn_id: 'turn-ashy',
                agent_id: 'agent:conv_ashy:main',
                cursor: 'a.28',
              },
            ],
            activity_cursor: 'a.28',
          }),
          { headers: { 'Content-Type': 'application/json' } },
        );
      }
      return new Response(JSON.stringify({ items: [] }), {
        headers: { 'Content-Type': 'application/json' },
      });
    });

    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={['/chats/conv_ashy']}>
          <Routes>
            <Route path="/chats/:conversationId" element={<ChatPage />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>,
    );

    expect(
      await screen.findByText(/Relatório final: todos os provedores foram auditados com sucesso\./i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('code-changed-files-card')).toBeNull();
    const batchToggle = screen.getByTestId('code-tool-batch-toggle');
    fireEvent.click(batchToggle);
    expect(screen.getByText(/Find Ashy providers\.json, mask API keys/i)).toBeInTheDocument();
    expect(screen.getByText(/providers\.json masked ok/i)).toBeInTheDocument();
  });

  it('does not duplicate assistant text or thoughts when reconcile_snapshot merges backend-compacted deltas over granular SSE deltas', () => {
    const fullText =
      'Aqui vai um resumo do que consigo fazer por você:\n\n📁 Arquivos e Código\nSe quiser, posso ajudar diretamente em qualquer uma dessas frentes. O que você precisa agora?';
    const chunks = [
      { id: 'activity:turn_dup:2', text: 'Aqu' },
      { id: 'activity:turn_dup:3', text: 'i vai um resumo do que consigo fazer por você:\n\n' },
      { id: 'activity:turn_dup:4', text: '📁 Arquivos e Código\n' },
      {
        id: 'activity:turn_dup:5',
        text: 'Se quiser, posso ajudar diretamente em qualquer uma dessas frentes. O que você precisa agora?',
      },
    ];

    let state = initialActivityState();

    // Stream granular thought deltas via SSE
    state = activityReducer(state, {
      type: 'event',
      event: {
        eventId: 'activity:turn_dup:0',
        cursor: 'a.0',
        turnId: 'turn_dup',
        agentId: 'agent:conv_dup:main',
        kind: 'agent',
        type: 'agent.thought_delta',
        state: 'working',
        label: 'Pensando',
        summary: 'Analisando ',
        detail: 'Analisando ',
        payload: { delta: 'Analisando ' },
        occurredAt: '2026-11-03T11:00:00.000Z',
      },
    });
    state = activityReducer(state, {
      type: 'event',
      event: {
        eventId: 'activity:turn_dup:1',
        cursor: 'a.1',
        turnId: 'turn_dup',
        agentId: 'agent:conv_dup:main',
        kind: 'agent',
        type: 'agent.thought_delta',
        state: 'working',
        label: 'Pensando',
        summary: 'o pedido do usuário.',
        detail: 'o pedido do usuário.',
        payload: { delta: 'o pedido do usuário.' },
        occurredAt: '2026-11-03T11:00:00.100Z',
      },
    });

    // Stream granular assistant.delta chunks via SSE
    for (let i = 0; i < chunks.length; i += 1) {
      state = activityReducer(state, {
        type: 'event',
        event: {
          eventId: chunks[i]!.id,
          cursor: `a.${i + 2}`,
          turnId: 'turn_dup',
          agentId: 'agent:conv_dup:main',
          messageId: 'msg_dup_1',
          kind: 'message',
          type: 'assistant.delta',
          state: 'working',
          label: 'Resposta',
          summary: chunks[i]!.text,
          content: chunks[i]!.text,
          occurredAt: `2026-11-03T11:00:0${i + 1}.000Z`,
        },
      });
    }

    // Backend GET /v1/conversations/:id compacts consecutive deltas into the first eventId of each run
    const compactedSnapshotEvents: ConversationActivityEvent[] = [
      {
        eventId: 'activity:turn_dup:0',
        cursor: 'a.1',
        turnId: 'turn_dup',
        agentId: 'agent:conv_dup:main',
        kind: 'agent',
        type: 'agent.thought_delta',
        state: 'completed',
        label: 'Pensando',
        summary: 'Analisando ',
        detail: 'Analisando o pedido do usuário.',
        payload: { content: 'Analisando o pedido do usuário.', delta: 'Analisando o pedido do usuário.' },
        occurredAt: '2026-11-03T11:00:00.100Z',
      },
      {
        eventId: 'activity:turn_dup:2',
        cursor: 'a.5',
        turnId: 'turn_dup',
        agentId: 'agent:conv_dup:main',
        messageId: 'msg_dup_1',
        kind: 'message',
        type: 'assistant.delta',
        state: 'completed',
        label: 'Resposta',
        summary: 'Aqu',
        content: fullText,
        occurredAt: '2026-11-03T11:00:04.000Z',
      },
      {
        eventId: 'activity:turn_dup:6',
        cursor: 'a.6',
        turnId: 'turn_dup',
        agentId: 'agent:conv_dup:main',
        kind: 'lifecycle',
        type: 'turn.completed',
        state: 'completed',
        label: 'Turno concluído',
        summary: 'Turno concluído',
        occurredAt: '2026-11-03T11:00:05.000Z',
      },
    ];

    const reconciled = activityReducer(state, {
      type: 'reconcile_snapshot',
      events: compactedSnapshotEvents,
      cursor: 'a.6',
      conversationState: 'completed',
    });

    const timeline = buildTurnTimeline(reconciled.events, 'turn_dup', 'msg_dup_1', fullText, true);
    const textItems = timeline.filter((item): item is { id: string; kind: 'text'; content: string } => item.kind === 'text');
    const combinedText = textItems.map((item) => item.content).join('');

    expect(combinedText).toBe(fullText);
    expect(combinedText).not.toContain('O que você precisa agora?i vai um resumo');

    const thoughtItems = timeline.filter((item) => item.kind === 'thought');
    expect(thoughtItems).toHaveLength(1);
    if (thoughtItems[0]?.kind === 'thought') {
      expect(thoughtItems[0].thought.content).toBe('Analisando o pedido do usuário.');
    }
  });

  it('does not duplicate assistant text in reconcile_snapshot when the first SSE delta was missed or interrupted by a settled tool.progressed', () => {
    const fullText = 'Aqui vai um resumo completo sem duplicação.';
    let state = initialActivityState();

    // Simulate SSE missing the first chunk ("Aqu", eventId: evt_d1) and receiving evt_d2..evt_d3 with an intervening tool.progressed
    state = activityReducer(state, {
      type: 'event',
      event: {
        eventId: 'evt_d2',
        cursor: 'a.2',
        turnId: 'turn_missed',
        agentId: 'agent:conv_missed:main',
        messageId: 'msg_missed',
        kind: 'message',
        type: 'assistant.delta',
        state: 'working',
        label: 'Resposta',
        summary: 'i vai um resumo ',
        content: 'i vai um resumo ',
        occurredAt: '2026-11-03T11:00:01.000Z',
      },
    });
    state = activityReducer(state, {
      type: 'event',
      event: {
        eventId: 'evt_prog_mid',
        cursor: 'a.3',
        turnId: 'turn_missed',
        agentId: 'agent:conv_missed:main',
        kind: 'tool',
        type: 'tool.progressed',
        state: 'waiting_tool',
        status: 'running',
        toolName: 'powershell',
        label: 'PowerShell',
        summary: 'Executando: check',
        occurredAt: '2026-11-03T11:00:02.000Z',
      },
    });
    state = activityReducer(state, {
      type: 'event',
      event: {
        eventId: 'evt_d3',
        cursor: 'a.4',
        turnId: 'turn_missed',
        agentId: 'agent:conv_missed:main',
        messageId: 'msg_missed',
        kind: 'message',
        type: 'assistant.delta',
        state: 'working',
        label: 'Resposta',
        summary: 'completo sem duplicação.',
        content: 'completo sem duplicação.',
        occurredAt: '2026-11-03T11:00:03.000Z',
      },
    });

    // Backend dropped tool.progressed on settled turn and compacted evt_d1 + evt_d2 + evt_d3 into evt_d1
    const reconciled = activityReducer(state, {
      type: 'reconcile_snapshot',
      events: [
        {
          eventId: 'evt_d1',
          cursor: 'a.4',
          turnId: 'turn_missed',
          agentId: 'agent:conv_missed:main',
          messageId: 'msg_missed',
          kind: 'message',
          type: 'assistant.delta',
          state: 'completed',
          label: 'Resposta',
          summary: 'Aqu',
          content: fullText,
          occurredAt: '2026-11-03T11:00:03.000Z',
        },
      ],
      cursor: 'a.4',
      conversationState: 'completed',
    });

    const deltaEvents = reconciled.events.filter((e) => e.type === 'assistant.delta');
    expect(deltaEvents).toHaveLength(1);
    expect(deltaEvents[0]?.content).toBe(fullText);
  });

  it('preserves separate paragraphs across surviving tool.finished events even when paragraph texts overlap', () => {
    let state = initialActivityState();

    state = activityReducer(state, {
      type: 'event',
      event: {
        eventId: 'evt_p1_a',
        cursor: 'a.1',
        turnId: 'turn_two_paras',
        agentId: 'agent:conv_two:main',
        messageId: 'msg_two',
        kind: 'message',
        type: 'assistant.delta',
        state: 'working',
        label: 'Resposta',
        summary: 'Ok. ',
        content: 'Ok. ',
        occurredAt: '2026-11-03T11:00:01.000Z',
      },
    });
    state = activityReducer(state, {
      type: 'event',
      event: {
        eventId: 'evt_p1_b',
        cursor: 'a.2',
        turnId: 'turn_two_paras',
        agentId: 'agent:conv_two:main',
        messageId: 'msg_two',
        kind: 'message',
        type: 'assistant.delta',
        state: 'working',
        label: 'Resposta',
        summary: 'Vou verificar.',
        content: 'Vou verificar.',
        occurredAt: '2026-11-03T11:00:02.000Z',
      },
    });
    state = activityReducer(state, {
      type: 'event',
      event: {
        eventId: 'evt_tool_done',
        cursor: 'a.3',
        turnId: 'turn_two_paras',
        agentId: 'agent:conv_two:main',
        kind: 'tool',
        type: 'tool.finished',
        state: 'completed',
        status: 'succeeded',
        toolName: 'read_file',
        label: 'read_file',
        summary: 'Leu config.json',
        occurredAt: '2026-11-03T11:00:03.000Z',
      },
    });
    // Second paragraph missed its first char "O" (evt_p2_a) and received "k." (evt_p2_b)
    state = activityReducer(state, {
      type: 'event',
      event: {
        eventId: 'evt_p2_b',
        cursor: 'a.5',
        turnId: 'turn_two_paras',
        agentId: 'agent:conv_two:main',
        messageId: 'msg_two',
        kind: 'message',
        type: 'assistant.delta',
        state: 'working',
        label: 'Resposta',
        summary: 'k.',
        content: 'k.',
        occurredAt: '2026-11-03T11:00:05.000Z',
      },
    });

    const reconciled = activityReducer(state, {
      type: 'reconcile_snapshot',
      events: [
        {
          eventId: 'evt_p1_a',
          cursor: 'a.2',
          turnId: 'turn_two_paras',
          agentId: 'agent:conv_two:main',
          messageId: 'msg_two',
          kind: 'message',
          type: 'assistant.delta',
          state: 'completed',
          label: 'Resposta',
          summary: 'Ok. ',
          content: 'Ok. Vou verificar.',
          occurredAt: '2026-11-03T11:00:02.000Z',
        },
        {
          eventId: 'evt_tool_done',
          cursor: 'a.3',
          turnId: 'turn_two_paras',
          agentId: 'agent:conv_two:main',
          kind: 'tool',
          type: 'tool.finished',
          state: 'completed',
          status: 'succeeded',
          toolName: 'read_file',
          label: 'read_file',
          summary: 'Leu config.json',
          occurredAt: '2026-11-03T11:00:03.000Z',
        },
        {
          eventId: 'evt_p2_a',
          cursor: 'a.5',
          turnId: 'turn_two_paras',
          agentId: 'agent:conv_two:main',
          messageId: 'msg_two',
          kind: 'message',
          type: 'assistant.delta',
          state: 'completed',
          label: 'Resposta',
          summary: 'O',
          content: 'Ok.',
          occurredAt: '2026-11-03T11:00:05.000Z',
        },
      ],
      cursor: 'a.5',
      conversationState: 'completed',
    });

    const deltaEvents = reconciled.events.filter((e) => e.type === 'assistant.delta');
    expect(deltaEvents).toHaveLength(2);
    expect(deltaEvents[0]?.content).toBe('Ok. Vou verificar.');
    expect(deltaEvents[1]?.content).toBe('Ok.');
  });

  it('ignores replayed or late SSE delta chunks after reconcile_snapshot has already compacted them', () => {
    const fullText = 'Aqui vai um resumo completo.';
    let state = initialActivityState();

    state = activityReducer(state, {
      type: 'event',
      event: {
        eventId: 'activity:turn_r:2',
        cursor: 'a.2',
        turnId: 'turn_r',
        agentId: 'agent:conv_r:main',
        messageId: 'msg_r',
        kind: 'message',
        type: 'assistant.delta',
        state: 'working',
        label: 'Resposta',
        summary: 'Aqu',
        content: 'Aqu',
      },
    });
    state = activityReducer(state, {
      type: 'event',
      event: {
        eventId: 'activity:turn_r:3',
        cursor: 'a.3',
        turnId: 'turn_r',
        agentId: 'agent:conv_r:main',
        messageId: 'msg_r',
        kind: 'message',
        type: 'assistant.delta',
        state: 'working',
        label: 'Resposta',
        summary: 'i vai um resumo completo.',
        content: 'i vai um resumo completo.',
      },
    });

    state = activityReducer(state, {
      type: 'reconcile_snapshot',
      events: [
        {
          eventId: 'activity:turn_r:2',
          cursor: 'a.3',
          turnId: 'turn_r',
          agentId: 'agent:conv_r:main',
          messageId: 'msg_r',
          kind: 'message',
          type: 'assistant.delta',
          state: 'completed',
          label: 'Resposta',
          summary: 'Aqu',
          content: fullText,
        },
      ],
      cursor: 'a.3',
      conversationState: 'completed',
    });

    // Replay chunk 3 (whose cursor equals state.cursor 'a.3') over SSE after compaction
    const afterReplay = activityReducer(state, {
      type: 'event',
      event: {
        eventId: 'activity:turn_r:3',
        cursor: 'a.3',
        turnId: 'turn_r',
        agentId: 'agent:conv_r:main',
        messageId: 'msg_r',
        kind: 'message',
        type: 'assistant.delta',
        state: 'working',
        label: 'Resposta',
        summary: 'i vai um resumo completo.',
        content: 'i vai um resumo completo.',
      },
    });

    const deltaEvents = afterReplay.events.filter((e) => e.type === 'assistant.delta');
    expect(deltaEvents).toHaveLength(1);
    expect(deltaEvents[0]?.content).toBe(fullText);
  });
});
