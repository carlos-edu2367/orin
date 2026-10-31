import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../api/client'
import { OrinActivityCenter } from '../OrinActivityCenter'

function LocationDisplay() {
  const location = useLocation()
  return <output data-testid="current-path">{location.pathname}</output>
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function makeConversationSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    conversation_id: 'conv-1',
    title: 'Sessão de Engenharia',
    state: 'completed',
    mode: 'code',
    permission_mode: 'ask_permissions',
    provider: 'openrouter',
    model_id: 'anthropic/claude-sonnet-4',
    messages: [
      {
        message_id: 'msg-1',
        role: 'user',
        content: 'Clone o repositório e rode os testes',
        status: 'completed',
        retryable: false,
      },
      {
        message_id: 'msg-2',
        role: 'assistant',
        content: 'Tarefa finalizada com sucesso.',
        status: 'completed',
        retryable: false,
      },
    ],
    turns: [
      {
        turn_id: 'turn-1',
        state: 'completed',
        created_at: '2026-10-31T10:00:00Z',
        started_at: '2026-10-31T10:00:01Z',
        finished_at: '2026-10-31T10:01:00Z',
        scheduled_by_schedule_id: null,
        code_mode: 'code',
      },
    ],
    activities: [],
    activity_cursor: 'a.1',
    workspace: {
      kind: 'local',
      path: 'C:/Projetos/orin',
      folder_name: 'orin',
      scope: 'chat',
      project_name: null,
    },
    ...overrides,
  }
}

