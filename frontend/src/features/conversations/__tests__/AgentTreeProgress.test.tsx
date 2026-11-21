import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { KeybindingsProvider } from '../../keybindings/engine'
import { AgentProgressRow } from '../AgentProgressRow'
import { TasksPanel } from '../TasksPanel'
import {
  hydrateAgentRuns,
  reduceAgentProgressEvents,
  type AgentProgressData,
} from '../agentTree'
import type { ConversationActivityEvent } from '../activityTypes'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function makeEvent(partial: Partial<ConversationActivityEvent>): ConversationActivityEvent {
  return {
    ...partial,
    eventId: partial.eventId ?? 'ev-1',
    cursor: partial.cursor ?? '1',
    type: partial.type ?? 'agent.progress',
    kind: partial.kind ?? 'agent',
    state: partial.state ?? 'working',
    summary: partial.summary ?? 'Progresso do subagente',
    agentId: partial.agentId ?? 'sub-1',
    parentAgentId: partial.parentAgentId ?? 'main',
    occurredAt: partial.occurredAt ?? '2026-11-21T19:00:00Z',
    payload: partial.payload ?? {},
  }
}

describe('AgentTreeProgress — progresso, travado, interrompido e retomada (WS-B9)', () => {
  it('renders AgentProgressRow with toolCalls, currentTool, elapsedSeconds, tokens, costUsd, stalled badge and mergeState', () => {
    const progress: AgentProgressData = {
      agentId: 'sub-worker-1',
      state: 'stalled',
      stalled: true,
      interrupted: false,
      elapsedSeconds: 685,
      lastActivityAt: '2026-11-21T18:50:00Z',
      currentTool: 'Bash',
      toolCalls: 7,
      inputTokens: 4200,
      outputTokens: 1350,
      costUsd: 0.0425,
      mergeState: 'conflict',
      parentAgentId: 'main',
      resultRef: null,
    }

    render(<AgentProgressRow progress={progress} conversationId="conv-1" />)

    const row = screen.getByTestId('agent-progress-sub-worker-1')
    expect(row).toHaveTextContent('Bash')
    expect(row).toHaveTextContent('7 ferramentas')
    expect(row).toHaveTextContent('11 min 25 s')
    expect(row).toHaveTextContent(/5\.550|5,550|5550/)
    expect(row).toHaveTextContent('$0.0425')
    expect(within(row).getByTestId('agent-stalled-badge')).toHaveTextContent(/travado/i)
    expect(within(row).getByTestId('agent-merge-state-badge')).toHaveTextContent(/conflito/i)
    expect(within(row).queryByRole('button', { name: /Retomar/i })).not.toBeInTheDocument()
  })

  it('reduces agent.progress, agent.stalled, agent.interrupted and merge.* events into AgentProgressMap', () => {
    const initial = hydrateAgentRuns([
      {
        agent_id: 'sub-1',
        parent_agent_id: 'main',
        state: 'running',
        tool_calls: 2,
        input_tokens: 1000,
        output_tokens: 200,
        cost_usd: 0.01,
        merge_state: 'pending',
      },
    ])

    const updated = reduceAgentProgressEvents(
      [
        makeEvent({
          eventId: 'ev-progress',
          type: 'agent.progress',
          agentId: 'sub-1',
          payload: {
            agent_id: 'sub-1',
            state: 'running',
            current_tool: 'Edit',
            tool_calls: 5,
            elapsed_seconds: 95,
            input_tokens: 3000,
            output_tokens: 800,
            cost_usd: 0.031,
          },
        }),
        makeEvent({
          eventId: 'ev-stalled',
          type: 'agent.stalled',
          agentId: 'sub-1',
          payload: {
            agent_id: 'sub-1',
            stalled: true,
          },
        }),
        makeEvent({
          eventId: 'ev-merge',
          type: 'merge.applied',
          kind: 'lifecycle',
          agentId: 'sub-1',
          payload: {
            agent_id: 'sub-1',
            files: ['src/app.ts'],
          },
        }),
      ],
      initial,
    )

    expect(updated['sub-1'].currentTool).toBe('Edit')
    expect(updated['sub-1'].toolCalls).toBe(5)
    expect(updated['sub-1'].elapsedSeconds).toBe(95)
    expect(updated['sub-1'].stalled).toBe(true)
    expect(updated['sub-1'].mergeState).toBe('applied')
  })

  it('loads agent runs in TasksPanel, displays stalled/interrupted/merge_state and resumes via POST /v1/conversations/{id}/agents/{agent_id}/resume', async () => {
    const user = userEvent.setup()
    let sub2State = 'interrupted'
    const resumeCalls: string[] = []

    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.includes('/tasks')) {
        return jsonResponse({ tasks: [] })
      }
      if (url.includes('/overview')) {
        return jsonResponse({
          conversation_id: 'conv-42',
          title: 'Sessão de teste',
          state: 'running',
          agents: [
            {
              agent_id: 'main',
              name: 'Orin',
              role: 'leader',
              state: 'working',
              parent_agent_id: null,
            },
            {
              agent_id: 'sub-stalled',
              name: 'Pesquisador',
              role: 'explore',
              state: 'working',
              parent_agent_id: 'main',
            },
            {
              agent_id: 'sub-interrupted',
              name: 'Implementador',
              role: 'general-purpose',
              state: sub2State,
              parent_agent_id: 'main',
            },
          ],
          artifacts: [],
          activities: [],
        })
      }
      if (url.includes('/agent-runs')) {
        return jsonResponse({
          conversation_id: 'conv-42',
          runs: [
            {
              agent_id: 'sub-stalled',
              user_id: 'u1',
              conversation_id: 'conv-42',
              turn_id: 't1',
              parent_agent_id: 'main',
              subagent_type: 'explore',
              provider: 'anthropic',
              model_id: 'claude-sonnet',
              effort: 'medium',
              state: 'stalled',
              task_digest: 'Mapear módulos',
              history_path: null,
              worktree_path: null,
              pipeline_run_id: null,
              last_activity_at: '2026-11-21T18:45:00Z',
              elapsed_seconds: 660,
              current_tool: 'Grep',
              tool_calls: 4,
              input_tokens: 1800,
              output_tokens: 450,
              cost_usd: 0.015,
              result_ref: null,
              created_at: '2026-11-21T18:35:00Z',
              finished_at: null,
              stalled: true,
              merge_state: null,
            },
            {
              agent_id: 'sub-interrupted',
              user_id: 'u1',
              conversation_id: 'conv-42',
              turn_id: 't1',
              parent_agent_id: 'main',
              subagent_type: 'general-purpose',
              provider: 'anthropic',
              model_id: 'claude-sonnet',
              effort: 'high',
              state: sub2State,
              task_digest: 'Refatorar API',
              history_path: '.orin/agent_runs/sub-interrupted.json',
              worktree_path: '/repo/.orin/worktrees/agent-impl',
              pipeline_run_id: null,
              last_activity_at: '2026-11-21T18:55:00Z',
              elapsed_seconds: 120,
              current_tool: 'Edit',
              tool_calls: 9,
              input_tokens: 5100,
              output_tokens: 1900,
              cost_usd: 0.052,
              result_ref: null,
              created_at: '2026-11-21T18:53:00Z',
              finished_at: null,
              stalled: false,
              merge_state: 'pending',
            },
          ],
        })
      }
      if (url.includes('/agents/sub-interrupted/resume') && init?.method === 'POST') {
        resumeCalls.push(url)
        sub2State = 'queued'
        return jsonResponse(
          {
            agent_id: 'sub-interrupted',
            conversation_id: 'conv-42',
            state: 'queued',
            resumed: true,
            history_messages: 6,
            history_corrupted: false,
            warning: null,
            notice: 'Retomado do último histórico salvo.',
            dispatch: { dispatched: true },
          },
          202,
        )
      }
      if (url.includes('/messages')) {
        return jsonResponse({ conversation_id: 'conv-42', agent_id: 'sub-stalled', messages: [] })
      }
      if (url.includes('/v1/settings/keybindings')) {
        return jsonResponse({ bindings: [], errors: [] })
      }
      throw new Error(`Unexpected URL: ${url}`)
    })

    const client = new ApiClient({ fetchImpl: fetchMock })

    render(
      <KeybindingsProvider client={client}>
        <TasksPanel client={client} conversationId="conv-42" onClose={() => {}} />
      </KeybindingsProvider>,
    )

    expect(await screen.findByText('Pesquisador')).toBeInTheDocument()
    expect(screen.getByText('Implementador')).toBeInTheDocument()

    // Stalled subagent displays progress and stalled badge
    const stalledProgress = await screen.findByTestId('agent-progress-sub-stalled')
    expect(within(stalledProgress).getByTestId('agent-stalled-badge')).toHaveTextContent(/travado/i)
    expect(stalledProgress).toHaveTextContent('Grep')
    expect(stalledProgress).toHaveTextContent('4 ferramentas')

    // Interrupted subagent displays interrupted badge, merge_state and Retomar button
    const interruptedProgress = screen.getByTestId('agent-progress-sub-interrupted')
    expect(within(interruptedProgress).getByTestId('agent-interrupted-badge')).toHaveTextContent(/interrompido/i)
    expect(within(interruptedProgress).getByTestId('agent-merge-state-badge')).toHaveTextContent(/pendente/i)
    expect(interruptedProgress).toHaveTextContent('9 ferramentas')

    const resumeBtn = within(interruptedProgress).getByRole('button', { name: /Retomar/i })
    await user.click(resumeBtn)

    await waitFor(() => {
      expect(resumeCalls).toHaveLength(1)
      expect(resumeCalls[0]).toContain('/v1/conversations/conv-42/agents/sub-interrupted/resume')
    })

    await waitFor(() => {
      expect(within(interruptedProgress).queryByRole('button', { name: /^Retomar$/i })).not.toBeInTheDocument()
    })
  })
})
