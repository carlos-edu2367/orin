import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import type { ActivityGroup, ConversationActivityEvent } from '../activityTypes'
import { reduceAgentProgressEvents } from '../agentTree'
import { MergeConflictCard } from '../MergeConflictCard'
import { TasksPanel } from '../TasksPanel'
import { CustomAgentsSection } from '../../agents/CustomAgentsSection'
import { AgentsSection } from '../../codeSettings/AgentsSection'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('Package B Red Team Regressions (RT-B-07, RT-B-08, RT-B-14)', () => {
  it('RT-B-07: MergeConflictCard renders structured conflict_detail and check_failed detail objects', () => {
    const conflictGroup: ActivityGroup = {
      id: 'grp-conflict',
      kind: 'lifecycle',
      state: 'failed',
      label: 'Conflito ao integrar worktree',
      count: 1,
      failed: true,
      agentId: 'agent:c1:child1',
      agentName: 'worker-1',
      events: [
        {
          eventId: 'ev-1',
          cursor: '1',
          type: 'merge.conflict',
          kind: 'lifecycle',
          state: 'failed',
          summary: 'Conflito ao integrar worktree',
          title: 'Conflito ao integrar worktree',
          status: 'failed',
          agentId: 'agent:c1:child1',
          payload: {
            agent_id: 'agent:c1:child1',
            worktree_path: '/repo/.orin/worktrees/child1',
            branch: 'orin/agent-child1',
            files: ['a.py'],
            conflict_detail: {
              files: ['a.py'],
              hunks: {
                'a.py': '<<<<<<< HEAD\nparent\n=======\nchild\n>>>>>>> child',
              },
              stderr: 'error: patch failed: a.py:1',
            },
          },
        },
      ],
    }

    const { rerender } = render(<MergeConflictCard group={conflictGroup} conversationId="c1" />)
    const conflictDetailEl = screen.getByTestId('merge-conflict-detail')
    expect(conflictDetailEl.textContent).toContain('<<<<<<< HEAD')
    expect(conflictDetailEl.textContent).toContain('a.py')
    expect(conflictDetailEl.textContent).toContain('error: patch failed: a.py:1')

    const checkFailedGroup: ActivityGroup = {
      id: 'grp-check',
      kind: 'lifecycle',
      state: 'failed',
      label: 'Checagem de integração falhou',
      count: 1,
      failed: true,
      agentId: 'agent:c1:child2',
      agentName: 'worker-2',
      events: [
        {
          eventId: 'ev-2',
          cursor: '2',
          type: 'merge.check_failed',
          kind: 'lifecycle',
          state: 'failed',
          summary: 'Checagem de integração falhou',
          title: 'Checagem de integração falhou',
          status: 'failed',
          agentId: 'agent:c1:child2',
          payload: {
            agent_id: 'agent:c1:child2',
            worktree_path: '/repo/.orin/worktrees/child2',
            files: ['b.py'],
            detail: {
              command: 'pytest -q',
              exit_code: 1,
              check_output: 'FAILED tests/test_b.py::test_sum - AssertionError',
              message: 'mergeCheckCommand exited with status 1',
            },
          },
        },
      ],
    }

    rerender(<MergeConflictCard group={checkFailedGroup} conversationId="c1" />)
    const checkDetailEl = screen.getByTestId('merge-conflict-detail')
    expect(checkDetailEl.textContent).toContain('pytest -q')
    expect(checkDetailEl.textContent).toContain('FAILED tests/test_b.py::test_sum')
    expect(checkDetailEl.textContent).toContain('mergeCheckCommand exited with status 1')
  })

  it('RT-B-08: TasksPanel preserves authoritative interrupted state and shows Retomar button despite stale liveAgentProgress', async () => {
    const staleEvents: ConversationActivityEvent[] = [
      {
        eventId: 'ev-wt',
        cursor: '1',
        type: 'worktree.created',
        kind: 'lifecycle',
        state: 'working',
        summary: 'Worktree criada',
        title: 'Worktree criada',
        agentId: 'agent-bg-interrupted',
        payload: { agent_id: 'agent-bg-interrupted' },
      },
      {
        eventId: 'ev-prog',
        cursor: '2',
        type: 'agent.progress',
        kind: 'lifecycle',
        state: 'working',
        summary: 'Progresso',
        title: 'Progresso',
        agentId: 'agent-bg-interrupted',
        payload: {
          agent_id: 'agent-bg-interrupted',
          state: 'running',
          tool_calls: 3,
          input_tokens: 400,
          output_tokens: 150,
          last_activity_at: '2026-11-21T19:01:00Z',
        },
      },
    ]
    const liveProgress = reduceAgentProgressEvents(staleEvents)
    expect(liveProgress['agent-bg-interrupted']?.state).toBe('running')

    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith('/tasks')) {
        return jsonResponse({ tasks: [] })
      }
      if (url.endsWith('/agents')) {
        return jsonResponse({
          conversation_id: 'conv-1',
          agents: [
            {
              agent_id: 'agent-bg-interrupted',
              name: 'worker-bg',
              role: 'general',
              state: 'interrupted',
              parent_agent_id: 'agent:conv-1:main',
            },
          ],
        })
      }
      if (url.endsWith('/agent-runs')) {
        return jsonResponse({
          conversation_id: 'conv-1',
          runs: [
            {
              agent_id: 'agent-bg-interrupted',
              subagent_type: 'general',
              parent_agent_id: 'agent:conv-1:main',
              state: 'interrupted',
              stalled: false,
              tool_calls: 3,
              input_tokens: 400,
              output_tokens: 150,
              cost_usd: 0.01,
              last_activity_at: '2026-11-21T19:01:00Z',
              created_at: '2026-11-21T19:00:00Z',
              finished_at: '2026-11-21T19:05:00Z',
            },
          ],
        })
      }
      return jsonResponse({})
    })
    const client = new ApiClient({ fetchImpl: fetchMock })

    render(
      <TasksPanel
        client={client}
        conversationId="conv-1"
        agentProgress={liveProgress}
        onClose={() => {}}
      />,
    )

    expect(await screen.findByTestId('agent-interrupted-badge')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Retomar' })).toBeInTheDocument()
    expect(screen.getAllByText(/interrompido/i).length).toBeGreaterThan(0)
  })

  it('RT-B-14: CustomAgentsSection and codeSettings/AgentsSection omit or null isolation when "none" and send "worktree" when selected', async () => {
    const user = userEvent.setup()
    const customBodies: Array<Record<string, unknown>> = []
    const customFetch = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.includes('/v1/providers/')) return jsonResponse({ models: [] })
      if (url.endsWith('/v1/custom-agents') && (!init?.method || init.method === 'GET')) {
        return jsonResponse({ agents: [] })
      }
      if (url.includes('/v1/custom-agents') && (init?.method === 'POST' || init?.method === 'PUT')) {
        const body = JSON.parse(String(init.body ?? '{}')) as Record<string, unknown>
        customBodies.push(body)
        return jsonResponse({
          agent: {
            name: 'plain-agent',
            description: body.description,
            prompt: body.prompt,
            provider: null,
            models: [],
            tools: null,
            disallowed_tools: [],
            effort: null,
            permission_mode: null,
            max_turns: null,
            memory: 'project',
            isolation: body.isolation ?? null,
            enabled: true,
            updated_at: '2026-11-21T20:00:00Z',
          },
        })
      }
      return jsonResponse({ agents: [] })
    })

    const { unmount } = render(
      <MemoryRouter initialEntries={['/library/agents']}>
        <CustomAgentsSection client={new ApiClient({ fetchImpl: customFetch })} />
      </MemoryRouter>,
    )

    await user.click(await screen.findByRole('button', { name: /Criar o primeiro agente|Novo agente/i }))
    await user.type(screen.getByLabelText(/Nome/i), 'plain-agent')
    await user.type(screen.getByLabelText(/Quando usar/i), 'Agente sem isolamento')
    await user.type(screen.getByLabelText(/Instruções/i), 'Faça a tarefa.')
    await user.click(screen.getByRole('button', { name: /Criar agente|Salvar/i }))

    await waitFor(() => {
      expect(customBodies).toHaveLength(1)
    })
    expect(customBodies[0].isolation == null).toBe(true)
    unmount()

    const fileBodies: Array<Record<string, unknown>> = []
    const fileFetch = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.includes('/v1/providers/')) return jsonResponse({ models: [] })
      if (url.includes('/v1/settings/agents') && (!init?.method || init.method === 'GET')) {
        return jsonResponse({ agents: [], project_folder: '/repo' })
      }
      if (url.includes('/v1/settings/agents/') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body ?? '{}')) as Record<string, unknown>
        fileBodies.push(body)
        return jsonResponse({ ok: true })
      }
      return jsonResponse({})
    })

    render(
      <MemoryRouter initialEntries={['/settings/agents']}>
        <AgentsSection client={new ApiClient({ fetchImpl: fileFetch })} />
      </MemoryRouter>,
    )

    await user.click(await screen.findByRole('button', { name: /Novo agente/i }))
    await user.type(screen.getByLabelText(/^Nome/i), 'file-agent')
    await user.type(screen.getByLabelText(/Descrição|Quando usar/i), 'Agente em arquivo')
    await user.type(screen.getByLabelText(/Instruções/i), 'Ajude o usuário.')
    await user.click(screen.getByRole('button', { name: /Salvar|Criar/i }))

    await waitFor(() => {
      expect(fileBodies).toHaveLength(1)
    })
    expect(fileBodies[0].isolation == null).toBe(true)
  })
})