describe('OrinActivityCenter (Caso 4)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('does NOT render green status dot when sessions have no pending approvals and no unread updates (seeds initial completed chats)', async () => {
    const user = userEvent.setup()
    const client = new ApiClient({
      maxAttempts: 1,
      fetchImpl: vi.fn<typeof fetch>().mockImplementation(async (input) => {
        const url = typeof input === 'string' ? input : String(input)
        if (url.endsWith('/v1/conversations')) {
          return jsonResponse({
            items: [
              {
                conversation_id: 'conv-hist-1',
                title: 'Conversa Histórica Concluída',
                state: 'completed',
                updated_at: '2026-10-31T09:00:00Z',
              },
            ],
          })
        }
        if (url.includes('/v1/projects/sidebar')) {
          return jsonResponse({ items: [] })
        }
        return jsonResponse({})
      }),
    })

    render(
      <MemoryRouter>
        <OrinActivityCenter client={client} />
      </MemoryRouter>,
    )

    const trigger = screen.getByTestId('orin-activity-center-trigger')
    expect(trigger).toBeInTheDocument()

    const mascot = within(trigger).getByTestId('orin-mascot')
    expect(mascot).toHaveAttribute('data-status-dot', 'none')
    expect(within(trigger).queryByTestId('orin-mascot-status-dot')).not.toBeInTheDocument()
    expect(within(trigger).queryByTestId('orin-activity-center-badge')).not.toBeInTheDocument()

    await user.click(trigger)
    const popover = await screen.findByTestId('orin-activity-center-popover')
    expect(popover).toBeInTheDocument()
    expect(within(popover).getByText(/Nenhuma aprovação pendente ou novidade/i)).toBeInTheDocument()
  })

  it('lights up the green dot when a session has a pending toolPermission and resolves it inline on clicking "Permitir"', async () => {
    const user = userEvent.setup()
    let permissionResolved = false
    const permissionCalls: Array<{ url: string; body: Record<string, unknown> }> = []
    const onApprovalResolved = vi.fn()

    const client = new ApiClient({
      maxAttempts: 1,
      fetchImpl: vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
        const url = typeof input === 'string' ? input : String(input)
        const method = (init?.method ?? 'GET').toUpperCase()

        if (url.endsWith('/v1/conversations') && method === 'GET') {
          return jsonResponse({
            items: [
              {
                conversation_id: 'conv-code-1',
                title: 'Deploy de Produção',
                state: permissionResolved ? 'completed' : 'waiting_user',
                mode: 'code',
                updated_at: '2026-10-31T10:05:00Z',
              },
            ],
          })
        }
        if (url.includes('/v1/projects/sidebar')) {
          return jsonResponse({ items: [] })
        }
        if (url.includes('/v1/conversations/conv-code-1/turns/turn-1/tool-permission') && method === 'POST') {
          const parsedBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
          permissionCalls.push({ url, body: parsedBody })
          permissionResolved = true
          return jsonResponse({
            request_id: 'req-git-1',
            decision: parsedBody.decision,
            allowed: true,
          })
        }
        if (url.endsWith('/v1/conversations/conv-code-1') && method === 'GET') {
          return jsonResponse(
            makeConversationSnapshot({
              conversation_id: 'conv-code-1',
              title: 'Deploy de Produção',
              state: permissionResolved ? 'completed' : 'waiting_user',
              turns: [
                {
                  turn_id: 'turn-1',
                  state: permissionResolved ? 'completed' : 'waiting_user',
                  created_at: '2026-10-31T10:00:00Z',
                  started_at: '2026-10-31T10:00:01Z',
                  finished_at: permissionResolved ? '2026-10-31T10:06:00Z' : null,
                  scheduled_by_schedule_id: null,
                  code_mode: 'code',
                },
              ],
              activities: [
                {
                  event_id: 'evt-perm-1',
                  event_type: 'code_mode.decision_required',
                  turn_id: 'turn-1',
                  agent_id: 'agent:main',
                  summary: 'Permissão necessária para executar git clone',
                  cursor: 'a.1',
                  payload: {
                    stage: 'waiting_decision',
                    state: 'waiting_user',
                    tool_name: 'run_command',
                    permission_request: {
                      request_id: 'req-git-1',
                      status: permissionResolved ? 'resolved' : 'pending',
                      tool_name: 'run_command',
                      command: 'git clone https://github.com/org/orin.git',
                      suggested_rule: 'run_command(git clone:*)',
                      reason: 'Clonar o repositório principal',
                      turn_id: 'turn-1',
                    },
                  },
                },
              ],
            }),
          )
        }
        return jsonResponse({})
      }),
    })

    const updatedEvents: Event[] = []
    const onUpdated = (e: Event) => updatedEvents.push(e)
    window.addEventListener('orin:conversations-updated', onUpdated)

    try {
      render(
        <MemoryRouter>
          <OrinActivityCenter
            client={client}
            activeConversationId="conv-code-1"
            onApprovalResolved={onApprovalResolved}
          />
        </MemoryRouter>,
      )

      const statusDot = await screen.findByTestId('orin-mascot-status-dot')
      expect(statusDot).toBeInTheDocument()
      expect(statusDot).toHaveClass('orin-mascot__status-dot--online')

      const trigger = screen.getByTestId('orin-activity-center-trigger')
      await user.click(trigger)

      const popover = await screen.findByTestId('orin-activity-center-popover')
      expect(within(popover).getByText('Deploy de Produção')).toBeInTheDocument()
      expect(within(popover).getByText(/git clone https:\/\/github\.com\/org\/orin\.git/i)).toBeInTheDocument()
      expect(within(popover).getByText(/Clonar o repositório principal/i)).toBeInTheDocument()

      const allowOnceBtn = within(popover).getByTestId('activity-center-allow-once-req-git-1')
      const allowAlwaysBtn = within(popover).getByTestId('activity-center-allow-always-req-git-1')
      const denyBtn = within(popover).getByTestId('activity-center-deny-req-git-1')
      expect(allowOnceBtn).toHaveTextContent('Permitir')
      expect(allowAlwaysBtn).toHaveTextContent('Sempre permitir')
      expect(denyBtn).toHaveTextContent('Negar')

      await user.click(allowOnceBtn)

      await waitFor(() => {
        expect(permissionCalls).toHaveLength(1)
      })
      expect(permissionCalls[0]?.body).toEqual({
        request_id: 'req-git-1',
        decision: 'allow_once',
      })
      expect(updatedEvents.length).toBeGreaterThanOrEqual(1)
      expect(onApprovalResolved).toHaveBeenCalledTimes(1)

      await waitFor(() => {
        expect(screen.queryByTestId('activity-center-allow-once-req-git-1')).not.toBeInTheDocument()
        expect(screen.queryByTestId('orin-mascot-status-dot')).not.toBeInTheDocument()
      })
    } finally {
      window.removeEventListener('orin:conversations-updated', onUpdated)
    }
  })

  it('ignores toolPermission events already resolved by a subsequent code_mode.permission_resolved event', async () => {
    const client = new ApiClient({
      maxAttempts: 1,
      fetchImpl: vi.fn<typeof fetch>().mockImplementation(async (input) => {
        const url = typeof input === 'string' ? input : String(input)
        if (url.endsWith('/v1/conversations')) {
          return jsonResponse({
            items: [
              {
                conversation_id: 'conv-resolved',
                title: 'Sessão em Execução',
                state: 'running',
                mode: 'code',
              },
            ],
          })
        }
        if (url.includes('/v1/projects/sidebar')) {
          return jsonResponse({ items: [] })
        }
        if (url.endsWith('/v1/conversations/conv-resolved')) {
          return jsonResponse(
            makeConversationSnapshot({
              conversation_id: 'conv-resolved',
              title: 'Sessão em Execução',
              state: 'running',
              turns: [
                {
                  turn_id: 'turn-1',
                  state: 'running',
                  created_at: '2026-10-31T10:00:00Z',
                  started_at: '2026-10-31T10:00:01Z',
                  finished_at: null,
                  scheduled_by_schedule_id: null,
                  code_mode: 'code',
                },
              ],
              activities: [
                {
                  event_id: 'evt-perm-old',
                  event_type: 'code_mode.decision_required',
                  turn_id: 'turn-1',
                  agent_id: 'agent:main',
                  summary: 'Permissão solicitada',
                  cursor: 'a.1',
                  payload: {
                    tool_name: 'run_command',
                    permission_request: {
                      request_id: 'req-already-done',
                      status: 'pending',
                      tool_name: 'run_command',
                      command: 'npm run build',
                      turn_id: 'turn-1',
                    },
                  },
                },
                {
                  event_id: 'evt-perm-resolved',
                  event_type: 'code_mode.permission_resolved',
                  turn_id: 'turn-1',
                  agent_id: 'agent:main',
                  summary: 'Permissão resolvida',
                  cursor: 'a.2',
                  payload: {
                    request_id: 'req-already-done',
                    decision: 'allow_once',
                  },
                },
              ],
            }),
          )
        }
        return jsonResponse({})
      }),
    })

    render(
      <MemoryRouter>
        <OrinActivityCenter client={client} activeConversationId="conv-resolved" />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('orin-activity-center-trigger')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('orin-mascot-status-dot')).not.toBeInTheDocument()
  })

  it('supports inline approvals for planApproval, workspaceTrust, pluginApproval, and mcpApproval without secrets', async () => {
    const user = userEvent.setup()
    const apiHits: string[] = []

    const client = new ApiClient({
      maxAttempts: 1,
      fetchImpl: vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
        const url = typeof input === 'string' ? input : String(input)
        const method = (init?.method ?? 'GET').toUpperCase()
        apiHits.push(`${method} ${url}`)

        if (url.endsWith('/v1/conversations') && method === 'GET') {
          return jsonResponse({
            items: [
              { conversation_id: 'conv-plan', title: 'Arquitetura V2', state: 'waiting_user', mode: 'code' },
              { conversation_id: 'conv-trust', title: 'Repo Local', state: 'waiting_user', mode: 'code' },
              { conversation_id: 'conv-plugin', title: 'Instalar Plugin', state: 'waiting_user', mode: 'chat' },
              { conversation_id: 'conv-mcp', title: 'Conectar MCP', state: 'waiting_user', mode: 'chat' },
            ],
          })
        }
        if (url.includes('/v1/projects/sidebar')) {
          return jsonResponse({ items: [] })
        }
        if (url.endsWith('/v1/conversations/conv-plan') && method === 'GET') {
          return jsonResponse(
            makeConversationSnapshot({
              conversation_id: 'conv-plan',
              title: 'Arquitetura V2',
              state: 'waiting_user',
              turns: [{ turn_id: 't-plan', state: 'waiting_user', created_at: '2026-10-31T10:00:00Z', started_at: null, finished_at: null, scheduled_by_schedule_id: null, code_mode: 'code' }],
              activities: [
                {
                  event_id: 'evt-plan',
                  event_type: 'code_mode.plan_ready',
                  turn_id: 't-plan',
                  agent_id: 'agent:main',
                  summary: 'Plano pronto para aprovação',
                  cursor: 'a.1',
                  payload: {
                    plan_approval: {
                      plan_path: '.orin/plans/v2.md',
                      plan_content: '# Plano V2',
                    },
                  },
                },
              ],
            }),
          )
        }
        if (url.endsWith('/v1/conversations/conv-trust') && method === 'GET') {
          return jsonResponse(
            makeConversationSnapshot({
              conversation_id: 'conv-trust',
              title: 'Repo Local',
              state: 'waiting_user',
              turns: [{ turn_id: 't-trust', state: 'waiting_user', created_at: '2026-10-31T10:00:00Z', started_at: null, finished_at: null, scheduled_by_schedule_id: null, code_mode: 'code' }],
              activities: [
                {
                  event_id: 'evt-trust',
                  event_type: 'workspace.trust_required',
                  turn_id: 't-trust',
                  agent_id: 'agent:main',
                  summary: 'Confiança necessária na pasta C:/repo',
                  cursor: 'a.1',
                  payload: {
                    workspace_trust: {
                      path: 'C:/repo',
                      items: ['.orin/settings.json'],
                    },
                  },
                },
              ],
            }),
          )
        }
        if (url.endsWith('/v1/conversations/conv-plugin') && method === 'GET') {
          return jsonResponse(
            makeConversationSnapshot({
              conversation_id: 'conv-plugin',
              title: 'Instalar Plugin',
              state: 'waiting_user',
              turns: [{ turn_id: 't-plugin', state: 'waiting_user', created_at: '2026-10-31T10:00:00Z', started_at: null, finished_at: null, scheduled_by_schedule_id: null, code_mode: null }],
              activities: [
                {
                  event_id: 'evt-plugin',
                  event_type: 'turn.waiting_user',
                  turn_id: 't-plugin',
                  agent_id: 'agent:main',
                  summary: 'Aprovar plugin Superpowers',
                  cursor: 'a.1',
                  payload: {
                    plugin_approval: true,
                    plugin: {
                      plugin_id: 'plug-superpowers',
                      version: '1.0.0',
                      display_name: 'Superpowers',
                      description: 'Workflow engineering',
                      author: 'Orin',
                      warnings: [],
                      skills: [],
                      mcp_servers: [],
                      agents: [],
                      commands: [],
                      hooks: [],
                      contribution_count: 1,
                    },
                  },
                },
              ],
            }),
          )
        }
        if (url.endsWith('/v1/conversations/conv-mcp') && method === 'GET') {
          return jsonResponse(
            makeConversationSnapshot({
              conversation_id: 'conv-mcp',
              title: 'Conectar MCP',
              state: 'waiting_user',
              turns: [{ turn_id: 't-mcp', state: 'waiting_user', created_at: '2026-10-31T10:00:00Z', started_at: null, finished_at: null, scheduled_by_schedule_id: null, code_mode: null }],
              activities: [
                {
                  event_id: 'evt-mcp',
                  event_type: 'turn.waiting_user',
                  turn_id: 't-mcp',
                  agent_id: 'agent:main',
                  summary: 'Conectar servidor Playwright',
                  cursor: 'a.1',
                  payload: {
                    mcp_approval: true,
                    server: {
                      server_id: 'srv-playwright',
                      display_name: 'Playwright MCP',
                      transport: 'stdio',
                      secret_names: [],
                      catalog_id: null,
                    },
                  },
                },
              ],
            }),
          )
        }
        if (url.includes('/permission-mode') && method === 'PATCH') {
          return jsonResponse({ conversation_id: 'conv-plan', mode: 'code', permission_mode: 'accept_edits' })
        }
        if (url.includes('/messages') && method === 'POST') {
          return jsonResponse(
            {
              conversation_id: 'conv-plan',
              title: 'Arquitetura V2',
              turn_id: 't-next',
              message_id: 'm-next',
              state: 'running',
              mode: 'code',
              permission_mode: 'accept_edits',
            },
            201,
          )
        }
        if (url.includes('/workspace-trust') && method === 'POST') {
          return jsonResponse({ trusted: true, path: 'C:/repo' })
        }
        if (url.includes('/v1/plugins/plug-superpowers/approve') && method === 'POST') {
          return jsonResponse({
            plugin_id: 'plug-superpowers',
            version: '1.0.0',
            display_name: 'Superpowers',
            description: 'Workflow engineering',
            author: 'Orin',
            homepage: null,
            state: 'active',
            warnings: [],
            contribution_count: 1,
            contributions: [],
          })
        }
        if (url.includes('/v1/mcp/servers/srv-playwright/approve') && method === 'POST') {
          return jsonResponse({
            server_id: 'srv-playwright',
            slug: 'playwright',
            display_name: 'Playwright MCP',
            transport: 'stdio',
            command: 'npx',
            args: [],
            url: null,
            secret_names: [],
            catalog_id: null,
            state: 'active',
            state_reason: '',
            protocol_version: '1.0',
            tool_count: 2,
            auth_kind: 'none',
            tools: [],
          })
        }
        return jsonResponse({})
      }),
    })

    render(
      <MemoryRouter>
        <OrinActivityCenter client={client} />
      </MemoryRouter>,
    )

    expect(await screen.findByTestId('orin-mascot-status-dot')).toBeInTheDocument()
    await user.click(screen.getByTestId('orin-activity-center-trigger'))

    const popover = await screen.findByTestId('orin-activity-center-popover')

    const approvePlanBtn = within(popover).getByRole('button', { name: /Aprovar plano/i })
    const trustBtn = within(popover).getByRole('button', { name: /Confiar na pasta/i })
    const approvePluginBtn = within(popover).getByRole('button', { name: /Aprovar plugin/i })
    const approveMcpBtn = within(popover).getByRole('button', { name: /Aprovar MCP/i })

    await user.click(approvePlanBtn)
    await user.click(trustBtn)
    await user.click(approvePluginBtn)
    await user.click(approveMcpBtn)

    await waitFor(() => {
      expect(apiHits.some((h) => h.includes('POST') && h.includes('/workspace-trust'))).toBe(true)
      expect(apiHits.some((h) => h.includes('POST') && h.includes('/v1/plugins/plug-superpowers/approve'))).toBe(true)
      expect(apiHits.some((h) => h.includes('POST') && h.includes('/v1/mcp/servers/srv-playwright/approve'))).toBe(true)
    })
  })

  it('tracks unread background session updates ("Novidades"), navigates on click, and clears when clicking "Marcar tudo como lido"', async () => {
    const user = userEvent.setup()
    let bgState = 'running'
    let bgUpdatedAt = '2026-10-31T10:00:00Z'

    const client = new ApiClient({
      maxAttempts: 1,
      fetchImpl: vi.fn<typeof fetch>().mockImplementation(async (input) => {
        const url = typeof input === 'string' ? input : String(input)
        if (url.endsWith('/v1/conversations')) {
          return jsonResponse({
            items: [
              {
                conversation_id: 'conv-active',
                title: 'Conversa Atual',
                state: 'completed',
                updated_at: '2026-10-31T10:00:00Z',
              },
              {
                conversation_id: 'conv-bg-1',
                title: 'Análise em Segundo Plano',
                state: bgState,
                updated_at: bgUpdatedAt,
              },
            ],
          })
        }
        if (url.includes('/v1/projects/sidebar')) {
          return jsonResponse({ items: [] })
        }
        if (url.endsWith('/v1/conversations/conv-bg-1')) {
          return jsonResponse(
            makeConversationSnapshot({
              conversation_id: 'conv-bg-1',
              title: 'Análise em Segundo Plano',
              state: bgState,
              turns: [
                {
                  turn_id: 'turn-bg',
                  state: bgState,
                  created_at: '2026-10-31T10:00:00Z',
                  started_at: '2026-10-31T10:00:01Z',
                  finished_at: bgState === 'completed' ? '2026-10-31T10:02:00Z' : null,
                  scheduled_by_schedule_id: null,
                  code_mode: null,
                },
              ],
              activities: [],
            }),
          )
        }
        return jsonResponse({})
      }),
    })

    render(
      <MemoryRouter initialEntries={['/chats/conv-active']}>
        <Routes>
          <Route
            path="/chats/:conversationId"
            element={<OrinActivityCenter client={client} activeConversationId="conv-active" />}
          />
        </Routes>
        <LocationDisplay />
      </MemoryRouter>,
    )

    // Initially conv-bg-1 is running without pending approvals -> no green dot
    await waitFor(() => {
      expect(screen.getByTestId('orin-activity-center-trigger')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('orin-mascot-status-dot')).not.toBeInTheDocument()

    // Background session transitions to completed
    bgState = 'completed'
    bgUpdatedAt = '2026-10-31T10:02:00Z'
    act(() => {
      window.dispatchEvent(new Event('orin:conversations-updated'))
    })

    // Green dot now lights up for unread background completion
    expect(await screen.findByTestId('orin-mascot-status-dot')).toBeInTheDocument()

    await user.click(screen.getByTestId('orin-activity-center-trigger'))
    const popover = await screen.findByTestId('orin-activity-center-popover')
    expect(within(popover).getByText('Análise em Segundo Plano')).toBeInTheDocument()

    const markAllBtn = within(popover).getByRole('button', { name: /Marcar tudo como lido/i })
    await user.click(markAllBtn)

    await waitFor(() => {
      expect(screen.queryByTestId('orin-mascot-status-dot')).not.toBeInTheDocument()
    })
    expect(window.localStorage.getItem('orin.activityCenter.lastSeen')).toContain('conv-bg-1')
  })

  it('handles adversarial edge cases: corrupted localStorage, double-click idempotency, Escape and outside click', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem('orin.activityCenter.lastSeen', '{not-valid-json')

    let resolvePost!: () => void
    let postCount = 0

    const client = new ApiClient({
      maxAttempts: 1,
      fetchImpl: vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
        const url = typeof input === 'string' ? input : String(input)
        const method = (init?.method ?? 'GET').toUpperCase()
        if (url.endsWith('/v1/conversations')) {
          return jsonResponse({
            items: [{ conversation_id: 'conv-adv', title: 'Teste Adversarial', state: 'waiting_user', mode: 'code' }],
          })
        }
        if (url.includes('/v1/projects/sidebar')) {
          return jsonResponse({ items: [] })
        }
        if (url.endsWith('/v1/conversations/conv-adv')) {
          return jsonResponse(
            makeConversationSnapshot({
              conversation_id: 'conv-adv',
              title: 'Teste Adversarial',
              state: 'waiting_user',
              turns: [{ turn_id: 't-adv', state: 'waiting_user', created_at: '2026-10-31T10:00:00Z', started_at: null, finished_at: null, scheduled_by_schedule_id: null, code_mode: 'code' }],
              activities: [
                {
                  event_id: 'evt-adv',
                  event_type: 'code_mode.decision_required',
                  turn_id: 't-adv',
                  agent_id: 'agent:main',
                  summary: 'Permissão pendente',
                  cursor: 'a.1',
                  payload: {
                    tool_name: 'run_command',
                    permission_request: {
                      request_id: 'req-adv-1',
                      status: 'pending',
                      tool_name: 'run_command',
                      command: 'echo ok',
                      turn_id: 't-adv',
                    },
                  },
                },
              ],
            }),
          )
        }
        if (url.includes('/tool-permission') && method === 'POST') {
          postCount += 1
          await new Promise<void>((r) => {
            resolvePost = r
          })
          return jsonResponse({ request_id: 'req-adv-1', decision: 'allow_once', allowed: true })
        }
        return jsonResponse({})
      }),
    })

    render(
      <MemoryRouter>
        <OrinActivityCenter client={client} />
      </MemoryRouter>,
    )

    expect(await screen.findByTestId('orin-mascot-status-dot')).toBeInTheDocument()
    const trigger = screen.getByTestId('orin-activity-center-trigger')
    await user.click(trigger)
    expect(screen.getByTestId('orin-activity-center-popover')).toBeInTheDocument()

    // Escape closes popover
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('orin-activity-center-popover')).not.toBeInTheDocument()

    // Reopen and outside pointerDown closes popover
    await user.click(trigger)
    expect(screen.getByTestId('orin-activity-center-popover')).toBeInTheDocument()
    fireEvent.pointerDown(document.body)
    expect(screen.queryByTestId('orin-activity-center-popover')).not.toBeInTheDocument()

    // Reopen and rapid double-click on Permitir only sends 1 POST while in-flight
    await user.click(trigger)
    const allowBtn = screen.getByTestId('activity-center-allow-once-req-adv-1')
    fireEvent.click(allowBtn)
    fireEvent.click(allowBtn)
    expect(postCount).toBe(1)

    await act(async () => {
      resolvePost()
    })
  })

  it('surfaces role="alert" error when inline approval fails with 500, navigates on unread update click, and uses zero hardcoded dark classes', async () => {
    const user = userEvent.setup()
    let bgState = 'running'

    const client = new ApiClient({
      maxAttempts: 1,
      fetchImpl: vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
        const url = typeof input === 'string' ? input : String(input)
        const method = (init?.method ?? 'GET').toUpperCase()
        if (url.endsWith('/v1/conversations')) {
          return jsonResponse({
            items: [
              { conversation_id: 'conv-err', title: 'Sessão com Falha API', state: 'waiting_user', mode: 'code' },
              { conversation_id: 'conv-bg-nav', title: 'Relatório Pronto', state: bgState, updated_at: '2026-10-31T10:10:00Z' },
            ],
          })
        }
        if (url.includes('/v1/projects/sidebar')) {
          return jsonResponse({ items: [] })
        }
        if (url.endsWith('/v1/conversations/conv-err')) {
          return jsonResponse(
            makeConversationSnapshot({
              conversation_id: 'conv-err',
              title: 'Sessão com Falha API',
              state: 'waiting_user',
              turns: [{ turn_id: 't-err', state: 'waiting_user', created_at: '2026-10-31T10:00:00Z', started_at: null, finished_at: null, scheduled_by_schedule_id: null, code_mode: 'code' }],
              activities: [
                {
                  event_id: 'evt-err',
                  event_type: 'code_mode.decision_required',
                  turn_id: 't-err',
                  agent_id: 'agent:main',
                  summary: 'Permissão com erro de rede',
                  cursor: 'a.1',
                  payload: {
                    tool_name: 'run_command',
                    permission_request: {
                      request_id: 'req-err-500',
                      status: 'pending',
                      tool_name: 'run_command',
                      command: 'rm -rf dist',
                      turn_id: 't-err',
                    },
                  },
                },
              ],
            }),
          )
        }
        if (url.endsWith('/v1/conversations/conv-bg-nav')) {
          return jsonResponse(
            makeConversationSnapshot({
              conversation_id: 'conv-bg-nav',
              title: 'Relatório Pronto',
              state: bgState,
            }),
          )
        }
        if (url.includes('/tool-permission') && method === 'POST') {
          return jsonResponse({ error: { code: 'internal_error', message: 'Falha ao gravar decisão' } }, 500)
        }
        return jsonResponse({})
      }),
    })

    const { container } = render(
      <MemoryRouter initialEntries={['/']}>
        <OrinActivityCenter client={client} />
        <LocationDisplay />
      </MemoryRouter>,
    )

    expect(await screen.findByTestId('orin-mascot-status-dot')).toBeInTheDocument()

    // Complete background session and refresh
    bgState = 'completed'
    act(() => {
      window.dispatchEvent(new Event('orin:conversations-updated'))
    })

    await user.click(screen.getByTestId('orin-activity-center-trigger'))
    const popover = await screen.findByTestId('orin-activity-center-popover')

    // Zero hardcoded dark utility classes check
    const allElements = Array.from(container.querySelectorAll('*'))
    for (const el of allElements) {
      const cls = el.getAttribute('class') ?? ''
      expect(cls).not.toMatch(/\b(text-white|bg-black|border-white)\b/)
    }

    // Trigger 500 error on inline approval -> shows alert and unlocks button
    const denyBtn = within(popover).getByTestId('activity-center-deny-req-err-500')
    await user.click(denyBtn)
    expect(await within(popover).findByRole('alert')).toBeInTheDocument()
    expect(denyBtn).not.toBeDisabled()

    // Click unread background update -> navigates to /chats/conv-bg-nav and closes popover
    const updateRow = within(popover).getByTestId('activity-center-update-conv-bg-nav')
    await user.click(updateRow)
    expect(screen.getByTestId('current-path')).toHaveTextContent('/chats/conv-bg-nav')
    expect(screen.queryByTestId('orin-activity-center-popover')).not.toBeInTheDocument()
  })
})
