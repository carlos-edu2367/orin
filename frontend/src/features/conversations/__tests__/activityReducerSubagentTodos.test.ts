import { describe, expect, it } from 'vitest'
import {
  activityReducer,
  applyActivityEvent,
  createActivityState,
} from '../activityReducer'
import type { ConversationActivityEvent, TodoItem } from '../activityTypes'

const MAIN_TODOS: TodoItem[] = [
  { id: 'todo-a', content: 'Implementar backend', status: 'completed' },
  { id: 'todo-b', content: 'Implementar frontend', status: 'in_progress', activeForm: 'Implementando frontend' },
]

const SUBAGENT_TODOS: TodoItem[] = [
  { id: 'sub-x', content: 'Tarefa interna do subagente', status: 'in_progress', activeForm: 'Executando subtarefa' },
]

const UPDATED_MAIN_TODOS: TodoItem[] = [
  { id: 'todo-a', content: 'Implementar backend', status: 'completed' },
  { id: 'todo-b', content: 'Implementar frontend', status: 'completed' },
]

function makeEvent(
  overrides: Partial<ConversationActivityEvent> & {
    eventId: string
    cursor: string
    type: ConversationActivityEvent['type']
  },
): ConversationActivityEvent {
  return {
    turnId: 'turn-1',
    kind: 'tool',
    state: 'working',
    agentId: 'agent:main',
    summary: overrides.type,
    ...overrides,
  }
}

