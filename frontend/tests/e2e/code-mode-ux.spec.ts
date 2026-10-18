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
    let response
    try {
      response = await route.fetch()
    } catch {
      response = await route.fetch()
    }
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

test('renders streaming ThoughtBlock collapsed by default, expands on click, places Trabalhou por Xs at bottom, and groups 6 parallel subagents', async ({
  page,
}) => {
  await installCsrfMeta(page)

  const parallelSubagentActivities = Array.from({ length: 6 }, (_, idx) => ({
    event_id: `ev-sub-tool-${idx + 1}`,
    cursor: `${idx + 2}`,
    turn_id: 'turn-done-1',
    agent_id: `agent:sub:${idx + 1}`,
    parent_agent_id: 'agent:main',
    agent_name: `explore-${idx + 1}`,
    event_type: 'tool.finished',
    kind: 'tool',
    state: 'completed',
    tool_name: 'read_file',
    summary: `Leu src/module-${idx + 1}.ts`,
    occurred_at: `2026-10-16T12:00:0${idx + 2}.000Z`,
    payload: {
      agent_name: `explore-${idx + 1}`,
      tool_name: 'read_file',
      tool_kind: 'filesystem',
      status: 'succeeded',
      path: `src/module-${idx + 1}.ts`,
    },
  }))

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const { pathname } = url

    if (request.method() === 'GET' && pathname === '/v1/conversations/conv-thought-subagents') {
      await fulfillJson(route, {
        conversation_id: 'conv-thought-subagents',
        title: 'Pensamento Expansível & 6 Subagentes · Orin Code',
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
            message_id: 'msg-u1',
            turn_id: 'turn-done-1',
            role: 'user',
            content: 'Analise os 6 módulos em paralelo.',
            status: 'completed',
            retryable: false,
            attachments: [],
            command: null,
          },
          {
            message_id: 'msg-a1',
            turn_id: 'turn-done-1',
            role: 'assistant',
            content: 'Concluí a análise paralela dos 6 módulos sem bloqueio de limite.',
            status: 'completed',
            retryable: false,
            attachments: [],
            command: null,
          },
          {
            message_id: 'msg-u2',
            turn_id: 'turn-live-2',
            role: 'user',
            content: 'Agora planeje a refatoração.',
            status: 'completed',
            retryable: false,
            attachments: [],
            command: null,
          },
          {
            message_id: 'msg-a2',
            turn_id: 'turn-live-2',
            role: 'assistant',
            content: '',
            status: 'streaming',
            retryable: false,
            attachments: [],
            command: null,
          },
        ],
        turns: [
          {
            turn_id: 'turn-done-1',
            state: 'completed',
            created_at: '2026-10-16T12:00:00.000Z',
            started_at: '2026-10-16T12:00:01.000Z',
            finished_at: '2026-10-16T12:00:08.000Z',
            scheduled_by_schedule_id: null,
            code_mode: 'code',
          },
          {
            turn_id: 'turn-live-2',
            state: 'running',
            created_at: '2026-10-16T12:01:00.000Z',
            started_at: '2026-10-16T12:01:01.000Z',
            finished_at: null,
            scheduled_by_schedule_id: null,
            code_mode: 'code',
          },
        ],
        activities: [
          {
            event_id: 'ev-main-start-1',
            cursor: '1',
            turn_id: 'turn-done-1',
            agent_id: 'agent:main',
            event_type: 'turn.started',
            kind: 'lifecycle',
            state: 'working',
            summary: 'Iniciado',
            occurred_at: '2026-10-16T12:00:01.000Z',
            payload: {},
          },
          ...parallelSubagentActivities,
          {
            event_id: 'ev-main-text-1',
            cursor: '8',
            turn_id: 'turn-done-1',
            agent_id: 'agent:main',
            event_type: 'assistant.delta',
            kind: 'message',
            state: 'working',
            summary: 'Respondendo',
            occurred_at: '2026-10-16T12:00:07.500Z',
            payload: {
              message_id: 'msg-a1',
              content: 'Concluí a análise paralela dos 6 módulos sem bloqueio de limite.',
            },
          },
          {
            event_id: 'ev-main-done-1',
            cursor: '9',
            turn_id: 'turn-done-1',
            agent_id: 'agent:main',
            event_type: 'turn.completed',
            kind: 'lifecycle',
            state: 'completed',
            summary: 'Concluído',
            occurred_at: '2026-10-16T12:00:08.000Z',
            payload: {},
          },
          {
            event_id: 'ev-live-thought-2',
            cursor: '10',
            turn_id: 'turn-live-2',
            agent_id: 'agent:main',
            event_type: 'agent.thought_delta',
            kind: 'lifecycle',
            state: 'working',
            summary: 'Pensando',
            occurred_at: '2026-10-16T12:01:02.000Z',
            payload: {
              delta: 'Raciocínio interno detalhado que deve ficar oculto por padrão enquanto pensa para não poluir a interface.',
              iteration: 1,
            },
          },
        ],
        has_older_activities: false,
        oldest_activity_id: 1,
        activity_cursor: '10',
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
        autonomy: 'full_autonomy',
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
        body: 'event: heartbeat\ndata: {"cursor":"10"}\n\n',
      })
      return
    }
    await fulfillJson(route, {})
  })

  await page.setViewportSize({ width: 1280, height: 860 })
  await page.goto('/chats/conv-thought-subagents')

  // 1. Verify 6 parallel subagents group in turn 1
  const parallelGroup = page.getByTestId('parallel-subagents-group')
  await expect(parallelGroup).toBeVisible()
  await expect(parallelGroup).toContainText('6 subagentes em paralelo')

  // 2. Verify "Trabalhou por 7s" is rendered at the bottom of turn 1 (below the assistant text)
  const workDuration = page.getByTestId('turn-work-duration')
  await expect(workDuration).toHaveText('Trabalhou por 7s')
  const assistantText = page.getByText('Concluí a análise paralela dos 6 módulos sem bloqueio de limite.')
  const textBox = await assistantText.boundingBox()
  const durationBox = await workDuration.boundingBox()
  expect(textBox).not.toBeNull()
  expect(durationBox).not.toBeNull()
  expect(durationBox!.y).toBeGreaterThan(textBox!.y)

  // 3. Verify streaming ThoughtBlock in turn 2 is collapsed by default
  const streamingThought = page.getByTestId('thought-block-streaming')
  await expect(streamingThought).toBeVisible()
  const streamingToggle = streamingThought.getByTestId('thought-block-toggle')
  await expect(streamingToggle).toHaveAttribute('aria-expanded', 'false')
  await expect(streamingToggle).toContainText('Pensando...')
  await expect(
    page.getByText('Raciocínio interno detalhado que deve ficar oculto por padrão enquanto pensa para não poluir a interface.'),
  ).toBeHidden()

  if (processEnv.ORIN_SHOT_COLLAPSED) {
    await page.screenshot({ path: processEnv.ORIN_SHOT_COLLAPSED, fullPage: false })
  }

  // 4. Click toggle to expand streaming ThoughtBlock live
  await streamingToggle.click()
  await expect(streamingToggle).toHaveAttribute('aria-expanded', 'true')
  await expect(
    page.getByText('Raciocínio interno detalhado que deve ficar oculto por padrão enquanto pensa para não poluir a interface.'),
  ).toBeVisible()

  if (processEnv.ORIN_SHOT_EXPANDED) {
    await page.screenshot({ path: processEnv.ORIN_SHOT_EXPANDED, fullPage: false })
  }

  // 5. Check mobile viewport (390x844) and accessibility
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(streamingThought).toBeVisible()
  await expect(workDuration).toBeVisible()
  if (processEnv.ORIN_SHOT_MOBILE) {
    await page.screenshot({ path: processEnv.ORIN_SHOT_MOBILE, fullPage: false })
  }

  await assertNoCriticalOrSeriousViolations(page)
})

