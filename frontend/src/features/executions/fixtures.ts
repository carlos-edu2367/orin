import type { ClientEvent, ExecutionState, ExecutionView } from '../../api/types'

export type ExecutionStatus = ExecutionState
export type ExecutionFixture = ExecutionView

/**
 * A static `DelegationCreated` fact plus one Tool invocation for
 * `/execution/fixture-collaborating` (Fase 6, IMPLEMENTATION_PLAN.md "Decisões
 * locais registradas para a Fase 6"). `ExecutionRoute` never populates
 * `ExecutionPage`'s `events` prop today — only the live realtime store's
 * `seenEventIds` survive a binding, not full event payloads (Fase 3 decision) — and
 * no production adapter delivers delegation or Tool Runtime events to the public
 * stream yet (`BACKEND_DISCOVERY.md`). This fixture is the only deterministic,
 * offline-navigable way to exercise `AgentRail`/`OrchestrationScene` with a real
 * observed edge, and `ToolActivityGroup` with a real invocation, in a real browser —
 * exactly like the other `/execution/fixture-*` routes already do for lifecycle
 * states. The payload shapes match the same locally assumed fields documented in
 * Fase 3 (`ToolActivityView`) and Fase 4 (`agentGraphProjection`).
 */
export const collaborationFixtureEvents: ClientEvent[] = [
  {
    event_id: 'evt-fixture-delegation-1',
    event_type: 'DelegationCreated',
    execution_id: 'exec-orbit-running',
    sequence: 1,
    occurred_at: '2026-08-07T10:20:05.000Z',
    payload: {
      delegation_id: 'delegation-fixture-1',
      child_execution_id: 'exec-orbit-child',
      child_agent_id: 'agent-cartographer',
    },
  },
  {
    event_id: 'evt-fixture-tool-1',
    event_type: 'ToolStarted',
    execution_id: 'exec-orbit-running',
    sequence: 2,
    occurred_at: '2026-08-07T10:20:06.000Z',
    payload: { invocation_id: 'invocation-fixture-1', tool_kind: 'search', state: 'running' },
  },
  {
    event_id: 'evt-fixture-tool-2',
    event_type: 'ToolFinished',
    execution_id: 'exec-orbit-running',
    sequence: 3,
    occurred_at: '2026-08-07T10:20:07.000Z',
    payload: {
      invocation_id: 'invocation-fixture-1',
      tool_kind: 'search',
      state: 'succeeded',
      summary: 'Resultado observado sem conteúdo sensível.',
    },
  },
]

export const fixtureExecutions: Record<string, ExecutionFixture> = {
  running: {
    execution_id: 'exec-orbit-running',
    agent_id: 'agent-orbit',
    state: 'RUNNING',
    state_version: 12,
    parent_execution_id: null,
    created_at: '2026-08-07T10:20:00.000Z',
    updated_at: '2026-08-07T10:20:08.000Z',
    finished_at: null,
    result: null,
    failure: null,
  },
  waiting: {
    execution_id: 'exec-orbit-waiting',
    agent_id: 'agent-orbit',
    state: 'WAITING_USER',
    state_version: 7,
    parent_execution_id: null,
    created_at: '2026-08-07T10:20:00.000Z',
    updated_at: '2026-08-07T10:20:08.000Z',
    finished_at: null,
    result: null,
    failure: null,
  },
  completed: {
    execution_id: 'exec-orbit-completed',
    agent_id: 'agent-orbit',
    state: 'COMPLETED',
    state_version: 16,
    parent_execution_id: null,
    created_at: '2026-08-07T10:20:00.000Z',
    updated_at: '2026-08-07T10:20:22.000Z',
    finished_at: '2026-08-07T10:20:22.000Z',
    result: { display_text: 'A execução terminou sem conteúdo adicional para exibir.', result_ref: 'opaque-result-completed' },
    failure: null,
  },
  failed: {
    execution_id: 'exec-orbit-failed',
    agent_id: 'agent-orbit',
    state: 'FAILED',
    state_version: 9,
    parent_execution_id: null,
    created_at: '2026-08-07T10:20:00.000Z',
    updated_at: '2026-08-07T10:20:14.000Z',
    finished_at: '2026-08-07T10:20:14.000Z',
    result: null,
    failure: { code: 'EXECUTION_FAILED' },
  },
  cancelled: {
    execution_id: 'exec-orbit-cancelled',
    agent_id: 'agent-orbit',
    state: 'CANCELLED',
    state_version: 10,
    parent_execution_id: null,
    created_at: '2026-08-07T10:20:00.000Z',
    updated_at: '2026-08-07T10:20:16.000Z',
    finished_at: '2026-08-07T10:20:16.000Z',
    result: null,
    failure: null,
  },
}