describe('activityReducer subagent TodoBar isolation', () => {
  it('ignores todos from subagent todo.updated, tool.finished, and code_mode.stage_changed events in applyActivityEvent', () => {
    let state = applyActivityEvent(
      createActivityState(),
      makeEvent({
        eventId: 'main-todo-1',
        cursor: '1',
        type: 'todo.updated',
        agentId: 'agent:main',
        todos: MAIN_TODOS,
      }),
    )
    expect(state.todos).toEqual(MAIN_TODOS)

    state = applyActivityEvent(
      state,
      makeEvent({
        eventId: 'sub-todo-1',
        cursor: '2',
        type: 'todo.updated',
        agentId: 'agent:sub-1',
        parentAgentId: 'agent:main',
        todos: SUBAGENT_TODOS,
      }),
    )
    expect(state.todos).toEqual(MAIN_TODOS)

    state = applyActivityEvent(
      state,
      makeEvent({
        eventId: 'sub-tool-fin-2',
        cursor: '3',
        type: 'tool.finished',
        toolName: 'todo_write',
        agentId: 'agent:sub-1',
        parentAgentId: 'agent:main',
        state: 'completed',
        status: 'succeeded',
        todos: SUBAGENT_TODOS,
      }),
    )
    expect(state.todos).toEqual(MAIN_TODOS)

    state = applyActivityEvent(
      state,
      makeEvent({
        eventId: 'sub-stage-3',
        cursor: '4',
        type: 'code_mode.stage_changed',
        kind: 'lifecycle',
        agentId: 'agent:sub-1',
        parentAgentId: 'agent:main',
        todos: SUBAGENT_TODOS,
      }),
    )
    expect(state.todos).toEqual(MAIN_TODOS)

    // Same event without parentAgentId replaces state.todos
    state = applyActivityEvent(
      state,
      makeEvent({
        eventId: 'main-todo-2',
        cursor: '5',
        type: 'todo.updated',
        agentId: 'agent:main',
        todos: UPDATED_MAIN_TODOS,
      }),
    )
    expect(state.todos).toEqual(UPDATED_MAIN_TODOS)
  })

  it('preserves main agent todos in snapshot, reconcile_snapshot, and prepend_older when mixed with subagent todos', () => {
    const events: ConversationActivityEvent[] = [
      makeEvent({
        eventId: 'ev-1',
        cursor: '1',
        type: 'todo.updated',
        agentId: 'agent:main',
        todos: MAIN_TODOS,
      }),
      makeEvent({
        eventId: 'ev-2',
        cursor: '2',
        type: 'todo.updated',
        agentId: 'agent:sub-1',
        parentAgentId: 'agent:main',
        todos: SUBAGENT_TODOS,
      }),
      makeEvent({
        eventId: 'ev-3',
        cursor: '3',
        type: 'code_mode.stage_changed',
        kind: 'lifecycle',
        agentId: 'agent:sub-2',
        parentAgentId: 'agent:main',
        todos: SUBAGENT_TODOS,
      }),
    ]

    const snapState = activityReducer(createActivityState(), {
      type: 'snapshot',
      cursor: '3',
      events,
    })
    expect(snapState.todos).toEqual(MAIN_TODOS)

    const reconciledState = activityReducer(snapState, {
      type: 'reconcile_snapshot',
      cursor: '4',
      events: [
        ...events,
        makeEvent({
          eventId: 'ev-4',
          cursor: '4',
          type: 'tool.finished',
          toolName: 'todo_write',
          agentId: 'agent:sub-3',
          parentAgentId: 'agent:main',
          todos: SUBAGENT_TODOS,
        }),
      ],
    })
    expect(reconciledState.todos).toEqual(MAIN_TODOS)

    const emptyInitial = activityReducer(createActivityState(), {
      type: 'snapshot',
      cursor: '10',
      events: [
        makeEvent({
          eventId: 'ev-10',
          cursor: '10',
          type: 'tool.started',
          agentId: 'agent:main',
        }),
      ],
    })
    const prepended = activityReducer(emptyInitial, {
      type: 'prepend_older',
      events: [
        makeEvent({
          eventId: 'ev-sub-old',
          cursor: '1',
          type: 'todo.updated',
          agentId: 'agent:sub-1',
          parentAgentId: 'agent:main',
          todos: SUBAGENT_TODOS,
        }),
        makeEvent({
          eventId: 'ev-main-old',
          cursor: '2',
          type: 'todo.updated',
          agentId: 'agent:main',
          todos: MAIN_TODOS,
        }),
      ],
    })
    expect(prepended.todos).toEqual(MAIN_TODOS)
  })

  it('only settles in_progress todos on main agent terminal turn outcome, not on subagent turn.failed', () => {
    let state = applyActivityEvent(
      createActivityState(),
      makeEvent({
        eventId: 'main-todo',
        cursor: '1',
        type: 'todo.updated',
        agentId: 'agent:main',
        todos: MAIN_TODOS,
      }),
    )

    // Subagent failure does not settle main agent's in_progress todo
    state = applyActivityEvent(
      state,
      makeEvent({
        eventId: 'sub-failed',
        cursor: '2',
        type: 'turn.failed',
        kind: 'lifecycle',
        state: 'failed',
        status: 'failed',
        agentId: 'agent:sub-1',
        parentAgentId: 'agent:main',
      }),
    )
    expect(state.todos).toEqual(MAIN_TODOS)

    // Main agent failure settles in_progress todo to pending
    state = applyActivityEvent(
      state,
      makeEvent({
        eventId: 'main-failed',
        cursor: '3',
        type: 'turn.failed',
        kind: 'lifecycle',
        state: 'failed',
        status: 'failed',
        agentId: 'agent:main',
      }),
    )
    expect(state.todos).toEqual([
      { id: 'todo-a', content: 'Implementar backend', status: 'completed' },
      { id: 'todo-b', content: 'Implementar frontend', status: 'pending', activeForm: 'Implementando frontend' },
    ])
  })

  it('does not resurrect state.status to running or overwrite activePhase when late subagent events arrive after main turn.completed or turn.cancelled', () => {
    const baseEvents: ConversationActivityEvent[] = [
      makeEvent({
        eventId: 'main-start',
        cursor: '1',
        type: 'turn.started',
        kind: 'lifecycle',
        state: 'working',
        status: 'running',
        agentId: 'agent:main',
      }),
      makeEvent({
        eventId: 'main-done',
        cursor: '2',
        type: 'turn.completed',
        kind: 'lifecycle',
        state: 'completed',
        status: 'succeeded',
        agentId: 'agent:main',
      }),
    ]

    let state = createActivityState()
    for (const ev of baseEvents) {
      state = applyActivityEvent(state, ev)
    }
    expect(state.status).toBe('completed')
    expect(state.activePhase).toBeNull()

    const lateSubagentCompleted = makeEvent({
      eventId: 'sub-late-completed',
      cursor: '3',
      type: 'turn.completed',
      kind: 'lifecycle',
      state: 'completed',
      status: 'succeeded',
      agentId: 'agent:sub-1',
      parentAgentId: 'agent:main',
      label: 'antigravity-1',
    })
    const lateSubagentDelegationFailed = makeEvent({
      eventId: 'sub-late-delegation-failed',
      cursor: '4',
      type: 'delegation.failed',
      kind: 'agent',
      state: 'failed',
      status: 'failed',
      agentId: 'agent:sub-2',
      parentAgentId: 'agent:main',
      label: 'antigravity-2',
    })

    state = applyActivityEvent(state, lateSubagentCompleted)
    expect(state.status).toBe('completed')
    expect(state.activePhase).toBeNull()

    state = applyActivityEvent(state, lateSubagentDelegationFailed)
    expect(state.status).toBe('completed')
    expect(state.activePhase).toBeNull()

    // Also verify snapshot and reconcile_snapshot with late subagent events at the tail
    const allCompletedEvents = [...baseEvents, lateSubagentCompleted, lateSubagentDelegationFailed]
    const snapCompleted = activityReducer(createActivityState(), {
      type: 'snapshot',
      cursor: '4',
      events: allCompletedEvents,
    })
    expect(snapCompleted.status).toBe('completed')
    expect(snapCompleted.activePhase).toBeNull()

    const reconciledCompleted = activityReducer(snapCompleted, {
      type: 'reconcile_snapshot',
      cursor: '4',
      events: allCompletedEvents,
    })
    expect(reconciledCompleted.status).toBe('completed')
    expect(reconciledCompleted.activePhase).toBeNull()

    // Verify turn.cancelled followed by late subagent cleanup event
    let cancelledState = applyActivityEvent(
      createActivityState(),
      makeEvent({
        eventId: 'main-cancelled',
        cursor: '10',
        type: 'turn.cancelled',
        kind: 'lifecycle',
        state: 'cancelled',
        status: 'cancelled',
        agentId: 'agent:main',
      }),
    )
    expect(cancelledState.status).toBe('cancelled')

    cancelledState = applyActivityEvent(
      cancelledState,
      makeEvent({
        eventId: 'sub-after-cancel',
        cursor: '11',
        type: 'delegation.failed',
        kind: 'agent',
        state: 'failed',
        status: 'failed',
        agentId: 'agent:sub-1',
        parentAgentId: 'agent:main',
      }),
    )
    expect(cancelledState.status).toBe('cancelled')
    expect(cancelledState.activePhase).toBeNull()
  })
})
