import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { parseConversationActivityEvent } from '../../api/conversations'
import { CodePermissionDock, assessPermissionRisk } from './CodePermissionDock'
import { Composer } from './Composer'
import { ToolPermissionCard } from './ToolPermissionCard'
import { TurnTimeline } from './TurnTimeline'
import { buildTurnTimeline } from './turnTimelineFold'

describe('Fase B: ToolPermissionCard, 4 Permission Modes & Rewind UI', () => {
  it('renders ToolPermissionCard with StructuredDiffView and invokes decision callback', async () => {
    const onDecision = vi.fn().mockResolvedValue(undefined)
    render(
      <ToolPermissionCard
        request={{
          requestId: 'req-123',
          turnId: 'turn-1',
          toolName: 'edit_file',
          path: 'src/app.ts',
          ruleSuggestion: 'edit_file',
          addedLines: 1,
          removedLines: 1,
          structuredPatch: [
            {
              oldStart: 1,
              oldLines: 1,
              newStart: 1,
              newLines: 1,
              lines: ['-const a = 1', '+const a = 2'],
            },
          ],
        }}
        onDecision={onDecision}
      />,
    )

    expect(screen.getByTestId('tool-permission-card')).toBeInTheDocument()
    expect(screen.getByText(/Permissão necessária · edit_file/i)).toBeInTheDocument()
    expect(screen.getByText(/Arquivo: src\/app\.ts/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Sempre permitir no projeto' }))
    await waitFor(() => {
      expect(onDecision).toHaveBeenCalledWith('allow_always')
    })
  })

  it('cycles through all 4 permission modes including full_autonomy on Shift+Tab in Composer', () => {
    const onPermissionChange = vi.fn()
    render(
      <Composer
        value=""
        onChange={() => {}}
        onSubmit={() => {}}
        codeMode="code"
        onCodeModeChange={() => {}}
        onCodePermissionModeChange={onPermissionChange}
      />,
    )

    const select = screen.getByTestId('code-permission-mode-select') as HTMLSelectElement
    expect(select.value).toBe('ask_permissions')

    const textarea = screen.getByLabelText('Mensagem')
    fireEvent.keyDown(textarea, { key: 'Tab', shiftKey: true })
    expect(select.value).toBe('full_autonomy')
    expect(onPermissionChange).toHaveBeenLastCalledWith('full_autonomy')

    fireEvent.keyDown(textarea, { key: 'Tab', shiftKey: true })
    expect(select.value).toBe('accept_edits')
    expect(onPermissionChange).toHaveBeenLastCalledWith('accept_edits')

    fireEvent.keyDown(textarea, { key: 'Tab', shiftKey: true })
    expect(select.value).toBe('plan_mode')
    expect(onPermissionChange).toHaveBeenLastCalledWith('plan_mode')

    fireEvent.keyDown(textarea, { key: 'Tab', shiftKey: true })
    expect(select.value).toBe('ask_permissions')
    expect(onPermissionChange).toHaveBeenLastCalledWith('ask_permissions')
  })

  it('parses permission_request from activity event and renders 2-step Reverter alterações confirmation on modified turn', async () => {
    const permEvent = parseConversationActivityEvent(
      {
        event_id: 'evt-perm-1',
        event_type: 'code_mode.decision_required',
        turn_id: 'turn-42',
        agent_id: 'agent:main',
        summary: 'Permissão necessária para run_command',
        payload: {
          stage: 'awaiting_approval',
          permission_request: {
            request_id: 'req-99',
            turn_id: 'turn-42',
            tool_name: 'run_command',
            command: 'npm test',
            rule_suggestion: 'run_command(npm test:*)',
          },
        },
      },
      '1',
    )
    expect(permEvent.toolPermission?.requestId).toBe('req-99')
    expect(permEvent.toolPermission?.command).toBe('npm test')

    const writeEvent = parseConversationActivityEvent(
      {
        event_id: 'evt-write-1',
        event_type: 'tool.finished',
        turn_id: 'turn-42',
        agent_id: 'agent:main',
        summary: 'Escreveu src/index.ts',
        payload: {
          tool_name: 'write_file',
          tool_kind: 'filesystem',
          status: 'succeeded',
          path: 'src/index.ts',
          added_lines: 3,
          removed_lines: 0,
        },
      },
      '2',
    )
    const msgEvent = parseConversationActivityEvent(
      {
        event_id: 'evt-msg-1',
        event_type: 'assistant.completed',
        turn_id: 'turn-42',
        agent_id: 'agent:main',
        summary: 'Resposta pronta',
        payload: {
          message_id: 'msg-42',
          content: 'Pronto!',
        },
      },
      '3',
    )

    const items = buildTurnTimeline([permEvent, writeEvent, msgEvent], 'turn-42', 'msg-42', 'Pronto!')
    const onRewind = vi.fn().mockResolvedValue(undefined)
    render(<TurnTimeline items={items} onRewindTurn={onRewind} />)

    expect(screen.getByTestId('tool-permission-card')).toBeInTheDocument()
    const rewindBtn = screen.getByTestId('rewind-turn-button')
    expect(rewindBtn).toHaveTextContent('Reverter alterações')
    fireEvent.click(rewindBtn)
    expect(onRewind).not.toHaveBeenCalled()
    const confirmBtn = screen.getByTestId('rewind-confirm-button')
    fireEvent.click(confirmBtn)
    await waitFor(() => {
      expect(onRewind).toHaveBeenCalledWith('turn-42')
    })
  })

  it('parses suggested_rule, target and reason in parseToolPermissionRequest', () => {
    const event = parseConversationActivityEvent(
      {
        event_id: 'evt-perm-suggested',
        event_type: 'code_mode.decision_required',
        turn_id: 'turn-55',
        agent_id: 'agent:main',
        summary: 'Permissão necessária para run_command',
        payload: {
          stage: 'waiting_decision',
          permission_request: {
            request_id: 'perm_abc123',
            turn_id: 'turn-55',
            tool_name: 'run_command',
            command: 'cloudflared tunnel --url http://localhost:5173',
            suggested_rule: 'run_command(cloudflared:*)',
            reason: 'Preciso expor o preview local',
          },
        },
      },
      '10',
    )
    expect(event.toolPermission?.requestId).toBe('perm_abc123')
    expect(event.toolPermission?.ruleSuggestion).toBe('run_command(cloudflared:*)')
    expect((event.toolPermission as { target?: string })?.target).toBe(
      'cloudflared tunnel --url http://localhost:5173',
    )
    expect((event.toolPermission as { reason?: string })?.reason).toBe('Preciso expor o preview local')
    expect((event.toolPermission as { state?: string })?.state).toBe('pending')
  })

  it('renders CodePermissionDock with semantic badges, high-risk indicator, queue badge, keyboard shortcuts, and inline deny+instruct', async () => {
    expect(assessPermissionRisk({ requestId: '1', toolName: 'run_command', command: 'cloudflared tunnel' })).toBe('high')
    expect(assessPermissionRisk({ requestId: '2', toolName: 'run_command', command: 'rm -rf dist' })).toBe('high')
    expect(assessPermissionRisk({ requestId: '3', toolName: 'edit_file', path: '.env.local' })).toBe('high')
    expect(assessPermissionRisk({ requestId: '4', toolName: 'run_command', command: 'npm test' })).toBe('normal')

    const onResolve = vi.fn().mockResolvedValue(undefined)
    render(
      <CodePermissionDock
        requests={[
          {
            requestId: 'req-1',
            turnId: 'turn-1',
            toolName: 'run_command',
            command: 'cloudflared tunnel --url http://localhost:5173',
            ruleSuggestion: 'run_command(cloudflared:*)',
            state: 'pending',
          } as never,
          {
            requestId: 'req-2',
            turnId: 'turn-1',
            toolName: 'edit_file',
            path: 'src/App.tsx',
            ruleSuggestion: 'edit_file(src/App.tsx)',
            state: 'pending',
          } as never,
        ]}
        onResolve={onResolve}
      />,
    )

    const dock = screen.getByTestId('code-permission-dock')
    expect(dock).toHaveClass('code-perm-dock--queued')
    expect(dock).toHaveClass('code-perm-dock--high-risk')
    expect(screen.getByText('Bash')).toBeInTheDocument()
    expect(screen.getByText('Executar comando no terminal')).toBeInTheDocument()
    expect(screen.getByText('Também aguardando: 1')).toBeInTheDocument()

    // Shortcut '2' -> Permitir sempre no projeto
    fireEvent.keyDown(window, { key: '2' })
    await waitFor(() => {
      expect(onResolve).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({
          behavior: 'allow',
          scope: 'project',
          updatedRule: 'run_command(cloudflared:*)',
        }),
      )
    })

    // Test inline guidance ("Negar e orientar...")
    fireEvent.click(screen.getByTestId('code-perm-deny-instruct-toggle'))
    const input = screen.getByPlaceholderText('Diga ao Orin como fazer diferente...')
    fireEvent.change(input, { target: { value: 'Use apenas localhost sem túnel' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(onResolve).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({
          behavior: 'deny',
          scope: 'once',
          reason: 'Use apenas localhost sem túnel',
        }),
      )
    })
  })
})
