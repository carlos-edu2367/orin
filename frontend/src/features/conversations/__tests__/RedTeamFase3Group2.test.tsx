import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { parseConversationActivityEvent } from '../../../api/conversations'
import { CodeModeCard } from '../CodeModeCard'
import { TurnTimeline } from '../TurnTimeline'
import {
  activityReducer,
  createActivityState,
  settleTodos,
} from '../activityReducer'
import { summarizeActivities } from '../activitySummary'
import { buildTurnTimeline } from '../turnTimelineFold'
import type { TodoItem } from '../activityTypes'

describe('RedTeam Fase 3 Group 2 — Completion, Unverified Notice & Todos', () => {
  describe('settleTodos & activityReducer todo invariants', () => {
    const sampleTodos: TodoItem[] = [
      { id: '1', content: 'Ler arquivo', status: 'completed' },
      { id: '2', content: 'Editar módulo', status: 'in_progress', activeForm: 'Editando módulo' },
      { id: '3', content: 'Rodar suíte de testes', status: 'pending' },
    ]

    it('preserves in_progress and pending todos untouched when turn completes', () => {
      const settled = settleTodos(sampleTodos, 'completed')
      expect(settled).toEqual(sampleTodos)
      expect(settled.find((t) => t.id === '2')?.status).toBe('in_progress')
      expect(settled.find((t) => t.id === '3')?.status).toBe('pending')
    })

    it('reverts in_progress to pending on failed and cancelled without marking anything completed', () => {
      for (const terminal of ['failed', 'cancelled'] as const) {
        const settled = settleTodos(sampleTodos, terminal)
        expect(settled.map((t) => ({ id: t.id, status: t.status }))).toEqual([
          { id: '1', status: 'completed' },
          { id: '2', status: 'pending' },
          { id: '3', status: 'pending' },
        ])
        expect(settled.filter((t) => t.status === 'completed')).toHaveLength(1)
      }
    })

    it('activityReducer does not auto-complete todos on turn.completed and reverts in_progress on turn.failed', () => {
      let state = createActivityState()
      const todoEvent = parseConversationActivityEvent(
        {
          event_id: 'evt-todo-1',
          event_type: 'todo.updated',
          turn_id: 'turn-1',
          agent_id: 'agent:main',
          summary: 'Lista de tarefas (1/3 concluídas)',
          payload: {
            todos: [
              { id: 't1', content: 'Passo 1', status: 'completed' },
              { id: 't2', content: 'Passo 2', status: 'in_progress', active_form: 'Fazendo passo 2' },
              { id: 't3', content: 'Passo 3', status: 'pending' },
            ],
          },
        },
        '1',
      )
      state = activityReducer(state, { type: 'event', event: todoEvent })
      expect(state.todos.map((t) => t.status)).toEqual(['completed', 'in_progress', 'pending'])

      const completedEvent = parseConversationActivityEvent(
        {
          event_id: 'evt-done-1',
          event_type: 'turn.completed',
          turn_id: 'turn-1',
          agent_id: 'agent:main',
          summary: 'Turno concluído',
          payload: {},
        },
        '2',
      )
      const afterComplete = activityReducer(state, { type: 'event', event: completedEvent })
      expect(afterComplete.status).toBe('completed')
      expect(afterComplete.todos.map((t) => t.status)).toEqual(['completed', 'in_progress', 'pending'])

      const failedEvent = parseConversationActivityEvent(
        {
          event_id: 'evt-fail-1',
          event_type: 'turn.failed',
          turn_id: 'turn-1',
          agent_id: 'agent:main',
          summary: 'Turno falhou',
          payload: {},
        },
        '3',
      )
      const afterFail = activityReducer(state, { type: 'event', event: failedEvent })
      expect(afterFail.status).toBe('failed')
      expect(afterFail.todos.map((t) => t.status)).toEqual(['completed', 'pending', 'pending'])
    })
  })

  describe('BUG REPRO: Real backend code_mode.unverified event in CodeModeCard and activityReducer', () => {
    it('BUG REPRO: parseConversationActivityEvent + summarizeActivities + CodeModeCard hides UnverifiedNotice and shows "Em andamento" when turn has no plan', () => {
      // Real sequence emitted by backend for a direct edit turn in Code mode (no plan -> _settle_code_mode returns early at `if not plan: return`)
      const events = [
        parseConversationActivityEvent(
          {
            event_id: 'e1',
            event_type: 'turn.started',
            turn_id: 'turn-1',
            agent_id: 'agent:main',
            summary: 'Turno iniciado',
            payload: {},
          },
          '1',
        ),
        parseConversationActivityEvent(
          {
            event_id: 'e2',
            event_type: 'code_mode.unverified',
            turn_id: 'turn-1',
            agent_id: 'agent:main',
            summary: 'Entrega não verificada',
            payload: { verification: 'unverified', changed_paths: 1 },
          },
          '2',
        ),
        parseConversationActivityEvent(
          {
            event_id: 'e3',
            event_type: 'turn.completed',
            turn_id: 'turn-1',
            agent_id: 'agent:main',
            summary: 'Turno concluído',
            payload: {},
          },
          '3',
        ),
      ]

      const groups = summarizeActivities(events)
      const codeGroup = groups.find((g) => g.id.startsWith('code-mode:'))
      expect(codeGroup).toBeDefined()

      render(<CodeModeCard group={codeGroup!} />)

      // Because the turn finished with code_mode.unverified, CodeModeCard MUST show "Concluído · não verificado"
      // and MUST render data-testid="code-unverified" (instead of "Em andamento" with no notice)!
      expect(screen.getByText('Concluído · não verificado')).toBeInTheDocument()
      expect(screen.getByTestId('code-unverified')).toHaveTextContent('Não verificado')
    })

    it('BUG REPRO: code_mode.completed emitted by _settle_code_mode in finally after turn.completed resets activityReducer status from "completed" to "running"', () => {
      // In workers/chat.py:635 & 694, runtime.run() emits turn.completed, and then the finally block calls
      // _settle_code_mode() which emits code_mode.completed (or code_mode.unverified).
      let state = createActivityState()
      const turnStarted = parseConversationActivityEvent(
        {
          event_id: 'e1',
          event_type: 'turn.started',
          turn_id: 'turn-1',
          agent_id: 'agent:main',
          summary: 'Turno iniciado',
          payload: {},
        },
        '1',
      )
      const turnCompleted = parseConversationActivityEvent(
        {
          event_id: 'e2',
          event_type: 'turn.completed',
          turn_id: 'turn-1',
          agent_id: 'agent:main',
          summary: 'Turno concluído',
          payload: {},
        },
        '2',
      )
      const codeModeCompleted = parseConversationActivityEvent(
        {
          event_id: 'e3',
          event_type: 'code_mode.completed',
          turn_id: 'turn-1',
          agent_id: 'agent:main',
          summary: 'Execução concluída no Modo Code',
          payload: { stage: 'completed', completion_kind: 'unverified' },
        },
        '3',
      )

      state = activityReducer(state, { type: 'event', event: turnStarted })
      state = activityReducer(state, { type: 'event', event: turnCompleted })
      expect(state.status).toBe('completed')

      state = activityReducer(state, { type: 'event', event: codeModeCompleted })
      // Must remain 'completed', NOT revert to 'running'!
      expect(state.status).toBe('completed')
    })

    it('BUG REPRO: buildTurnTimeline hoists code_mode.unverified before tool calls and assistant response instead of placing it at turn end', () => {
      const events = [
        parseConversationActivityEvent(
          {
            event_id: 'e1',
            event_type: 'turn.started',
            turn_id: 'turn-1',
            agent_id: 'agent:main',
            summary: 'Turno iniciado',
            payload: {},
          },
          '1',
        ),
        parseConversationActivityEvent(
          {
            event_id: 'e2',
            event_type: 'tool.completed',
            turn_id: 'turn-1',
            agent_id: 'agent:main',
            summary: 'Arquivo editado: app.py',
            payload: { tool_name: 'edit_file', path: 'app.py' },
          },
          '2',
        ),
        parseConversationActivityEvent(
          {
            event_id: 'e3',
            event_type: 'code_mode.unverified',
            turn_id: 'turn-1',
            agent_id: 'agent:main',
            summary: 'Entrega não verificada',
            payload: { verification: 'unverified', changed_paths: 1 },
          },
          '3',
        ),
        parseConversationActivityEvent(
          {
            event_id: 'e4',
            event_type: 'turn.completed',
            turn_id: 'turn-1',
            agent_id: 'agent:main',
            summary: 'Turno concluído',
            payload: {},
          },
          '4',
        ),
      ]

      const items = buildTurnTimeline(events, {
        turnId: 'turn-1',
        completeContent: 'Editei o arquivo app.py.',
      })
      const codeIdx = items.findIndex((item) => item.id.startsWith('code-mode:'))
      const toolIdx = items.findIndex((item) => item.kind === 'activity' && !item.id.startsWith('code-mode:'))

      expect(codeIdx).toBeGreaterThanOrEqual(0)
      expect(toolIdx).toBeGreaterThanOrEqual(0)
      // When code_mode.unverified is the only code_mode.* event (emitted at turn completion),
      // the notice should appear AFTER the tool edits, not hoisted before them at index 0!
      expect(codeIdx).toBeGreaterThan(toolIdx)

      render(<TurnTimeline items={items} mode="code" turnCompleted={true} />)
      const toolBlock = screen.getByTestId('code-tool-call-block')
      const unverifiedNotice = screen.getByTestId('code-unverified')
      expect(unverifiedNotice).toHaveTextContent('Não verificado')
      expect(
        Boolean(toolBlock.compareDocumentPosition(unverifiedNotice) & Node.DOCUMENT_POSITION_FOLLOWING),
      ).toBe(true)
    })
  })
})
