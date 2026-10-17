import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../../api/client'
import type { ConversationActivityEvent } from '../activityTypes'
import { summarizeActivities } from '../activitySummary'
import { buildTurnTimeline } from '../turnTimelineFold'
import { TurnTimeline } from '../TurnTimeline'
import { WorkspaceNavigation } from '../../projects/WorkspaceNavigation'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function makeSubagentToolEvent(
  overrides: Partial<ConversationActivityEvent> & {
    eventId: string
    agentId: string
    agentName: string
    toolName: string
    summary: string
  },
): ConversationActivityEvent {
  return {
    cursor: '1',
    turnId: 'turn-1',
    parentAgentId: 'agent:main',
    kind: 'tool',
    type: 'tool.finished',
    state: 'completed',
    status: 'succeeded',
    toolKind: 'filesystem',
    occurredAt: '2026-10-17T12:00:00Z',
    ...overrides,
  }
}

describe('Parallel Subagents Timeline & Folding', () => {
  it('coalesces interleaved tool events from parallel subagents into one group per subagent', () => {
    const events: ConversationActivityEvent[] = [
      makeSubagentToolEvent({
        eventId: 'ev-1',
        cursor: '1',
        agentId: 'agent:sub-1',
        agentName: 'explore',
        toolName: 'read_file',
        path: 'src/a.ts',
        summary: 'Leu src/a.ts',
      }),
      makeSubagentToolEvent({
        eventId: 'ev-2',
        cursor: '2',
        agentId: 'agent:sub-2',
        agentName: 'explore-2',
        toolName: 'read_file',
        path: 'src/b.ts',
        summary: 'Leu src/b.ts',
      }),
      makeSubagentToolEvent({
        eventId: 'ev-3',
        cursor: '3',
        agentId: 'agent:sub-1',
        agentName: 'explore',
        toolName: 'search_files',
        summary: 'Buscou foo em src/',
      }),
      makeSubagentToolEvent({
        eventId: 'ev-4',
        cursor: '4',
        agentId: 'agent:sub-2',
        agentName: 'explore-2',
        toolName: 'search_files',
        summary: 'Buscou bar em src/',
      }),
    ]

    const summarized = summarizeActivities(events)
    expect(summarized).toHaveLength(2)
    expect(summarized[0].agentId).toBe('agent:sub-1')
    expect(summarized[0].events).toHaveLength(2)
    expect(summarized[1].agentId).toBe('agent:sub-2')
    expect(summarized[1].events).toHaveLength(2)

    const timeline = buildTurnTimeline(events, 'turn-1', 'msg-1', '', false)
    const activityItems = timeline.filter((item) => item.kind === 'activity')
    expect(activityItems).toHaveLength(2)
    expect(activityItems[0].group.agentId).toBe('agent:sub-1')
    expect(activityItems[0].group.events).toHaveLength(2)
    expect(activityItems[1].group.agentId).toBe('agent:sub-2')
    expect(activityItems[1].group.events).toHaveLength(2)
  })

  it('renders a parallel subagents tree container with distinct branches and live status badges', () => {
    const events: ConversationActivityEvent[] = [
      makeSubagentToolEvent({
        eventId: 'ev-main-1',
        cursor: '1',
        agentId: 'agent:main',
        agentName: 'Main',
        parentAgentId: undefined,
        toolName: 'read_file',
        path: 'README.md',
        summary: 'Leu README.md',
      }),
      makeSubagentToolEvent({
        eventId: 'ev-sub1-1',
        cursor: '2',
        agentId: 'agent:sub-1',
        agentName: 'explore',
        toolName: 'read_file',
        path: 'src/auth.py',
        summary: 'Leu src/auth.py',
      }),
      makeSubagentToolEvent({
        eventId: 'ev-sub2-1',
        cursor: '3',
        agentId: 'agent:sub-2',
        agentName: 'explore-2',
        type: 'tool.started',
        state: 'waiting_tool',
        status: 'running',
        toolName: 'search_files',
        summary: 'Executando search_files',
      }),
      makeSubagentToolEvent({
        eventId: 'ev-sub1-2',
        cursor: '4',
        agentId: 'agent:sub-1',
        agentName: 'explore',
        toolName: 'search_files',
        summary: 'Buscou token em src/',
      }),
    ]

    render(<TurnTimeline events={events} mode="code" turnCompleted={false} />)

    const parallelGroup = screen.getByTestId('parallel-subagents-group')
    expect(parallelGroup).toBeTruthy()
    expect(parallelGroup.textContent).toContain('2 subagentes')

    const subagentCards = screen.getAllByTestId('subagent-task-group')
    expect(subagentCards).toHaveLength(2)
    expect(subagentCards[0].getAttribute('data-agent-id')).toBe('agent:sub-1')
    expect(subagentCards[0].getAttribute('data-status')).toBe('completed')
    expect(subagentCards[1].getAttribute('data-agent-id')).toBe('agent:sub-2')
    expect(subagentCards[1].getAttribute('data-status')).toBe('running')
  })

  it('keeps two parallel subagent waves separated when main agent acts between them and marks failed subagents', () => {
    const events: ConversationActivityEvent[] = [
      // Wave 1: sub-1 and sub-2
      makeSubagentToolEvent({
        eventId: 'w1-sub1',
        cursor: '1',
        agentId: 'agent:sub-1',
        agentName: 'explore',
        toolName: 'read_file',
        path: 'src/w1a.ts',
        summary: 'Leu src/w1a.ts',
      }),
      makeSubagentToolEvent({
        eventId: 'w1-sub2',
        cursor: '2',
        agentId: 'agent:sub-2',
        agentName: 'explore-2',
        toolName: 'read_file',
        path: 'src/w1b.ts',
        summary: 'Leu src/w1b.ts',
      }),
      {
        eventId: 'w1-done',
        cursor: '3',
        turnId: 'turn-1',
        agentId: 'agent:main',
        kind: 'agent',
        type: 'agent.message_received',
        state: 'completed',
        label: 'explore',
        summary: 'Recebeu a resposta de explore',
      },
      // Main agent tool call separates Wave 1 and Wave 2
      makeSubagentToolEvent({
        eventId: 'main-mid',
        cursor: '4',
        agentId: 'agent:main',
        agentName: 'Main',
        parentAgentId: undefined,
        toolName: 'edit_file',
        path: 'src/index.ts',
        summary: 'Editou src/index.ts',
      }),
      // Wave 2: sub-1 resumed and sub-3 fails
      makeSubagentToolEvent({
        eventId: 'w2-sub1',
        cursor: '5',
        agentId: 'agent:sub-1',
        agentName: 'explore',
        toolName: 'search_files',
        summary: 'Buscou final em src/',
      }),
      makeSubagentToolEvent({
        eventId: 'w2-sub3-fail',
        cursor: '6',
        agentId: 'agent:sub-3',
        agentName: 'verifier',
        type: 'tool.finished',
        state: 'failed',
        status: 'failed',
        toolName: 'run_command',
        command: 'pytest',
        summary: 'pytest failed',
      }),
    ]

    render(<TurnTimeline events={events} mode="code" turnCompleted={true} />)

    const parallelGroups = screen.getAllByTestId('parallel-subagents-group')
    expect(parallelGroups).toHaveLength(2)

    const allSubagentCards = screen.getAllByTestId('subagent-task-group')
    expect(allSubagentCards).toHaveLength(4)
    expect(allSubagentCards[3].getAttribute('data-agent-id')).toBe('agent:sub-3')
    expect(allSubagentCards[3].getAttribute('data-status')).toBe('failed')
    expect(allSubagentCards[3].textContent).toContain('Falhou')
  })
})

