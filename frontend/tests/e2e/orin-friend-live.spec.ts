import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Route } from '@playwright/test'

const FRIEND_CONVERSATION_ID = 'conv-orin-friend-pinned'
const CODE_CONVERSATION_ID = 'conv-orin-code-delegated'

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
      '<head><meta name="csrf-token" content="csrf-e2e-orin-friend-live">',
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

test.describe('Orin Friend & Modo Conversa Live (E2E)', () => {
  test.beforeEach(async ({ page }) => {
    await installCsrfMeta(page)

    await page.route('**/v1/**', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()

      if ((path === '/v1/projects' || path === '/v1/projects/sidebar') && method === 'GET') {
        await fulfillJson(route, { items: [] })
        return
      }

      if (path === '/v1/conversations' && method === 'GET') {
        await fulfillJson(route, {
          items: [
            {
              conversation_id: FRIEND_CONVERSATION_ID,
              title: 'Orin Friend',
              mode: 'friend',
              permission_mode: 'ask_permissions',
              state: 'completed',
              updated_at: '2026-10-27T12:05:00Z',
            },
          ],
        })
        return
      }

      if (path === '/v1/conversations/friend/ensure' && method === 'POST') {
        await fulfillJson(route, {
          conversation_id: FRIEND_CONVERSATION_ID,
          title: 'Orin Friend',
          mode: 'friend',
          permission_mode: 'ask_permissions',
          state: 'completed',
          conversation: {
            conversation_id: FRIEND_CONVERSATION_ID,
            title: 'Orin Friend',
            mode: 'friend',
            permission_mode: 'ask_permissions',
            state: 'completed',
          },
        })
        return
      }

      if ((path === '/v1/friend/persona' || path === '/v1/friend/soul/sync') && (method === 'GET' || method === 'PUT' || method === 'POST')) {
        await fulfillJson(route, {
          traits: {
            warmth: 0.72,
            humor: 0.58,
            sarcasm: 0.25,
            formality: 0.30,
            verbosity: 0.40,
            candor: 0.75,
            energy: 0.60,
          },
          affinity_score: 68.5,
          bond_stage: 'close_friend',
          style_notes: ['Gosta de respostas diretas'],
          current_mood: 'focado e bem-humorado',
          soul_markdown_path: 'C:/Users/User/.orin/friend/SOUL.md',
          updated_at: '2026-10-27T15:00:00Z',
        })
        return
      }

      if (path === '/v1/friend/memories' && method === 'GET') {
        await fulfillJson(route, {
          items: [
            {
              id: 'mem-1',
              kind: 'inside_joke',
              content: 'Nunca confie num regex escrito às 3h da manhã',
              importance: 0.9,
              emotion_tag: 'humor',
              created_at: '2026-10-25T10:00:00Z',
            },
          ],
        })
        return
      }

      if (path === `/v1/conversations/${FRIEND_CONVERSATION_ID}` && method === 'GET') {
        await fulfillJson(route, {
          conversation_id: FRIEND_CONVERSATION_ID,
          title: 'Orin Friend',
          state: 'completed',
          provider: 'openai',
          model_id: 'gpt-4o',
          mode: 'friend',
          permission_mode: 'ask_permissions',
          project_id: null,
          project_name: null,
          workspace: {
            kind: 'managed',
            path: null,
            folder_name: null,
            scope: 'chat',
            project_name: null,
          },
          messages: [
            {
              message_id: 'msg-friend-u1',
              turn_id: 'turn-friend-1',
              role: 'user',
              content: 'Vamos rodar os testes da branch e corrigir a reconexão WebSocket.',
              status: 'completed',
              retryable: false,
              attachments: [],
              command: null,
            },
            {
              message_id: 'msg-friend-a1',
              turn_id: 'turn-friend-1',
              role: 'assistant',
              content: 'E aí! Já deixei o Orin Code rodando os testes da branch enquanto a gente conversa.',
              status: 'completed',
              retryable: false,
              attachments: [],
              command: null,
            },
          ],
          turns: [
            {
              turn_id: 'turn-friend-1',
              state: 'completed',
              created_at: '2026-10-27T12:00:00.000Z',
              started_at: '2026-10-27T12:00:01.000Z',
              finished_at: '2026-10-27T12:00:05.000Z',
              scheduled_by_schedule_id: null,
              code_mode: null,
            },
          ],
          activities: [
            {
              event_id: 'ev-friend-code-1',
              turn_id: 'turn-friend-1',
              agent_id: 'agent:conv-orin-friend-pinned:main',
              event_type: 'code_session.progress',
              sequence: 1,
              summary: 'Supervisionando sessão Orin Code: 3/4 concluídas — Reta final!',
              occurred_at: '2026-10-27T12:00:03.000Z',
              payload: {
                status: 'running',
                tool_kind: 'code_handoff',
                delegated_code_session: {
                  code_conversation_id: CODE_CONVERSATION_ID,
                  goal: 'Executar suíte de testes e corrigir reconexão WebSocket',
                  status: 'running',
                  completed_todo_count: 3,
                  total_todo_count: 4,
                  active_task_title: 'Rodando pytest',
                  modified_files_count: 2,
                  examined_files_count: 5,
                  friendly_milestone: '3/4 concluídas — Reta final!',
                },
              },
            },
          ],
          activity_cursor: '1',
          context_usage: null,
        })
        return
      }

      if (path.endsWith('/queue') || path.endsWith('/commands') || path.endsWith('/tasks') || path.endsWith('/skills')) {
        await fulfillJson(route, { items: [], commands: [], tasks: [] })
        return
      }

      if (path.endsWith('/statusline')) {
        await fulfillJson(route, { enabled: false, lines: [], error: 'not_configured' })
        return
      }

      if (path.endsWith('/events')) {
        await route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: 'event: heartbeat\ndata: {"cursor":"1"}\n\n',
        })
        return
      }

      if (path.endsWith('/v1/live-voice/hardware') || path.endsWith('/v1/live-voice/hardware-status')) {
        await fulfillJson(route, {
          gpu_available: true,
          gpu_name: 'NVIDIA GeForce RTX 4060',
          free_vram_gb: 8.0,
          free_ram_gb: 16.0,
          recommended_tts_provider: 'local_sidecar',
          recommended_tts_model: 'chatterbox-pt-br',
          recommended_stt_provider: 'local_sidecar',
          recommended_stt_model: 'faster-whisper-large-v3-turbo',
          reason: 'RTX 4060 detectada com 8.0 GB VRAM — Perfil 100% Local Expressivo recomendado.',
        })
        return
      }

      if (path.includes('/v1/settings/media-models/') && method === 'PUT') {
        const modality = path.split('/').pop() ?? 'audio'
        await fulfillJson(route, {
          modality,
          provider: 'local_sidecar',
          model_id: modality === 'audio' ? 'chatterbox-pt-br' : 'faster-whisper-large-v3-turbo',
          mode: 'manual',
        })
        return
      }

      await fulfillJson(route, { items: [] })
    })
  })

  test('abre Orin Friend fixado, inspeciona SOUL.md & Memórias, exibe CodeSessionLiveCard e ativa LiveVoiceStage', async ({ page }) => {
    await page.goto('/')

    const friendSidebarBtn = page.getByTestId('sidebar-orin-friend-button')
    await expect(friendSidebarBtn).toBeVisible()
    await friendSidebarBtn.click()

    // Verifica o CodeSessionLiveCard embutido na conversa do Orin Friend
    const codeLiveCard = page.getByTestId('code-session-live-card')
    await expect(codeLiveCard).toBeVisible()
    await expect(codeLiveCard).toContainText('3/4 concluídas — Reta final!')

    // Abre o FriendSoulDrawer ("Caixa de Vidro")
    const soulBtn = page.getByTestId('open-friend-soul-drawer-button')
    await expect(soulBtn).toBeVisible()
    await soulBtn.click()

    const soulDrawer = page.getByTestId('friend-soul-drawer')
    await expect(soulDrawer).toBeVisible()
    await expect(soulDrawer).toContainText('Nunca confie num regex escrito às 3h da manhã')

    // Fecha o drawer e abre o LiveVoiceStage
    await page.getByTestId('close-friend-soul-drawer-button').click()

    const liveToggle = page.getByTestId('composer-live-voice-toggle')
    await expect(liveToggle).toBeVisible()
    await liveToggle.click()

    const liveStage = page.getByTestId('live-voice-stage')
    await expect(liveStage).toBeVisible()

    // Abre a Configuração Rápida de Voz (1 Clique) direto de dentro do LiveVoiceStage
    const openQuickSettingsBtn = page.getByTestId('live-voice-open-settings-button')
    await expect(openQuickSettingsBtn).toBeVisible()
    await openQuickSettingsBtn.click()

    const quickSettingsModal = page.getByTestId('live-voice-quick-settings')
    await expect(quickSettingsModal).toBeVisible()
    await expect(page.getByTestId('live-quick-hardware-banner')).toContainText('RTX 4060')

    await page.getByTestId('live-preset-card-hybrid_fast').click()
    await page.getByTestId('live-quick-test-voice-button').click()
    await expect(page.getByTestId('live-quick-voice-preview-status')).toContainText('Olá!')

    await assertNoCriticalOrSeriousViolations(page)
  })
})
