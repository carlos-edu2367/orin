import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Route } from '@playwright/test'

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installCsrfMeta(page: Page): Promise<void> {
  await page.route('**/**', async (route) => {
    if (route.request().resourceType() !== 'document') {
      await route.fallback()
      return
    }
    const response = await route.fetch()
    const body = (await response.text()).replace(
      '<head>',
      '<head><meta name="csrf-token" content="csrf-e2e-extensions-handoff-projects">',
    )
    await route.fulfill({ response, body })
  })
}

async function assertNoCriticalOrSeriousViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze()
  const blocking = results.violations.filter(
    (violation) => violation.impact === 'critical' || violation.impact === 'serious',
  )
  expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([])
}

test('E2E: Orin Chat CodeSessionProposalCard -> Orin Code McpApprovalCard & PluginApprovalCard -> Create Project from Session & ProjectPage', async ({
  page,
}) => {
  await installCsrfMeta(page)

  const createdConversations: Array<Record<string, unknown>> = []
  const createdProjects: Array<Record<string, unknown>> = []
  let codeConvProjectId: string | null = null
  let codeConvProjectName: string | null = null

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const { pathname } = url

    // 1. Chat conversation with `propose_code_session` card waiting for user
    if (request.method() === 'GET' && pathname === '/v1/conversations/conv-chat-1') {
      await fulfillJson(route, {
        conversation_id: 'conv-chat-1',
        title: 'Arquitetura Auth JWT',
        state: 'waiting_user',
        provider: 'anthropic',
        model_id: 'claude-sonnet-4-5',
        mode: 'chat',
        permission_mode: 'ask_permissions',
        project_id: null,
        project_name: null,
        workspace: {
          kind: 'local',
          path: 'C:/Repos/orin-auth',
          folder_name: 'orin-auth',
          scope: 'chat',
          project_name: null,
        },
        messages: [
          {
            message_id: 'msg-u-1',
            turn_id: 'turn-chat-1',
            role: 'user',
            content: 'Vamos implementar o módulo de refresh token e rodar a suíte de testes.',
            status: 'completed',
            retryable: false,
            attachments: [],
            command: null,
          },
          {
            message_id: 'msg-a-1',
            turn_id: 'turn-chat-1',
            role: 'assistant',
            content: 'Preparei uma sugestão para abrir essa implementação diretamente no Orin Code.',
            status: 'streaming',
            retryable: false,
            attachments: [],
            command: null,
          },
        ],
        turns: [
          {
            turn_id: 'turn-chat-1',
            state: 'waiting_user',
            created_at: '2026-10-16T14:00:00.000Z',
            started_at: '2026-10-16T14:00:01.000Z',
            finished_at: null,
            scheduled_by_schedule_id: null,
            code_mode: null,
          },
        ],
        activities: [
          {
            event_id: 'ev-prop-1',
            cursor: '1',
            turn_id: 'turn-chat-1',
            agent_id: 'agent:main',
            event_type: 'tool.finished',
            summary: 'Sugestão para iniciar no Orin Code: Implementar Refresh Token JWT',
            occurred_at: '2026-10-16T14:00:02.000Z',
            payload: {
              tool_name: 'propose_code_session',
              tool_kind: 'code_handoff',
              status: 'succeeded',
              wait_for_user: true,
              code_session_proposal: {
                proposal_id: 'codeprop_e2e_01',
                title: 'Implementar Refresh Token JWT',
                prompt: 'Implemente rotação de refresh token em src/auth/jwt.py com testes.',
                workspace_path: 'C:/Repos/orin-auth',
                provider: 'anthropic',
                model_id: 'claude-sonnet-4-5',
                permission_mode: 'accept_edits',
                project_id: null,
              },
            },
          },
        ],
        activity_cursor: '1',
        context_usage: null,
      })
      return
    }

    // 2. Launching the new Orin Code session from CodeSessionProposalCard
    if (request.method() === 'POST' && pathname === '/v1/conversations') {
      const body = request.postDataJSON() as Record<string, unknown>
      createdConversations.push(body)
      await fulfillJson(
        route,
        {
          conversation_id: 'conv-code-launched',
          title: 'Implementar Refresh Token JWT',
          turn_id: 'turn-code-1',
          message_id: 'msg-code-a1',
          state: 'waiting_user',
          mode: 'code',
          permission_mode: body.permission_mode ?? 'accept_edits',
        },
        201,
      )
      return
    }

    // 3. The launched Orin Code session rendering McpApprovalCard & PluginApprovalCard in Code Mode timeline
    if (request.method() === 'GET' && pathname === '/v1/conversations/conv-code-launched') {
      await fulfillJson(route, {
        conversation_id: 'conv-code-launched',
        title: 'Implementar Refresh Token JWT',
        state: 'waiting_user',
        provider: 'anthropic',
        model_id: 'claude-sonnet-4-5',
        mode: 'code',
        permission_mode: 'accept_edits',
        project_id: codeConvProjectId,
        project_name: codeConvProjectName,
        workspace: {
          kind: 'local',
          path: 'C:/Repos/orin-auth',
          folder_name: 'orin-auth',
          scope: codeConvProjectId ? 'project' : 'chat',
          project_name: codeConvProjectName,
        },
        messages: [
          {
            message_id: 'msg-code-u1',
            turn_id: 'turn-code-1',
            role: 'user',
            content: 'Implemente rotação de refresh token e conecte o MCP do GitHub + plugin security-audit.',
            status: 'completed',
            retryable: false,
            attachments: [],
            command: null,
          },
          {
            message_id: 'msg-code-a1',
            turn_id: 'turn-code-1',
            role: 'assistant',
            content: 'Configurei a proposta do servidor MCP GitHub e do plugin Security Audit para sua aprovação.',
            status: 'streaming',
            retryable: false,
            attachments: [],
            command: null,
          },
        ],
        turns: [
          {
            turn_id: 'turn-code-1',
            state: 'waiting_user',
            created_at: '2026-10-16T14:01:00.000Z',
            started_at: '2026-10-16T14:01:01.000Z',
            finished_at: null,
            scheduled_by_schedule_id: null,
            code_mode: 'code',
          },
        ],
        activities: [
          {
            event_id: 'ev-code-start',
            cursor: '1',
            turn_id: 'turn-code-1',
            agent_id: 'agent:main',
            event_type: 'code_mode.started',
            summary: 'Modo Code iniciado',
            occurred_at: '2026-10-16T14:01:01.000Z',
            payload: { stage: 'implementing' },
          },
          {
            event_id: 'ev-read-1',
            cursor: '1a',
            turn_id: 'turn-code-1',
            agent_id: 'agent:main',
            event_type: 'tool.finished',
            summary: 'Leu README.md',
            occurred_at: '2026-10-16T14:01:01.200Z',
            payload: {
              tool_name: 'read_file',
              tool_kind: 'filesystem',
              status: 'succeeded',
              path: 'README.md',
            },
          },
          {
            event_id: 'ev-read-2',
            cursor: '1b',
            turn_id: 'turn-code-1',
            agent_id: 'agent:main',
            event_type: 'tool.finished',
            summary: 'Leu src/auth/jwt.py',
            occurred_at: '2026-10-16T14:01:01.500Z',
            payload: {
              tool_name: 'read_file',
              tool_kind: 'filesystem',
              status: 'succeeded',
              path: 'src/auth/jwt.py',
            },
          },
          {
            event_id: 'ev-mcp-prop',
            cursor: '2',
            turn_id: 'turn-code-1',
            agent_id: 'agent:main',
            event_type: 'tool.finished',
            summary: 'Aguardando aprovação da conexão GitHub MCP',
            occurred_at: '2026-10-16T14:01:02.000Z',
            payload: {
              tool_name: 'configure_mcp',
              tool_kind: 'mcp',
              status: 'succeeded',
              mcp_approval: true,
              wait_for_user: true,
              server: {
                server_id: 'mcp_github_1',
                display_name: 'GitHub MCP',
                transport: 'stdio',
                secret_names: ['GITHUB_TOKEN'],
                catalog_id: 'github',
              },
            },
          },
          {
            event_id: 'ev-plugin-prop',
            cursor: '3',
            turn_id: 'turn-code-1',
            agent_id: 'agent:main',
            event_type: 'tool.finished',
            summary: 'Aguardando aprovação do plugin Security Audit Pack',
            occurred_at: '2026-10-16T14:01:03.000Z',
            payload: {
              tool_name: 'install_plugin',
              tool_kind: 'plugin',
              status: 'succeeded',
              plugin_approval: true,
              wait_for_user: true,
              plugin: {
                plugin_id: 'plugin_sec_1',
                version: '1.2.0',
                display_name: 'Security Audit Pack',
                description: 'Auditoria de JWT e OAuth2',
                author: 'Orin Security',
                warnings: [],
                skills: [{ skill_id: 'jwt-audit', name: 'JWT Audit' }],
                mcp_servers: [],
                agents: [],
                commands: [],
                hooks: [],
                contribution_count: 1,
              },
            },
          },
        ],
        activity_cursor: '3',
        context_usage: null,
      })
      return
    }

    if (pathname.endsWith('/events')) {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: heartbeat\ndata: {"cursor":"3"}\n\n',
      })
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/projects') {
      await fulfillJson(route, {
        items: createdProjects.length
          ? createdProjects
          : [
              {
                project_id: 'project_existing_1',
                name: 'Plataforma Core',
                description: 'Serviços centrais',
                workspace_id: 'workspace:project_existing_1',
                created_at: '2026-10-16T12:00:00.000Z',
                updated_at: '2026-10-16T12:00:00.000Z',
                archived_at: null,
                workspace: {
                  kind: 'local',
                  path: 'C:/Repos/orin-core',
                  folder_name: 'orin-core',
                  scope: 'project',
                  project_name: 'Plataforma Core',
                },
              },
            ],
      })
      return
    }

    if (request.method() === 'POST' && pathname === '/v1/projects') {
      const body = request.postDataJSON() as Record<string, unknown>
      codeConvProjectId = 'project_created_from_code'
      codeConvProjectName = String(body.name || 'Orin Auth Project')
      const created = {
        project_id: codeConvProjectId,
        name: codeConvProjectName,
        description: (body.description as string) || 'Projeto criado a partir da sessão Code',
        workspace_id: `workspace:${codeConvProjectId}`,
        created_at: '2026-10-16T14:05:00.000Z',
        updated_at: '2026-10-16T14:05:00.000Z',
        archived_at: null,
        workspace: {
          kind: 'local',
          path: (body.workspace_path as string) || 'C:/Repos/orin-auth',
          folder_name: 'orin-auth',
          scope: 'project',
          project_name: codeConvProjectName,
        },
      }
      createdProjects.push(created)
      await fulfillJson(route, created, 201)
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/projects/sidebar') {
      await fulfillJson(route, {
        items: [
          {
            project_id: codeConvProjectId ?? 'project_created_from_code',
            name: codeConvProjectName ?? 'Orin Auth Project',
            description: 'Módulo de autenticação compartilhado',
            workspace: {
              kind: 'local',
              path: 'C:/Repos/orin-auth',
              folder_name: 'orin-auth',
              scope: 'project',
              project_name: codeConvProjectName ?? 'Orin Auth Project',
            },
            chats: [
              {
                conversation_id: 'conv-code-launched',
                title: 'Implementar Refresh Token JWT',
                state: 'waiting_user',
                updated_at: '2026-10-16T14:05:00.000Z',
                mode: 'code',
              },
            ],
          },
        ],
      })
      return
    }

    if (request.method() === 'GET' && pathname.endsWith('/memories')) {
      await fulfillJson(route, {
        items: [{ memory_id: 'mem_1', fact: 'Usar chaves assimétricas Ed25519 para assinar tokens.', tags: ['security'], updated_at: null }],
      })
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/conversations') {
      await fulfillJson(route, { items: [] })
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/skills') {
      await fulfillJson(route, { items: [] })
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/commands') {
      await fulfillJson(route, { items: [] })
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/settings/vision-model') {
      await fulfillJson(route, { provider: null, model_id: null })
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/settings/code-mode') {
      await fulfillJson(route, { default_permission_mode: 'accept_edits' })
      return
    }

    if (request.method() === 'GET' && pathname.startsWith('/v1/providers/')) {
      await fulfillJson(route, { items: [] })
      return
    }

    if (request.method() === 'GET' && pathname.endsWith('/worktree')) {
      await fulfillJson(route, { is_git_repo: true, active: false, worktree: null, worktrees: [] })
      return
    }

    await fulfillJson(route, {})
  })

  // Step 1: Open Chat conversation with CodeSessionProposalCard
  await page.goto('/chats/conv-chat-1')
  const proposalCard = page.getByTestId('code-proposal-card')
  await expect(proposalCard).toBeVisible()
  await expect(proposalCard).toContainText('Implementar Refresh Token JWT')
  await expect(page.getByTestId('code-proposal-workspace-input')).toHaveValue('C:/Repos/orin-auth')

  // Edit the prompt in the card and accept to launch Orin Code
  const promptInput = page.getByTestId('code-proposal-prompt-input')
  await promptInput.fill('Implemente rotação de refresh token com Ed25519 e rode pytest.')
  await assertNoCriticalOrSeriousViolations(page)
  await page.getByTestId('code-proposal-accept-button').click()

  // Step 2: Verify navigation to the new Orin Code session and extension approval cards in Code timeline
  await expect(page).toHaveURL(/\/chats\/conv-code-launched$/)
  expect(createdConversations).toHaveLength(1)
  expect(createdConversations[0].code_mode).toBe('code')
  expect(createdConversations[0].message).toBe('Implemente rotação de refresh token com Ed25519 e rode pytest.')

  await expect(page.getByTestId('orin-code-badge')).toBeVisible()
  // Verify smart collapsible CodeToolBatch renders collapsed by default ("Leu 2 arquivos") and expands on click
  const toolBatch = page.getByTestId('code-tool-batch')
  await expect(toolBatch).toBeVisible()
  const batchToggle = page.getByTestId('code-tool-batch-toggle')
  await expect(batchToggle).toContainText('Leu 2 arquivos')
  await expect(toolBatch).not.toContainText('src/auth/jwt.py')
  await batchToggle.click()
  await expect(toolBatch).toContainText('README.md')
  await expect(toolBatch).toContainText('src/auth/jwt.py')

  // Both McpApprovalCard and PluginApprovalCard must render inside the Code Mode timeline!
  await expect(page.getByRole('region', { name: /Conectar GitHub MCP/i })).toBeVisible()
  await expect(page.getByRole('region', { name: /Instalar Security Audit Pack/i })).toBeVisible()

  // Step 3: Create a Project from the current Orin Code session via Header Project Control
  await page.getByTestId('conversation-project-trigger').click()
  const projectMenu = page.getByTestId('conversation-project-menu')
  await expect(projectMenu).toBeVisible()

  const createForm = page.getByTestId('create-project-from-session-form')
  await createForm.getByLabel('Nome do novo projeto').fill('Orin Auth Project')
  await createForm.getByRole('button', { name: /Criar projeto e vincular/i }).click()

  await expect(page).toHaveURL(/\/projects\/project_created_from_code\/chats\/conv-code-launched$/)
  await expect(page.getByTestId('conversation-project-badge')).toHaveText('Orin Auth Project')

  // Step 4: Navigate to ProjectPage via the header badge and verify workspace & quick actions
  await page.getByTestId('conversation-project-badge').click()
  await expect(page).toHaveURL(/\/projects\/project_created_from_code$/)
  await expect(page.getByTestId('project-new-chat-button')).toBeVisible()
  await expect(page.getByTestId('project-new-code-button')).toBeVisible()
  await expect(page.getByTestId('project-workspace-section')).toContainText('C:/Repos/orin-auth')
  await expect(page.getByTestId('project-chat-code-pill')).toBeVisible()
  await assertNoCriticalOrSeriousViolations(page)
})
