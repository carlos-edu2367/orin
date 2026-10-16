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
      '<head><meta name="csrf-token" content="csrf-code-ux-e2e">',
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

test('Orin Code v0.7.6 renders animated ThoughtBlock, subagent model badge, and fixed CodePermissionDock with keyboard/redirect actions and zero a11y violations', async ({
  page,
}) => {
  await installCsrfMeta(page)
  const resolvedPayloads: Array<{ turnId: string; body: Record<string, unknown> }> = []

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const { pathname } = url

    if (request.method() === 'GET' && pathname === '/v1/conversations/conv-code-ux') {
      await fulfillJson(route, {
        conversation_id: 'conv-code-ux',
        title: 'Landing Page SaaS IA · Orin Code',
        state: 'running',
        provider: 'openrouter',
        model_id: 'qwen/qwen3.8-max-0902',
        mode: 'code',
        permission_mode: 'ask_permissions',
        project_id: null,
        workspace: {
          mode: 'local_folder',
          path: 'C:/Projetos/nexus-ai-landing',
          folder_name: 'nexus-ai-landing',
          status: 'available',
        },
        messages: [
          {
            message_id: 'msg-user-1',
            turn_id: 'turn-code-1',
            role: 'user',
            content: 'Use o subagente luna para criar a copy PT-BR e suba um túnel de preview.',
            status: 'completed',
            retryable: false,
            attachments: [],
            command: null,
          },
          {
            message_id: 'msg-asst-1',
            turn_id: 'turn-code-1',
            role: 'assistant',
            content: 'Deleguei a criação da copy para o subagente luna e agora vou iniciar o túnel.',
            status: 'streaming',
            retryable: false,
            attachments: [],
            command: null,
          },
        ],
        turns: [
          {
            turn_id: 'turn-code-1',
            state: 'running',
            created_at: '2026-10-16T12:00:00.000Z',
            started_at: '2026-10-16T12:00:01.000Z',
            finished_at: null,
            scheduled_by_schedule_id: null,
            code_mode: 'code',
          },
        ],
        activities: [
          {
            event_id: 'ev-thought-1',
            cursor: '1',
            turn_id: 'turn-code-1',
            agent_id: 'agent:main',
            event_type: 'agent.thought_delta',
            summary: 'Pensando',
            occurred_at: '2026-10-16T12:00:01.000Z',
            payload: {
              delta:
                'Analisando os modelos favoritos disponíveis (~openai/gpt-luna-latest) e planejando a copy em PT-BR antes de expor o preview local.',
              iteration: 1,
            },
          },
          {
            event_id: 'ev-subagent-1',
            cursor: '2',
            turn_id: 'turn-code-1',
            agent_id: 'agent:main',
            event_type: 'tool.completed',
            summary: 'Copy PT-BR gerada pelo subagente',
            occurred_at: '2026-10-16T12:00:04.000Z',
            payload: {
              tool_name: 'run_subagent',
              tool_kind: 'delegation',
              status: 'completed',
              subagent_type: 'general',
              description: 'Copy PT-BR Hero & Pricing',
              model_id: '~openai/gpt-luna-latest',
            },
          },
          {
            event_id: 'ev-text-1',
            cursor: '3',
            turn_id: 'turn-code-1',
            agent_id: 'agent:main',
            event_type: 'assistant.delta',
            summary: 'Respondendo',
            occurred_at: '2026-10-16T12:00:05.000Z',
            payload: {
              message_id: 'msg-asst-1',
              content: 'Deleguei a criação da copy para o subagente luna e agora vou iniciar o túnel.',
            },
          },
          {
            event_id: 'ev-thought-2',
            cursor: '4',
            turn_id: 'turn-code-1',
            agent_id: 'agent:main',
            event_type: 'agent.thought_delta',
            summary: 'Pensando',
            occurred_at: '2026-10-16T12:00:06.000Z',
            payload: {
              delta: 'Verificando permissão para abrir túnel cloudflared na porta 5175...',
              iteration: 2,
            },
          },
          {
            event_id: 'ev-perm-1',
            cursor: '5',
            turn_id: 'turn-code-1',
            agent_id: 'agent:main',
            event_type: 'code_mode.decision_required',
            summary: 'Permissão necessária para executar `npx cloudflared tunnel --url http://localhost:5175`',
            occurred_at: '2026-10-16T12:00:07.000Z',
            payload: {
              stage: 'waiting_decision',
              state: 'waiting_user',
              tool_name: 'run_command',
              permission_request_id: 'perm_01',
              suggested_rule: 'Bash(npx cloudflared:*)',
              rule_suggestion: 'Bash(npx cloudflared:*)',
              permission_mode: 'ask_permissions',
              command: 'npx cloudflared tunnel --url http://localhost:5175',
              permission_request: {
                request_id: 'perm_01',
                status: 'pending',
                tool_name: 'run_command',
                command: 'npx cloudflared tunnel --url http://localhost:5175',
                suggested_rule: 'Bash(npx cloudflared:*)',
                rule_suggestion: 'Bash(npx cloudflared:*)',
                permission_mode: 'ask_permissions',
                turn_id: 'turn-code-1',
              },
            },
          },
        ],
        has_older_activities: false,
        oldest_activity_id: 1,
        activity_cursor: '5',
      })
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/conversations') {
      await fulfillJson(route, { items: [] })
      return
    }

    if (request.method() === 'GET' && pathname === '/v1/projects') {
      await fulfillJson(route, { items: [] })
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

    if (
      request.method() === 'POST' &&
      pathname === '/v1/conversations/conv-code-ux/turns/turn-code-1/tool-permission'
    ) {
      const body = (request.postDataJSON() ?? {}) as Record<string, unknown>
      resolvedPayloads.push({ turnId: 'turn-code-1', body })
      await fulfillJson(route, {
        request_id: 'perm_01',
        decision: body.decision ?? 'deny',
        allowed: body.decision === 'allow_once' || body.decision === 'allow_always',
      })
      return
    }

    if (pathname.endsWith('/events')) {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: heartbeat\ndata: {"cursor":"5"}\n\n',
      })
      return
    }

    await fulfillJson(route, {})
  })

  await page.setViewportSize({ width: 1280, height: 860 })
  await page.goto('/chats/conv-code-ux')

  // 1. Verify completed ThoughtBlock ("Pensou") and subagent model badge (~openai/gpt-luna-latest)
  const completedThought = page.getByTestId('thought-block-completed').first()
  await expect(completedThought).toBeVisible()
  await expect(page.getByText('~openai/gpt-luna-latest')).toBeVisible()

  // 2. Verify fixed CodePermissionDock above composer input
  const dock = page.getByTestId('code-permission-dock')
  await expect(dock).toBeVisible()
  await expect(dock).toHaveClass(/code-perm-dock--high-risk/)
  await expect(dock).toContainText('npx cloudflared tunnel --url http://localhost:5175')
  await expect(page.getByTestId('code-perm-allow-once')).toBeVisible()
  await expect(page.getByTestId('code-perm-allow-always')).toBeVisible()
  await expect(page.getByTestId('code-perm-deny')).toBeVisible()

  // 3. Optional visual screenshot when requested
  if (processEnv.ORIN_ARTIFACT_SHOT) {
    await page.screenshot({ path: processEnv.ORIN_ARTIFACT_SHOT, fullPage: false })
  }

  // 4. Verify WCAG accessibility with CodePermissionDock and ThoughtBlock mounted
  await assertNoCriticalOrSeriousViolations(page)

  // 5. Test "Negar e orientar..." inline redirect flow
  await page.getByTestId('code-perm-deny-instruct-toggle').click()
  const redirectInput = page.getByPlaceholder('Diga ao Orin como fazer diferente...')
  await expect(redirectInput).toBeVisible()
  await redirectInput.fill('Use apenas localhost:5175 sem abrir túnel público')
  await page.getByRole('button', { name: 'Enviar orientação' }).click()

  await expect.poll(() => resolvedPayloads.length).toBe(1)
  expect(resolvedPayloads[0]).toEqual({
    turnId: 'turn-code-1',
    body: {
      request_id: 'perm_01',
      decision: 'deny',
      reason: 'Use apenas localhost:5175 sem abrir túnel público',
    },
  })

  // 6. Verify inline receipt immediately transitions to denied and Stop button reappears
  await expect(page.getByTestId('perm-receipt-denied')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Parar execução' })).toBeVisible()
})
