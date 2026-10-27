import AxeBuilder from '@axe-core/playwright'
import { Buffer } from 'node:buffer'
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

// A fake capture device (a steady tone) lets the Live stage prove the microphone works.
test.use({
  permissions: ['microphone'],
  launchOptions: { args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--use-gl=swiftshader', '--enable-unsafe-swiftshader'] },
})

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
          display_name: 'Orin',
          user_nickname: 'Carlos',
          orb_theme: 'aurora',
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

      if (path === '/v1/live-voice/capabilities') {
        await fulfillJson(route, {
          stt: {
            local_sidecar: {
              available: true, device: 'cuda', model_id: 'faster-whisper-large-v3-turbo', reason: 'Whisper local pronto (GPU).',
              install: { engine: 'whisper', label: 'Whisper local', installed: true, state: 'installed', step: '', message: '', device: 'cuda', size_hint: '', gpu: true },
            },
            openai: { available: false, model_id: 'gpt-4o-mini-transcribe', reason: '' },
            openrouter: { available: false, model_id: 'openai/whisper-large-v3-turbo', reason: '' },
            groq: { available: false, model_id: 'whisper-large-v3-turbo', reason: '' },
          },
          tts: {
            openai: { available: false, model_id: 'gpt-4o-mini-tts', voices: [], reason: '' },
            openrouter: { available: false, models: [], reason: '' },
            kokoro: { available: false, model_id: 'kokoro-82m-onnx', voices: ['pf_dora'], install: { engine: 'kokoro', label: 'Kokoro', installed: false, state: 'idle', step: '', message: '', device: null, size_hint: '~400 MB', gpu: true } },
            chatterbox: { available: false, model_id: 'chatterbox-multilingual', voices: ['default'], voice_clips: [{ id: 'default', name: 'Voz padrão do Chatterbox' }], install: { engine: 'chatterbox', label: 'Chatterbox', installed: false, state: 'idle', step: '', message: '', device: null, size_hint: '~6 GB', gpu: true } },
          },
          recommended_stt: 'local_sidecar',
          recommended_tts: 'browser',
          hardware: { has_cuda: true, device_name: 'NVIDIA GeForce RTX 4060', free_vram_gb: 6, total_vram_gb: 8 },
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

    await expect(soulDrawer.getByTestId('friend-name-input')).toHaveValue('Orin')
    await page.screenshot({ path: test.info().outputPath('friend-personalize.png') })

    // Fecha o drawer e abre o LiveVoiceStage
    await page.getByTestId('close-friend-soul-drawer').click()

    const liveToggle = page.getByTestId('composer-live-voice-toggle')
    await expect(liveToggle).toBeVisible()
    await liveToggle.click()

    const liveStage = page.getByTestId('live-voice-stage')
    await expect(liveStage).toBeVisible()
    // The fake capture device plays a tone: the meter must show the microphone is alive.
    await expect(page.getByTestId('live-mic-meter')).not.toHaveAttribute('aria-valuenow', '0', { timeout: 10_000 })
    await expect(page.getByTestId('live-voice-active-profile-pill')).toContainText('Whisper local')
    await page.waitForTimeout(800)
    await page.screenshot({ path: test.info().outputPath('live-stage.png') })

    const openQuickSettingsBtn = page.getByTestId('live-voice-open-settings-button')
    await expect(openQuickSettingsBtn).toBeVisible()
    await openQuickSettingsBtn.click()

    const quickSettingsModal = page.getByTestId('live-voice-quick-settings')
    await expect(quickSettingsModal).toBeVisible()
    await expect(page.getByTestId('live-stt-option-local_sidecar')).toContainText('Pronto · GPU')
    await expect(page.getByTestId('live-stt-option-local_sidecar')).toContainText('RTX 4060')
    await page.screenshot({ path: test.info().outputPath('voice-settings.png'), fullPage: true })

    await assertNoCriticalOrSeriousViolations(page)
  })

  test('ouve o microfone de verdade: envia o áudio pelo WebSocket, recebe a transcrição e manda a mensagem', async ({ page }) => {
    // A short end-of-sentence pause so the fake device's beeps close an utterance quickly.
    await page.addInitScript(() => {
      window.localStorage.setItem('orin.live-voice.prefs.v1', JSON.stringify({ endSilenceMs: 400, sensitivity: 0.7 }))
    })
    const received = { binaryFrames: 0, speechEnds: 0, configs: [] as Array<Record<string, unknown>>, csrf: '' }
    await page.routeWebSocket(/\/v1\/conversations\/[^/]+\/live/, (ws) => {
      received.csrf = new URL(ws.url()).searchParams.get('csrf') ?? ''
      ws.send(JSON.stringify({ type: 'session.ready', conversation_id: FRIEND_CONVERSATION_ID, audio_epoch: 1 }))
      ws.onMessage((message) => {
        if (typeof message !== 'string') {
          received.binaryFrames += 1
          return
        }
        const event = JSON.parse(message) as Record<string, unknown>
        if (event.type === 'config') received.configs.push(event)
        if (event.type === 'audio_speech_end' && received.speechEnds === 0) {
          received.speechEnds += 1
          ws.send(JSON.stringify({ type: 'stt.processing', provider: 'local_sidecar', audio_ms: 900 }))
          ws.send(JSON.stringify({ type: 'stt.transcript', is_end_of_utterance: true, text: 'Oi Orin, tudo bem?', audio_epoch: 1 }))
        }
      })
    })

    await page.goto('/')
    await page.getByTestId('sidebar-orin-friend-button').click()
    const sent = page.waitForRequest((request) => request.method() === 'POST' && /\/v1\/conversations\/[^/]+\/messages$/.test(new URL(request.url()).pathname))
    await page.getByTestId('composer-live-voice-toggle').click()
    await expect(page.getByTestId('live-voice-stage')).toBeVisible()

    const request = await sent
    expect(JSON.stringify(request.postDataJSON())).toContain('Oi Orin, tudo bem?')
    expect(received.binaryFrames).toBeGreaterThan(5)
    expect(received.csrf).toBe('csrf-e2e-orin-friend-live')
    expect(received.configs.at(-1)).toMatchObject({ stt_provider: 'local_sidecar', stt_model: 'faster-whisper-large-v3-turbo' })
    await expect(page.getByTestId('live-voice-captions')).toContainText('Oi Orin, tudo bem?')
  })

  test('fala a resposta com a voz escolhida do modelo (OpenRouter · ElevenLabs v4) e mostra a legenda', async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('orin.live-voice.prefs.v1', JSON.stringify({
        tts: 'openrouter',
        ttsModels: { openrouter: 'elevenlabs/eleven-v4' },
        ttsVoices: { 'openrouter:elevenlabs/eleven-v4': 'bella' },
        endSilenceMs: 400,
      }))
    })
    await page.route('**/v1/live-voice/capabilities', (route) => fulfillJson(route, {
      stt: {
        local_sidecar: { available: true, device: 'cuda', model_id: 'faster-whisper-large-v3-turbo', reason: '' },
        openai: { available: true, model_id: 'gpt-4o-mini-transcribe', reason: '' },
        groq: { available: false, model_id: 'whisper-large-v3-turbo', reason: '' },
      },
      tts: {
        openai: { available: false, models: [], voices: [], reason: '' },
        openrouter: {
          available: true,
          model_id: 'elevenlabs/eleven-v3',
          reason: '',
          models: [
            { id: 'elevenlabs/eleven-v3', name: 'ElevenLabs: Eleven v3', vendor: 'elevenlabs', voices: ['adam'] },
            { id: 'elevenlabs/eleven-v4', name: 'ElevenLabs: Eleven v4', vendor: 'elevenlabs', voices: ['sarah', 'bella'] },
          ],
        },
        kokoro: { available: false, voices: [], models: [], reason: '' },
        chatterbox: { available: false, voices: [], models: [], reason: '' },
      },
      recommended_stt: 'local_sidecar',
      recommended_tts: 'openrouter',
      hardware: { has_cuda: true, device_name: 'NVIDIA GeForce RTX 4060', free_vram_gb: 6, total_vram_gb: 8 },
    }))
    let replied = false
    let speakDelivered = false
    await page.route('**/v1/conversations/*/messages', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      replied = true
      await fulfillJson(route, { conversation_id: FRIEND_CONVERSATION_ID, turn_id: 'turn-friend-2', message_id: 'msg-friend-u2', state: 'queued' }, 202)
    })
    await page.route('**/v1/conversations/*/events*', async (route) => {
      if (replied && !speakDelivered) {
        speakDelivered = true
        const event = {
          event_id: 'ev-speak-1', turn_id: 'turn-friend-2', agent_id: 'agent:main', event_type: 'speak.chunk_ready',
          summary: 'Tudo ótimo por aqui!', cursor: '2',
          payload: { tts_text: 'Tudo ótimo por aqui!', clean_text: 'Tudo ótimo por aqui' },
        }
        await route.fulfill({ status: 200, contentType: 'text/event-stream', body: `id: 2\ndata: ${JSON.stringify(event)}\n\n` })
        return
      }
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'event: heartbeat\ndata: {"cursor":"1"}\n\n' })
    })

    const spoken: Array<Record<string, unknown>> = []
    const configs: Array<Record<string, unknown>> = []
    await page.routeWebSocket(/\/v1\/conversations\/[^/]+\/live/, (ws) => {
      ws.send(JSON.stringify({ type: 'session.ready', conversation_id: FRIEND_CONVERSATION_ID, audio_epoch: 1 }))
      let transcribed = false
      ws.onMessage((message) => {
        if (typeof message !== 'string') return
        const event = JSON.parse(message) as Record<string, unknown>
        if (event.type === 'audio_speech_end' && !transcribed) {
          transcribed = true
          ws.send(JSON.stringify({ type: 'stt.transcript', is_end_of_utterance: true, text: 'Como você está?', audio_epoch: 1 }))
        }
        if (event.type === 'config') configs.push(event)
        if (event.type === 'speak_text') {
          spoken.push(event)
          // A cloud voice answers with an encoded file the browser decodes itself:
          // 1.2 s of a soft 220 Hz tone as a 24 kHz WAV stands in for it.
          const samples = new Int16Array(24000 * 1.2)
          for (let i = 0; i < samples.length; i += 1) samples[i] = Math.round(Math.sin((2 * Math.PI * 220 * i) / 24000) * 6000)
          const header = Buffer.alloc(44)
          header.write('RIFF', 0); header.writeUInt32LE(36 + samples.byteLength, 4); header.write('WAVE', 8)
          header.write('fmt ', 12); header.writeUInt32LE(16, 16); header.writeUInt16LE(1, 20); header.writeUInt16LE(1, 22)
          header.writeUInt32LE(24000, 24); header.writeUInt32LE(48000, 28); header.writeUInt16LE(2, 32); header.writeUInt16LE(16, 34)
          header.write('data', 36); header.writeUInt32LE(samples.byteLength, 40)
          const wav = Buffer.concat([header, Buffer.from(samples.buffer)]).toString('base64')
          ws.send(JSON.stringify({
            type: 'tts.chunk', epoch: 1, audio_epoch: 1, chunk_seq: 0, clean_text: String(event.text), encoding: 'wav', audio_base64: wav,
            mime: 'audio/wav', sample_rate: 0, duration_ms: 1200, word_timings: [], provider: 'openrouter',
          }))
        }
      })
    })

    await page.goto('/')
    await page.getByTestId('sidebar-orin-friend-button').click()
    await page.getByTestId('composer-live-voice-toggle').click()
    await expect(page.getByTestId('live-voice-active-profile-pill')).toContainText('OpenRouter · Bella')

    await expect.poll(() => spoken.length, { timeout: 20_000 }).toBeGreaterThan(0)
    expect(spoken[0]).toMatchObject({ type: 'speak_text', text: 'Tudo ótimo por aqui!' })
    expect(configs.at(-1)).toMatchObject({ tts_provider: 'openrouter', tts_model: 'elevenlabs/eleven-v4', voice_id: 'bella' })
    await expect(page.getByTestId('live-voice-stage')).toHaveAttribute('data-voice-state', 'speaking')
    await expect(page.getByTestId('live-voice-assistant-caption')).toContainText('Tudo ótimo por aqui!')
    await page.screenshot({ path: test.info().outputPath('live-speaking.png') })
  })
})