describe('WorkspaceNavigation Multi-Session Active Indicators', () => {
  it('shows active sessions count badge, deduplicates project chats, and refreshes on orin:conversations-updated', async () => {
    let callCount = 0
    const fakeClient = {
      request: vi.fn(async ({ path }: { path: string }) => {
        if (path === '/v1/conversations') {
          callCount += 1
          if (callCount === 1) {
            return {
              items: [
                { conversation_id: 'conv-1', title: 'Refatorar Backend', state: 'running', mode: 'code' },
                { conversation_id: 'conv-2', title: 'Revisar UI', state: 'streaming', mode: 'chat' },
                { conversation_id: 'conv-3', title: 'Sessão Antiga', state: 'completed', mode: 'chat' },
              ],
            }
          }
          return {
            items: [
              { conversation_id: 'conv-1', title: 'Refatorar Backend', state: 'completed', mode: 'code' },
              { conversation_id: 'conv-2', title: 'Revisar UI', state: 'running', mode: 'chat' },
              { conversation_id: 'conv-3', title: 'Sessão Antiga', state: 'completed', mode: 'chat' },
            ],
          }
        }
        if (path === '/v1/projects/sidebar') {
          return {
            items: [
              {
                project_id: 'proj-1',
                name: 'Orin',
                description: '',
                chats: [
                  // conv-1 also listed inside project — must be deduplicated in activeSessionCount
                  { conversation_id: 'conv-1', title: 'Refatorar Backend', state: callCount <= 1 ? 'running' : 'completed', mode: 'code' },
                ],
              },
            ],
          }
        }
        return {}
      }),
    } as unknown as ApiClient

    render(
      <MemoryRouter initialEntries={['/chats/conv-1']}>
        <WorkspaceNavigation client={fakeClient} />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('sidebar-active-sessions-badge')).toBeTruthy()
    })
    expect(screen.getByTestId('sidebar-active-sessions-badge').textContent).toContain('2 ativas')
    expect(screen.getByTestId('conversation-state-conv-1').getAttribute('data-state')).toBe('running')
    expect(screen.getByTestId('conversation-state-conv-2').getAttribute('data-state')).toBe('streaming')
    expect(screen.getByTestId('conversation-state-conv-3').getAttribute('data-state')).toBe('completed')

    // Dispatching orin:conversations-updated should reload and update the active count to 1
    fireEvent(window, new CustomEvent('orin:conversations-updated'))

    await waitFor(() => {
      expect(screen.getByTestId('sidebar-active-sessions-badge').textContent).toContain('1 ativa')
    })
    expect(screen.getByTestId('conversation-state-conv-1').getAttribute('data-state')).toBe('completed')
    expect(screen.getByTestId('conversation-state-conv-2').getAttribute('data-state')).toBe('running')
  })
})