test('renders redesigned CodeSessionProposalCard, UserQuestionCard with collapsible note, and Playwright MCP BrowserSection across desktop and mobile with zero a11y violations', async ({
  page,
}) => {
  await installCsrfMeta(page)

  let playwrightInstalled = false

  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const { pathname } = url

    if (request.method() === 'GET' && pathname === '/v1/conversations/conv-decision-cards') {
      await fulfillJson(route, {
        conversation_id: 'conv-decision-cards',
        title: 'Landing Page Nexus AI · Orin',
        state: 'waiting_user',
        provider: 'openrouter',
        model_id: 'qwen/qwen3.7-flash',
        mode: 'chat',
        permission_mode: 'accept_edits',
        project_id: null,
        workspace: {
          mode: 'managed',
          path: null,
          folder_name: null,
          status: 'available',
        },
        messages: [
          {
            message_id: 'msg-u1',
            turn_id: 'turn-ask-1',
            role: 'user',
            content: 'Crie uma landing page completa para a Nexus AI.',
            status: 'completed',
            retryable: false,
            attachments: [],
            command: null,
          },
          {
            message_id: 'msg-a1',
            turn_id: 'turn-ask-1',
            role: 'assistant',
            content: 'Preparei as opções de direção visual e a proposta de sessão Orin Code.',
            status: 'completed',
            retryable: false,
            attachments: [],
            command: null,
          },
        ],
        turns: [
          {
            turn_id: 'turn-ask-1',
            state: 'waiting_user',
            created_at: '2026-10-18T00:10:00.000Z',
            started_at: '2026-10-18T00:10:01.000Z',
            finished_at: null,
            scheduled_by_schedule_id: null,
            code_mode: 'chat',
          },
        ],
        activities: [
          {
            event_id: 'ev-ask-user-1',
            cursor: '1',
            turn_id: 'turn-ask-1',
            agent_id: 'agent:main',
            event_type: 'tool.finished',
            kind: 'tool',
            state: 'waiting_user',
            tool_name: 'ask_user',
            summary: 'Aguardando sua resposta',
            occurred_at: '2026-10-18T00:10:03.000Z',
            payload: {
              tool_name: 'ask_user',
              tool_kind: 'user_input',
              status: 'succeeded',
              invocation_id: 'call-ask-1',
              questions: [
                {
                  id: 'theme_style',
                  question: 'Qual direção estética você prefere para a hero section?',
                  description: 'Definiremos a paleta tipográfica e os blocos de destaque a partir desta escolha.',
                  mode: 'single_choice',
                  options: [
                    {
                      id: 'editorial_dark',
                      label: 'Editorial Dark (Recomendado)',
                      description: 'Contraste alto, tipografia serifada editorial e detalhes em violeta.',
                    },
                    {
                      id: 'swiss_minimal',
                      label: 'Swiss Minimalist',
                      description: 'Fundo claro aquecido, grid rigoroso e foco tipográfico.',
                    },
                  ],
                },
              ],
            },
          },
          {
            event_id: 'ev-code-proposal-1',
            cursor: '2',
            turn_id: 'turn-ask-1',
            agent_id: 'agent:main',
            event_type: 'tool.finished',
            kind: 'tool',
            state: 'waiting_user',
            tool_name: 'propose_code_session',
            summary: 'Proposta de sessão Orin Code: Landing Page Nexus AI',
            occurred_at: '2026-10-18T00:10:04.000Z',
            payload: {
              tool_name: 'propose_code_session',
              tool_kind: 'user_input',
              status: 'succeeded',
              invocation_id: 'call-prop-1',
              code_session_proposal: {
                proposal_id: 'prop-e2e-1',
                title: 'Landing Page Nexus AI',
                summary: 'Criar uma página de alta conversão em React + Vite com verificação via Playwright MCP.',
                prompt:
                  'Construir a landing page da Nexus AI com Hero, Bento Grid de recursos, seção de preços e validação responsiva completa.',
                workspace_path: 'C:/Projetos/nexus-ai-web',
                provider: 'openai',
                model_id: 'default',
                permission_mode: 'accept_edits',
                status: 'pending',
              },
            },
          },
          {
            event_id: 'ev-wait-1',
            cursor: '3',
            turn_id: 'turn-ask-1',
            agent_id: 'agent:main',
            event_type: 'turn.waiting_user',
            kind: 'lifecycle',
            state: 'waiting_user',
            summary: 'Aguardando sua resposta',
            occurred_at: '2026-10-18T00:10:05.000Z',
            payload: { state: 'waiting_user' },
          },
        ],
        has_older_activities: false,
        oldest_activity_id: 1,
        activity_cursor: '3',
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
            model_id: 'qwen/qwen3.7-flash',
            display_name: 'Qwen3.7 Flash',
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
    if (request.method() === 'GET' && pathname === '/v1/mcp/servers') {
      await fulfillJson(
        route,
        playwrightInstalled
          ? [
              {
                server_id: 'mcp-pw-e2e',
                slug: 'playwright',
                display_name: 'Playwright',
                transport: 'stdio',
                command: 'npx',
                args: ['-y', '@playwright/mcp@latest'],
                url: null,
                secret_names: [],
                catalog_id: 'playwright',
                state: 'active',
                state_reason: '',
                protocol_version: '2024-11-05',
                tool_count: 24,
                auth_kind: 'none',
                tools: [{ name: 'browser_navigate', description: 'Navigate to URL', enabled: true }],
              },
            ]
          : [],
      )
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/mcp/servers') {
      await fulfillJson(
        route,
        {
          server_id: 'mcp-pw-e2e',
          slug: 'playwright',
          display_name: 'Playwright',
          transport: 'stdio',
          command: 'npx',
          args: ['-y', '@playwright/mcp@latest'],
          url: null,
          secret_names: [],
          catalog_id: 'playwright',
          state: 'pending_approval',
          state_reason: '',
          protocol_version: '2024-11-05',
          tool_count: 0,
          auth_kind: 'none',
          tools: [],
        },
        201,
      )
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/mcp/servers/mcp-pw-e2e/approve') {
      playwrightInstalled = true
      await fulfillJson(route, {
        server_id: 'mcp-pw-e2e',
        slug: 'playwright',
        display_name: 'Playwright',
        transport: 'stdio',
        command: 'npx',
        args: ['-y', '@playwright/mcp@latest'],
        url: null,
        secret_names: [],
        catalog_id: 'playwright',
        state: 'active',
        state_reason: '',
        protocol_version: '2024-11-05',
        tool_count: 24,
        auth_kind: 'none',
        tools: [{ name: 'browser_navigate', description: 'Navigate to URL', enabled: true }],
      })
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/mcp/servers/mcp-pw-e2e/test') {
      await fulfillJson(route, {
        connected: true,
        protocol_version: '2024-11-05',
        tools: ['browser_navigate', 'browser_snapshot', 'browser_click'],
        error: null,
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
    await fulfillJson(route, {})
  })

  await page.setViewportSize({ width: 1280, height: 860 })
  await page.goto('/chats/conv-decision-cards')

  // 1. Verify UserQuestionCard renders with collapsed optional note
  const questionCard = page.getByTestId('user-question-card')
  await expect(questionCard).toBeVisible()
  await expect(questionCard).toContainText('Qual direção estética você prefere para a hero section?')
  const noteToggle = questionCard.getByTestId('user-question-note-toggle')
  await expect(noteToggle).toBeVisible()
  await expect(questionCard.getByLabel('Observação (opcional)')).toBeHidden()

  // 2. Verify CodeSessionProposalCard inherits openrouter + Qwen3.7 Flash, shows unredacted prompt and folder picker
  const proposalCard = page.getByTestId('code-proposal-card')
  await expect(proposalCard).toBeVisible()
  const providerSelect = proposalCard.getByLabel('Provedor') as unknown as ReturnType<Page['locator']>
  await expect(providerSelect).toHaveValue('openrouter')
  const modelSelect = proposalCard.getByLabel('Modelo') as unknown as ReturnType<Page['locator']>
  await expect(modelSelect).toHaveValue('qwen/qwen3.7-flash')
  await expect(proposalCard.getByLabel('Prompt / Especificação')).toHaveValue(
    'Construir a landing page da Nexus AI com Hero, Bento Grid de recursos, seção de preços e validação responsiva completa.',
  )
  await expect(proposalCard.getByTestId('code-proposal-choose-folder-button')).toBeVisible()

  await questionCard.scrollIntoViewIfNeeded()
  if (processEnv.ORIN_SHOT_CARDS_DESKTOP) {
    await page.screenshot({ path: processEnv.ORIN_SHOT_CARDS_DESKTOP, fullPage: false })
  }

  // Expand optional note in UserQuestionCard to verify toggle interaction
  await noteToggle.click()
  await expect(questionCard.getByLabel('Observação (opcional)')).toBeVisible()

  await assertNoCriticalOrSeriousViolations(page)

  // 3. Mobile viewport (390x844) verification for decision cards
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(proposalCard).toBeVisible()
  if (processEnv.ORIN_SHOT_CARDS_MOBILE) {
    await page.screenshot({ path: processEnv.ORIN_SHOT_CARDS_MOBILE, fullPage: true })
  }

  // 4. Verify Playwright MCP BrowserSection in PreferencesModal on desktop
  await page.setViewportSize({ width: 1280, height: 860 })
  await page.getByRole('button', { name: 'Preferências' }).click()
  await page.getByTestId('preferences-tab-system').click()

  const installBtn = page.getByRole('button', { name: 'Instalar e ativar Playwright MCP' })
  await expect(installBtn).toBeVisible()
  await installBtn.click()
  await expect(page.getByTestId('playwright-mcp-status')).toHaveText('Ativo')
  await expect(page.getByText(/24 ferramentas disponíveis/i)).toBeVisible()

  if (processEnv.ORIN_SHOT_BROWSER_MCP) {
    await page.screenshot({ path: processEnv.ORIN_SHOT_BROWSER_MCP, fullPage: false })
  }

  await assertNoCriticalOrSeriousViolations(page)
})


