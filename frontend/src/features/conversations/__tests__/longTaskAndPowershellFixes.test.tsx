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
});
