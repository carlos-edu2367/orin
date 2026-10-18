import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TurnTimeline } from '../../src/features/conversations/TurnTimeline'
import type { ConversationActivityEvent } from '../../src/features/conversations/activityTypes'
import { buildTurnTimeline, type TimelineItem } from '../../src/features/conversations/turnTimelineFold'

describe('TurnTimeline', () => {
  it('renders text and activity items in the order given', () => {
    const items: TimelineItem[] = [
      { id: 'text:1', kind: 'text', content: 'Vou criar o subagente.' },
      {
        id: 'activity:agent:c:sub:agent.created:2',
        kind: 'activity',
        group: {
          id: 'agent:c:sub:agent.created:',
          kind: 'agent',
          state: 'completed',
          label: 'Criou o agente Analista',
          count: 1,
          agentId: 'agent:c:sub',
          failed: false,
          events: [{
            eventId: '2', cursor: 'a.2', type: 'agent.created', kind: 'agent', state: 'completed',
            agentId: 'agent:c:sub', summary: 'Criou o agente Analista', label: 'Analista',
          }],
        },
      },
    ]

    const { container } = render(<TurnTimeline items={items} />)

    expect(screen.getByText('Vou criar o subagente.')).toBeInTheDocument()
    expect(screen.getByText(/Criou o agente/)).toBeInTheDocument()
    const order = container.querySelector('.turn-timeline')!.children
    expect(order[0].className).toBe('turn-timeline__text')
    expect(order[1].className).toBe('turn-timeline__activity')
  })

  it('renders CodeToolCallBlock for edit_file, read_file unchanged, and run_command and retires the 5-stage CodeModeCard stepper', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'evt-code-start',
        cursor: 'a.1',
        turnId: 'turn-code-1',
        type: 'code_mode.started',
        kind: 'tool',
        state: 'working',
        agentId: 'agent:main',
        summary: 'Iniciando modo code',
        codeStage: 'discover',
      },
      {
        eventId: 'evt-read-1',
        cursor: 'a.2',
        turnId: 'turn-code-1',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        summary: 'Leu src/config.ts',
        toolName: 'read_file',
        path: 'src/config.ts',
        unchanged: true,
      },
      {
        eventId: 'evt-edit-1',
        cursor: 'a.3',
        turnId: 'turn-code-1',
        type: 'code_mode.file_edited',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        summary: 'Atualizou src/app.tsx',
        toolName: 'edit_file',
        path: 'src/app.tsx',
        addedLines: 4,
        removedLines: 1,
        structuredPatch: [
          {
            oldStart: 10,
            oldLines: 1,
            newStart: 10,
            newLines: 4,
            lines: ['-const oldVal = 1', '+const newVal = 2'],
          },
        ],
      },
      {
        eventId: 'evt-bash-1',
        cursor: 'a.4',
        turnId: 'turn-code-1',
        type: 'tool.completed',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        summary: 'npm test',
        toolName: 'run_command',
        command: 'npm test',
        bashOutput: {
          command: 'npm test',
          stdout: 'PASS src/app.test.tsx',
          stderr: '',
          exit_code: 0,
          duration_ms: 320,
        },
      },
    ]

    const onRewind = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      <TurnTimeline events={events} mode="code" onRewindTurn={onRewind} />,
    )

    // Compact rewind button is rendered inside the turn header
    const header = screen.getByTestId('turn-timeline-header')
    const rewindBtn = screen.getByTestId('rewind-turn-button')
    expect(header).toContainElement(rewindBtn)

    // CodeToolCallBlock rendered for read_file (unchanged), edit_file, and run_command
    const blocks = screen.getAllByTestId('code-tool-call-block')
    expect(blocks.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText(/sem alterações desde a última leitura/i)).toBeInTheDocument()
    expect(screen.getByText('src/app.tsx')).toBeInTheDocument()
    expect(screen.getByText('PASS src/app.test.tsx')).toBeInTheDocument()

    // Old 5-stage CodeModeCard stepper is retired
    expect(container.querySelector('.code-mode-card')).not.toBeInTheDocument()
  })

  it('renders PlanApprovalCard, locks buttons with receipt after approval, and supports requesting changes', () => {
    const onAcceptEdits = vi.fn()
    const onAskPermissions = vi.fn()
    const onRequestChanges = vi.fn()

    const events: ConversationActivityEvent[] = [
      {
        eventId: 'evt-plan-1',
        cursor: 'a.5',
        turnId: 'turn-plan-1',
        type: 'code_mode.plan_ready',
        kind: 'tool',
        state: 'waiting_user',
        agentId: 'agent:main',
        summary: 'Plano pronto para aprovação',
        planApproval: {
          plan_path: '.orin/plans/2026-10-15-refactor.md',
          plan_content: '# Plano de Refatoração\n\n1. Atualizar TurnTimeline\n2. Atualizar Composer',
        },
      },
    ]

    const { unmount } = render(
      <TurnTimeline
        events={events}
        mode="code"
        permissionMode="plan_mode"
        onApprovePlanAcceptEdits={onAcceptEdits}
        onApprovePlanAskPermissions={onAskPermissions}
        onRequestPlanChanges={onRequestChanges}
      />,
    )

    expect(screen.getByTestId('plan-approval-card')).toBeInTheDocument()
    expect(screen.getByText('.orin/plans/2026-10-15-refactor.md')).toBeInTheDocument()

    const approveBtn = screen.getByTestId('plan-approve-accept-edits')
    fireEvent.click(approveBtn)
    expect(onAcceptEdits).toHaveBeenCalledTimes(1)
    expect(approveBtn).toBeDisabled()
    expect(screen.getByTestId('plan-approval-receipt')).toHaveTextContent(/Plano aprovado/i)

    // Clicking again does not re-trigger callback
    fireEvent.click(approveBtn)
    expect(onAcceptEdits).toHaveBeenCalledTimes(1)
    unmount()

    render(
      <TurnTimeline
        events={events}
        mode="code"
        permissionMode="plan_mode"
        onApprovePlanAcceptEdits={onAcceptEdits}
        onApprovePlanAskPermissions={onAskPermissions}
        onRequestPlanChanges={onRequestChanges}
      />,
    )

    fireEvent.click(screen.getByTestId('plan-request-changes-toggle'))
    fireEvent.change(screen.getByLabelText('Ajustes no plano'), {
      target: { value: 'Adicione cobertura de testes para ChatPage' },
    })
    fireEvent.click(screen.getByTestId('plan-request-changes-submit'))
    expect(onRequestChanges).toHaveBeenCalledWith(
      'Adicione cobertura de testes para ChatPage',
      expect.objectContaining({ eventId: 'evt-plan-1' }),
    )
    expect(screen.getByTestId('plan-approval-receipt')).toHaveTextContent(/Ajustes solicitados/i)
  })

  it('requires 2-click inline confirmation before triggering onRewindTurn and displays error alert on failure', async () => {
    const onRewind = vi
      .fn()
      .mockRejectedValueOnce(new Error('Checkpoint expirado no servidor'))
      .mockResolvedValueOnce(undefined)

    const events: ConversationActivityEvent[] = [
      {
        eventId: 'evt-write-1',
        cursor: 'a.1',
        turnId: 'turn-rewind-9',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        summary: 'Escreveu src/main.ts',
        toolName: 'write_file',
        path: 'src/main.ts',
        addedLines: 5,
        removedLines: 0,
      },
    ]

    render(<TurnTimeline events={events} mode="code" onRewindTurn={onRewind} />)

    // First click opens inline confirmation instead of firing immediately
    fireEvent.click(screen.getByTestId('rewind-turn-button'))
    expect(onRewind).not.toHaveBeenCalled()
    expect(screen.getByTestId('rewind-confirm-bar')).toBeInTheDocument()

    // Cancel returns to initial button
    fireEvent.click(screen.getByTestId('rewind-cancel-button'))
    expect(screen.queryByTestId('rewind-confirm-bar')).not.toBeInTheDocument()

    // First confirmed attempt fails -> shows error alert
    fireEvent.click(screen.getByTestId('rewind-turn-button'))
    fireEvent.click(screen.getByTestId('rewind-confirm-button'))
    await waitFor(() => {
      expect(screen.getByTestId('rewind-turn-error')).toHaveTextContent('Checkpoint expirado no servidor')
    })

    // Second confirmed attempt succeeds
    fireEvent.click(screen.getByTestId('rewind-turn-button'))
    fireEvent.click(screen.getByTestId('rewind-confirm-button'))
    await waitFor(() => {
      expect(onRewind).toHaveBeenCalledTimes(2)
      expect(onRewind).toHaveBeenLastCalledWith('turn-rewind-9')
    })
  })

  it('folds more than 2 consecutive failed calls of the same tool into an expandable group', () => {
    const events: ConversationActivityEvent[] = [1, 2, 3, 4].map((idx) => ({
      eventId: `evt-fail-write-${idx}`,
      cursor: `a.${idx}`,
      turnId: 'turn-fail-batch',
      type: 'tool.finished',
      kind: 'tool',
      state: 'failed',
      status: 'failed',
      agentId: 'agent:main',
      toolName: 'write_file',
      path: `src/file${idx}.ts`,
      errorCode: 'TOOL_REFUSED',
      summary: ` recusou escrever src/file${idx}.ts`,
    }))

    render(<TurnTimeline events={events} mode="code" />)

    const group = screen.getByTestId('code-tool-failure-group')
    expect(group).toHaveTextContent('4 falhas de Write recolhidas')
    expect(screen.queryAllByTestId('code-tool-call-block')).toHaveLength(0)

    fireEvent.click(screen.getByTestId('code-tool-failure-group-toggle'))
    expect(screen.getAllByTestId('code-tool-call-block')).toHaveLength(4)
  })

  it('nests subagent tool calls inside SubagentTaskContainer and backfills path from tool.started', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'evt-main-start',
        cursor: 'a.1',
        turnId: 'turn-sub-1',
        type: 'tool.started',
        kind: 'tool',
        state: 'waiting_tool',
        agentId: 'agent:main',
        invocationId: 'inv-read-1',
        toolName: 'read_file',
        path: 'src/preserved-from-started.ts',
        summary: 'Lendo src/preserved-from-started.ts',
      },
      {
        eventId: 'evt-main-finish',
        cursor: 'a.2',
        turnId: 'turn-sub-1',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:main',
        invocationId: 'inv-read-1',
        toolName: 'read_file',
        unchanged: true,
        summary: 'Sem alterações desde a última leitura',
      },
      {
        eventId: 'evt-sub-tool',
        cursor: 'a.3',
        turnId: 'turn-sub-1',
        type: 'tool.finished',
        kind: 'tool',
        state: 'completed',
        agentId: 'agent:sub:explorer',
        parentAgentId: 'agent:main',
        agentName: 'Explorador',
        toolName: 'search_files',
        label: 'useTimeline',
        summary: 'Buscou useTimeline',
      },
    ]

    render(<TurnTimeline events={events} mode="code" />)

    expect(screen.getByText('src/preserved-from-started.ts')).toBeInTheDocument()
    const subagentContainer = screen.getByTestId('subagent-task-group')
    expect(subagentContainer).toHaveTextContent('Explorador')
  })

  it('renders ThoughtBlock collapsed by default while streaming, expands on click, and preserves expansion when completed', () => {
    const streamingItems: TimelineItem[] = [
      {
        id: 'thought:1',
        kind: 'thought',
        thought: {
          id: 'thought:1',
          turnId: 'turn-t1',
          content: 'Analisando a causa raiz do problema...',
          status: 'streaming',
        },
      } as unknown as TimelineItem,
    ]

    const { rerender } = render(<TurnTimeline items={streamingItems} mode="code" />)

    expect(screen.getByTestId('thought-block-streaming')).toBeInTheDocument()
    const streamingToggleBtn = screen.getByTestId('thought-block-toggle')
    expect(streamingToggleBtn).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('Pensando...')).toHaveClass('agent-pulse__label--shimmer')
    expect(screen.queryByText('Analisando a causa raiz do problema...')).not.toBeInTheDocument()

    // Clicking while streaming expands the thought body live
    fireEvent.click(streamingToggleBtn)
    expect(streamingToggleBtn).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Analisando a causa raiz do problema...')).toBeInTheDocument()

    const completedItems: TimelineItem[] = [
      {
        id: 'thought:1',
        kind: 'thought',
        thought: {
          id: 'thought:1',
          turnId: 'turn-t1',
          content: 'Analisando a causa raiz do problema...',
          status: 'completed',
          durationMs: 4200,
        },
      } as unknown as TimelineItem,
    ]

    rerender(<TurnTimeline items={completedItems} mode="code" />)

    // Since user expanded it while streaming, it stays expanded on completion
    const completedToggleBtn = screen.getByRole('button', { name: /Pensou por 4s/i })
    expect(completedToggleBtn).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Analisando a causa raiz do problema...')).toBeInTheDocument()

    // Clicking collapses it
    fireEvent.click(completedToggleBtn)
    expect(completedToggleBtn).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Analisando a causa raiz do problema...')).not.toBeInTheDocument()
  })

  it('keeps ThoughtBlock collapsed on completion when untouched during streaming', () => {
    const streamingItems: TimelineItem[] = [
      {
        id: 'thought:2',
        kind: 'thought',
        thought: {
          id: 'thought:2',
          turnId: 'turn-t2',
          content: 'Pensamento silencioso...',
          status: 'streaming',
        },
      } as unknown as TimelineItem,
    ]

    const { rerender } = render(<TurnTimeline items={streamingItems} mode="code" />)
    expect(screen.queryByText('Pensamento silencioso...')).not.toBeInTheDocument()

    const completedItems: TimelineItem[] = [
      {
        id: 'thought:2',
        kind: 'thought',
        thought: {
          id: 'thought:2',
          turnId: 'turn-t2',
          content: 'Pensamento silencioso...',
          status: 'completed',
          durationMs: 3000,
        },
      } as unknown as TimelineItem,
    ]

    rerender(<TurnTimeline items={completedItems} mode="code" />)
    const toggleBtn = screen.getByRole('button', { name: /Pensou por 3s/i })
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Pensamento silencioso...')).not.toBeInTheDocument()

    fireEvent.click(toggleBtn)
    expect(screen.getByText('Pensamento silencioso...')).toBeInTheDocument()
  })

  it('keeps ParallelSubagentsGroup intact when subagents emit thought_delta events during a parallel wave', () => {
    const events: ConversationActivityEvent[] = [
      {
        eventId: 'evt-main-start',
        cursor: 'c.1',
        turnId: 'turn-w1',
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        agentId: 'agent:main',
        summary: 'Iniciado',
      },
      {
        eventId: 'evt-sub1-tool',
        cursor: 'c.2',
        turnId: 'turn-w1',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'agent:sub:1',
        parentAgentId: 'agent:main',
        agentName: 'explore-1',
        toolName: 'read_file',
        path: 'src/a.ts',
        summary: 'Lendo src/a.ts',
      },
      {
        eventId: 'evt-sub2-thought',
        cursor: 'c.3',
        turnId: 'turn-w1',
        type: 'agent.thought_delta',
        kind: 'lifecycle',
        state: 'working',
        agentId: 'agent:sub:2',
        parentAgentId: 'agent:main',
        agentName: 'explore-2',
        detail: 'Pensando no subagente 2...',
        summary: 'Pensando',
      },
      {
        eventId: 'evt-sub2-tool',
        cursor: 'c.4',
        turnId: 'turn-w1',
        type: 'tool.started',
        kind: 'tool',
        state: 'working',
        agentId: 'agent:sub:2',
        parentAgentId: 'agent:main',
        agentName: 'explore-2',
        toolName: 'read_file',
        path: 'src/b.ts',
        summary: 'Lendo src/b.ts',
      },
    ]

    const { rerender } = render(<TurnTimeline events={events} mode="code" />)
    expect(screen.getByTestId('parallel-subagents-group')).toBeInTheDocument()

    const foldedItems = buildTurnTimeline(events, 'turn-w1')
    rerender(<TurnTimeline items={foldedItems} mode="code" />)
    expect(screen.getByTestId('parallel-subagents-group')).toBeInTheDocument()
  })

  it('renders compact 1-line permission receipts for allowed, denied, and pending toolPermission events', () => {
    const allowedEvent: ConversationActivityEvent = {
      eventId: 'evt-perm-allow',
      cursor: 'a.1',
      turnId: 'turn-p1',
      type: 'code_mode.decision_required',
      kind: 'tool',
      state: 'completed',
      agentId: 'agent:main',
      summary: 'Permissão concedida',
      toolName: 'run_command',
      command: 'npm test',
      toolPermission: {
        requestId: 'req-allow',
        turnId: 'turn-p1',
        toolName: 'run_command',
        command: 'npm test',
        state: 'allowed',
      } as never,
    }
    const deniedEvent: ConversationActivityEvent = {
      eventId: 'evt-perm-deny',
      cursor: 'a.2',
      turnId: 'turn-p1',
      type: 'code_mode.decision_required',
      kind: 'tool',
      state: 'failed',
      agentId: 'agent:main',
      summary: 'Permissão negada',
      toolName: 'edit_file',
      path: 'src/secret.ts',
      toolPermission: {
        requestId: 'req-deny',
        turnId: 'turn-p1',
        toolName: 'edit_file',
        path: 'src/secret.ts',
        state: 'denied',
      } as never,
    }

    const { container } = render(<TurnTimeline events={[allowedEvent, deniedEvent]} mode="code" />)
    expect(container.querySelector('.perm-receipt--allowed')).toHaveTextContent(/✓ Permissão concedida · Bash\(npm test\)/)
    expect(container.querySelector('.perm-receipt--denied')).toHaveTextContent(/✕ Permissão negada · Update\(src\/secret\.ts\)/)
  })
})

