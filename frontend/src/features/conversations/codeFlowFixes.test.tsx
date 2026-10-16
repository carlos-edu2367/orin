import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import {
  EVENT_LIMIT,
  activityReducer,
  applyActivityEvent,
  createActivityState,
  settleTodos,
} from './activityReducer'
import type { ActivityGroup, ConversationActivityEvent, TodoItem } from './activityTypes'
import { CodeToolCallBlock } from './CodeToolCallBlock'
import { TodoBar } from './TodoBar'
import { listWorkspaceFiles, nextCodePermissionMode } from '../../api/codeMode'
import { renderActivityGroup } from './ActivityStream'
import type { ApiClient } from '../../api/client'

describe('Code Flow Bug Fixes (Frontend)', () => {
  it('settleTodos transitions pending and in_progress todos to completed on terminal turn state', () => {
    const todos: TodoItem[] = [
      { id: '1', content: 'Create component', status: 'completed' },
      { id: '2', content: 'Run tests', status: 'in_progress', activeForm: 'Running tests' },
      { id: '3', content: 'Verify build', status: 'pending' },
    ]
    const settled = settleTodos(todos, 'completed')
    expect(settled.map((t) => t.status)).toEqual(['completed', 'completed', 'completed'])

    const withTodoEvent = applyActivityEvent(createActivityState(), {
      eventId: 'evt-todo-1',
      cursor: '1',
      type: 'todo.updated',
      kind: 'tool',
      state: 'working',
      agentId: 'main',
      turnId: 'turn-1',
      summary: 'Updated todos',
      todos,
    })
    expect(withTodoEvent.todos.some((t) => t.status === 'in_progress')).toBe(true)

    const afterTurnCompleted = applyActivityEvent(withTodoEvent, {
      eventId: 'evt-complete-1',
      cursor: '2',
      type: 'turn.completed',
      kind: 'lifecycle',
      state: 'completed',
      agentId: 'main',
      turnId: 'turn-1',
      summary: 'Turn completed',
    })
    expect(afterTurnCompleted.todos.every((t) => t.status === 'completed')).toBe(true)
  })

  it('activityReducer snapshot settles todos when latest state is terminal', () => {
    const state = activityReducer(createActivityState(), {
      type: 'snapshot',
      cursor: '2',
      events: [
        {
          eventId: 'evt-1',
          cursor: '1',
          type: 'todo.updated',
          kind: 'tool',
          state: 'working',
          agentId: 'main',
          summary: 'Todos',
          todos: [
            { id: '1', content: 'Step 1', status: 'completed' },
            { id: '2', content: 'Step 2', status: 'in_progress' },
          ],
        },
        {
          eventId: 'evt-2',
          cursor: '2',
          type: 'turn.completed',
          kind: 'lifecycle',
          state: 'completed',
          agentId: 'main',
          summary: 'Done',
        },
      ],
    })
    expect(state.todos.map((t) => t.status)).toEqual(['completed', 'completed'])
  })

  it('TodoBar hides pulsing active indicator and collapses by default when isTurnRunning is false or todos > 4', () => {
    const todos: TodoItem[] = [
      { id: '1', content: 'Done task', status: 'completed' },
      { id: '2', content: 'Active task', status: 'in_progress', activeForm: 'Doing active task' },
    ]
    const { container, rerender } = render(<TodoBar todos={todos} isTurnRunning={true} />)
    expect(container.querySelector('.animate-pulse')).not.toBeNull()
    expect(container.querySelector('ul')).not.toBeNull()

    rerender(<TodoBar todos={todos} isTurnRunning={false} />)
    expect(container.querySelector('.animate-pulse')).toBeNull()
    expect(container.querySelector('ul')).toBeNull()

    const fiveTodos: TodoItem[] = [1, 2, 3, 4, 5].map((i) => ({
      id: String(i),
      content: `Task ${i}`,
      status: 'pending',
    }))
    rerender(<TodoBar todos={fiveTodos} isTurnRunning={true} />)
    expect(container.querySelector('ul')).toBeNull()
  })

  it('CodeToolCallBlock marks non-zero exit codes as failed even when event state is completed', () => {
    const failedBashEvent: ConversationActivityEvent = {
      eventId: 'evt-bash-fail',
      cursor: '10',
      type: 'tool.completed',
      kind: 'tool',
      state: 'completed',
      status: 'completed',
      agentId: 'main',
      toolName: 'run_command',
      summary: '$ pytest',
      exitCode: 1,
      bashOutput: {
        command: 'pytest',
        exit_code: 1,
        stderr: '1 failed',
      },
    }
    render(<CodeToolCallBlock event={failedBashEvent} />)
    const block = screen.getByTestId('code-tool-call-block')
    expect(block.getAttribute('data-status')).toBe('failed')
  })

  it('applyActivityEvent evicts assistant.delta events before structural tool events when exceeding EVENT_LIMIT', () => {
    let state = applyActivityEvent(createActivityState(), {
      eventId: 'evt-tool-write',
      cursor: '1',
      type: 'tool.completed',
      kind: 'tool',
      state: 'working',
      agentId: 'main',
      turnId: 'turn-1',
      toolName: 'write_file',
      path: 'src/index.ts',
      summary: 'Wrote src/index.ts',
    })

    for (let i = 0; i < EVENT_LIMIT + 25; i += 1) {
      state = applyActivityEvent(state, {
        eventId: `evt-delta-${i}`,
        cursor: String(i + 2),
        type: 'assistant.delta',
        kind: 'message',
        state: 'working',
        agentId: 'main',
        turnId: 'turn-1',
        messageId: 'msg-1',
        content: 'chunk ',
        summary: 'chunk',
      })
    }

    expect(state.events.length).toBeLessThanOrEqual(EVENT_LIMIT)
    expect(state.events.some((e) => e.eventId === 'evt-tool-write')).toBe(true)
  })

  it('nextCodePermissionMode cycles only user-facing modes and listWorkspaceFiles passes options.query', async () => {
    expect(nextCodePermissionMode('accept_edits')).toBe('plan_mode')
    expect(nextCodePermissionMode('plan_mode')).toBe('ask_permissions')
    expect(nextCodePermissionMode('ask_permissions')).toBe('accept_edits')
    expect(nextCodePermissionMode('full_autonomy')).toBe('ask_permissions')

    const requestSpy = vi.fn().mockImplementation(async (options: { parse: (val: unknown) => unknown }) =>
      options.parse({ files: ['src/App.tsx'] }),
    )
    const mockClient = { request: requestSpy } as unknown as ApiClient
    const files = await listWorkspaceFiles(mockClient, 'conv-123', 'App', 25)
    expect(files).toEqual(['src/App.tsx'])
    expect(requestSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/v1/conversations/conv-123/workspace-files',
        query: { query: 'App', limit: 25 },
      }),
    )
  })

  it('CodeToolCallBlock bounds diffs > 12 lines with an expand toggle and wraps .md diff lines', () => {
    const patchLines = Array.from({ length: 18 }, (_, i) => `+linha adicionada ${i + 1}`)
    const editEvent: ConversationActivityEvent = {
      eventId: 'evt-md-diff',
      cursor: '20',
      type: 'code_mode.file_edited',
      kind: 'tool',
      state: 'completed',
      summary: 'Editou docs/SPEC.md',
      agentId: 'main',
      toolName: 'edit_file',
      path: 'docs/SPEC.md',
      addedLines: 18,
      removedLines: 0,
      structuredPatch: [
        {
          oldStart: 1,
          oldLines: 0,
          newStart: 1,
          newLines: 18,
          lines: patchLines,
        },
      ],
    }

    render(<CodeToolCallBlock event={editEvent} />)

    const expandBtn = screen.getByTestId('code-tool-diff-expand')
    expect(expandBtn).toHaveTextContent('Mostrar todas as 18 linhas (+18 -0)')
    expect(screen.queryByText('+linha adicionada 18')).not.toBeInTheDocument()

    fireEvent.click(expandBtn)
    expect(screen.getByText('+linha adicionada 18')).toBeInTheDocument()
  })

  it('CodeToolCallBlock sanitizes [REDACTED], renders coherent failed badge, normalizes Glob("/"), and renders error detail', () => {
    const failedCancelBash: ConversationActivityEvent = {
      eventId: 'evt-cancel-bash',
      cursor: '21',
      type: 'tool.finished',
      kind: 'tool',
      state: 'failed',
      status: 'failed',
      agentId: 'main',
      toolName: 'run_command',
      command: 'npm init',
      errorCode: 'COMMAND_CANCELLED_INTERACTIVE',
      summary: ' run_command falhou ao iniciar prompt interativo',
      bashOutput: {
        command: 'npm init',
        stdout: '[REDACTED]',
      },
      outputTail: 'Prompt detectado: aguarde entrada do usuário',
      exitCode: 0,
    }

    const { unmount } = render(<CodeToolCallBlock event={failedCancelBash} />)
    expect(screen.getByTestId('code-tool-status-badge')).toHaveTextContent('falhou')
    expect(screen.queryByText('exit 0')).not.toBeInTheDocument()
    expect(screen.getByText('Prompt detectado: aguarde entrada do usuário')).toBeInTheDocument()
    expect(screen.getByTestId('code-tool-error-code')).toHaveTextContent('COMMAND_CANCELLED_INTERACTIVE')
    expect(screen.getByTestId('code-tool-error-detail')).toHaveTextContent(
      'run_command falhou ao iniciar prompt interativo',
    )
    unmount()

    const globEvent: ConversationActivityEvent = {
      eventId: 'evt-glob-root',
      cursor: '22',
      type: 'tool.finished',
      kind: 'tool',
      state: 'completed',
      agentId: 'main',
      toolName: 'list_files',
      path: '/',
      summary: '/',
    }
    render(<CodeToolCallBlock event={globEvent} />)
    expect(screen.getByText('Glob')).toBeInTheDocument()
    expect(screen.getByText('.')).toBeInTheDocument()
  })

  it('renderActivityGroup passes active=false to ToolPermissionCard when the permission group is already settled', () => {
    const settledGroup: ActivityGroup = {
      id: 'approval:tool:main:turn-1:req-1',
      kind: 'tool',
      state: 'completed',
      label: 'Permissão concedida',
      count: 1,
      agentId: 'main',
      failed: false,
      events: [
        {
          eventId: 'evt-perm-1',
          cursor: '30',
          type: 'tool.permission',
          kind: 'tool',
          state: 'completed',
          status: 'succeeded',
          agentId: 'main',
          turnId: 'turn-1',
          summary: 'Permissão resolvida',
          toolPermission: {
            requestId: 'req-1',
            toolName: 'write_file',
            path: 'src/secret.ts',
          },
        },
      ],
    }

    render(<>{renderActivityGroup(settledGroup)}</>)
    const buttons = screen.queryAllByRole('button')
    for (const btn of buttons) {
      expect(btn).toBeDisabled()
    }
  })
})
