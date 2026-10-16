import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { parseConversationActivityEvent } from '../../api/conversations'
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

  it('cycles through the 3 safe Code Mode permission modes on Shift+Tab in Composer without escalating to full_autonomy', () => {
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
})
