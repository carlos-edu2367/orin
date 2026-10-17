import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Route } from '@playwright/test'
import { env as processEnv } from 'node:process'

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installCsrfMeta(page: Page): Promise<void> {
  await page.route('**/chats/**', async (route) => {
    if (route.request().resourceType() !== 'document') {
      await route.fallback()
      return
    }
    const response = await route.fetch()
    const body = (await response.text()).replace(
      '<head>',
      '<head><meta name="csrf-token" content="csrf-multi-session-e2e">',
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

test('Multi-session concurrency & parallel subagents tree render distinct branches, active sidebar badge, clean session switching, and zero a11y violations', async ({
  page,
}) => {
  await installCsrfMeta(page)

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const { pathname } = url

    if (request.method() === 'GET' && pathname === '/v1/conversations') {
      await fulfillJson(route, {
        items: [
          {
            conversation_id: 'conv-parallel-1',
            title: 'Auditoria Paralela de Segurança',
            state: 'running',
            mode: 'code',
          },
          {
            conversation_id: 'conv-parallel-2',
            title: 'Migração de Banco em Paralelo',
            state: 'streaming',
            mode: 'code',
          },
          {
            conversation_id: 'conv-idle-3',
            title: 'Resumo de Arquitetura',
            state: 'completed',
            mode: 'chat',
          },
        ],
      })
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/projects/sidebar') {
      await fulfillJson(route, { items: [] })
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/conversations/conv-parallel-1') {
      await fulfillJson(route, {
        conversation_id: 'conv-parallel-1',
        title: 'Auditoria Paralela de Segurança',
        state: 'running',
        provider: 'openrouter',
        model_id: 'qwen/qwen3.8-max-0902',
        mode: 'code',
        permission_mode: 'accept_edits',
        project_id: null,
        workspace: {
          mode: 'local_folder',
          path: 'C:/Projetos/orin',
          folder_name: 'orin',
          status: 'available',
        },
        messages: [
          {
            message_id: 'msg-u-1',
            turn_id: 'turn-par-1',
            role: 'user',
            content: 'Explore a autenticação e o executor de comandos em paralelo usando dois subagentes.',
            status: 'completed',
            retryable: false,
            attachments: [],
            command: null,
          },
          {
            message_id: 'msg-a-1',
            turn_id: 'turn-par-1',
            role: 'assistant',
            content: 'Iniciei dois subagentes em paralelo (`explore` e `explore-2`) para auditar os módulos.',
            status: 'streaming',
            retryable: false,
            attachments: [],
            command: null,
          },
        ],
        turns: [
          {
            turn_id: 'turn-par-1',
            state: 'running',
            created_at: '2026-10-17T12:00:00.000Z',
            started_at: '2026-10-17T12:00:01.000Z',
            finished_at: null,
            scheduled_by_schedule_id: null,
            code_mode: 'code',
          },
        ],
        activities: [
          {
            event_id: 'ev-main-read',
            cursor: '1',
            turn_id: 'turn-par-1',
            agent_id: 'agent:main',
            event_type: 'tool.finished',
            summary: 'Leu pyproject.toml',
            occurred_at: '2026-10-17T12:00:01.000Z',
            payload: {
              tool_name: 'read_file',
              tool_kind: 'filesystem',
              status: 'succeeded',
              path: 'pyproject.toml',
            },
          },
          {
            event_id: 'ev-sub1-1',
            cursor: '2',
            turn_id: 'turn-par-1',
            agent_id: 'agent:sub-1',
            parent_agent_id: 'agent:main',
            event_type: 'tool.finished',
            summary: 'Leu src/agentos/api/auth.py',
            occurred_at: '2026-10-17T12:00:02.000Z',
            payload: {
              agent_name: 'explore',
              tool_name: 'read_file',
              tool_kind: 'filesystem',
              status: 'succeeded',
              path: 'src/agentos/api/auth.py',
            },
          },
          {
            event_id: 'ev-sub2-1',
            cursor: '3',
            turn_id: 'turn-par-1',
            agent_id: 'agent:sub-2',
            parent_agent_id: 'agent:main',
            event_type: 'tool.finished',
            summary: 'Leu src/agentos/terminal/local.py',
            occurred_at: '2026-10-17T12:00:03.000Z',
            payload: {
              agent_name: 'explore-2',
              tool_name: 'read_file',
              tool_kind: 'filesystem',
              status: 'succeeded',
              path: 'src/agentos/terminal/local.py',
            },
          },
          {
            event_id: 'ev-sub1-2',
            cursor: '4',
            turn_id: 'turn-par-1',
            agent_id: 'agent:sub-1',
            parent_agent_id: 'agent:main',
            event_type: 'tool.finished',
            summary: 'Buscou csrf em src/agentos/api/',
            occurred_at: '2026-10-17T12:00:04.000Z',
            payload: {
              agent_name: 'explore',
              tool_name: 'search_files',
              tool_kind: 'filesystem',
              status: 'succeeded',
              path: 'src/agentos/api/',
            },
          },
          {
            event_id: 'ev-sub2-2',
            cursor: '5',
            turn_id: 'turn-par-1',
            agent_id: 'agent:sub-2',
            parent_agent_id: 'agent:main',
            event_type: 'tool.started',
            summary: 'Executando search_files em src/agentos/terminal/',
            occurred_at: '2026-10-17T12:00:05.000Z',
            payload: {
              agent_name: 'explore-2',
              tool_name: 'search_files',
              tool_kind: 'filesystem',
              status: 'running',
              path: 'src/agentos/terminal/',
              invocation_id: 'inv-sub2-running',
            },
          },
          {
            event_id: 'ev-msg-delta',
            cursor: '6',
            turn_id: 'turn-par-1',
            agent_id: 'agent:main',
            event_type: 'assistant.delta',
            summary: 'Respondendo',
            occurred_at: '2026-10-17T12:00:06.000Z',
            payload: {
              message_id: 'msg-a-1',
              content: 'Iniciei dois subagentes em paralelo (`explore` e `explore-2`) para auditar os módulos.',
            },
          },
        ],
        has_older_activities: false,
        oldest_activity_id: 1,
        activity_cursor: '6',
      })
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/conversations/conv-parallel-2') {
      await fulfillJson(route, {
        conversation_id: 'conv-parallel-2',
        title: 'Migração de Banco em Paralelo',
        state: 'running',
        provider: 'openrouter',
        model_id: 'qwen/qwen3.8-max-0902',
        mode: 'code',
        permission_mode: 'accept_edits',
        project_id: null,
        workspace: {
          mode: 'local_folder',
          path: 'C:/Projetos/orin-db',
          folder_name: 'orin-db',
          status: 'available',
        },
        messages: [
          {
            message_id: 'msg-u-2',
            turn_id: 'turn-par-2',
            role: 'user',
            content: 'Verifique os índices SQLite de conversas em background.',
            status: 'completed',
            retryable: false,
            attachments: [],
            command: null,
          },
          {
            message_id: 'msg-a-2',
            turn_id: 'turn-par-2',
            role: 'assistant',
            content: 'Analisando o schema SQLite na segunda sessão paralela sem bloquear a primeira.',
            status: 'streaming',
            retryable: false,
            attachments: [],
            command: null,
          },
        ],
        turns: [
          {
            turn_id: 'turn-par-2',
            state: 'running',
            created_at: '2026-10-17T12:01:00.000Z',
            started_at: '2026-10-17T12:01:01.000Z',
            finished_at: null,
            scheduled_by_schedule_id: null,
            code_mode: 'code',
          },
        ],
        activities: [
          {
            event_id: 'ev-s2-delta',
            cursor: '1',
            turn_id: 'turn-par-2',
            agent_id: 'agent:main-2',
            event_type: 'assistant.delta',
            summary: 'Respondendo',
            occurred_at: '2026-10-17T12:01:02.000Z',
            payload: {
              message_id: 'msg-a-2',
              content: 'Analisando o schema SQLite na segunda sessão paralela sem bloquear a primeira.',
            },
          },
        ],
        has_older_activities: false,
        oldest_activity_id: 1,
        activity_cursor: '1',
      })
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/settings/code-mode') {
      await fulfillJson(route, {
        autonomy: 'approval_required',
        system_notifications: false,
        monitoring_enabled: true,
      })
      return
    }

    if (request.method() === 'GET' && pathname.startsWith('/v1/providers')) {
      await fulfillJson(route, {
        items: [
          {
            provider: 'openrouter',
            model_id: 'qwen/qwen3.8-max-0902',
            display_name: 'Qwen 3.8 Max',
            context_window: 128000,
            capabilities: ['tools'],
            input_modalities: ['text'],
            output_modalities: ['text'],
            pricing: null,
            is_favorite: true,
            refreshed_at: null,
          },
        ],
      })
      return
    }

    if (pathname.endsWith('/events')) {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: heartbeat\ndata: {"cursor":"6"}\n\n',
      })
      return
    }

    await fulfillJson(route, {})
  })

  await page.setViewportSize({ width: 1280, height: 860 })
  await page.goto('/chats/conv-parallel-1')

  // 1. Verify sidebar active sessions badge and per-session running indicators
  const activeBadge = page.getByTestId('sidebar-active-sessions-badge')
  await expect(activeBadge).toBeVisible()
  await expect(activeBadge).toContainText('2 ativas')
  await expect(page.getByTestId('conversation-state-conv-parallel-1')).toHaveAttribute('data-state', 'running')
  await expect(page.getByTestId('conversation-state-conv-parallel-2')).toHaveAttribute('data-state', 'streaming')
  await expect(page.getByTestId('conversation-state-conv-idle-3')).toHaveAttribute('data-state', 'completed')

  // 2. Verify ParallelSubagentsGroup and distinct SubagentTaskContainer branches
  const parallelGroup = page.getByTestId('parallel-subagents-group')
  await expect(parallelGroup).toBeVisible()
  await expect(parallelGroup).toContainText('2 subagentes em paralelo')

  const subagentCards = page.getByTestId('subagent-task-group')
  await expect(subagentCards).toHaveCount(2)
  await expect(subagentCards.nth(0)).toHaveAttribute('data-agent-id', 'agent:sub-1')
  await expect(subagentCards.nth(0)).toHaveAttribute('data-status', 'completed')
  await expect(subagentCards.nth(0)).toContainText('explore')
  await expect(subagentCards.nth(0)).toContainText('Concluído')

  await expect(subagentCards.nth(1)).toHaveAttribute('data-agent-id', 'agent:sub-2')
  await expect(subagentCards.nth(1)).toHaveAttribute('data-status', 'running')
  await expect(subagentCards.nth(1)).toContainText('explore-2')
  await expect(subagentCards.nth(1)).toContainText('Em execução')

  // Expand the first subagent card so both subagent branches are visible for inspection
  await subagentCards.nth(0).getByTestId('subagent-task-toggle').click()

  if (processEnv.ORIN_MULTI_SESSION_DESKTOP_SHOT) {
    await page.screenshot({ path: processEnv.ORIN_MULTI_SESSION_DESKTOP_SHOT, fullPage: false })
  }

  // 3. Verify WCAG accessibility on desktop
  await assertNoCriticalOrSeriousViolations(page)

  // 4. Switch to the second running conversation and verify isolated state
  await page.getByRole('link', { name: /Migração de Banco em Paralelo/i }).click()
  await expect(
    page.getByText('Analisando o schema SQLite na segunda sessão paralela sem bloquear a primeira.'),
  ).toBeVisible()
  await expect(page.getByTestId('parallel-subagents-group')).toHaveCount(0)

  // 5. Switch back to conv-parallel-1 on mobile viewport and verify mobile drawer + active badge
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/chats/conv-parallel-1')
  await expect(page.getByTestId('parallel-subagents-group')).toBeVisible()
  await page.getByTestId('mobile-navigation-trigger').click()
  await expect(page.getByTestId('sidebar-active-sessions-badge')).toBeVisible()
  await expect(page.locator('#workspace-navigation-drawer')).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)')

  if (processEnv.ORIN_MULTI_SESSION_MOBILE_SHOT) {
    await page.screenshot({ path: processEnv.ORIN_MULTI_SESSION_MOBILE_SHOT, fullPage: false })
  }
})
