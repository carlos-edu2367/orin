import { expect, test, type Page, type Route } from '@playwright/test'
import { env as processEnv } from 'node:process'

// This fake is deliberately test-only and is not a production contract. It simulates
// only the public responses documented in BACKEND_DISCOVERY.md/BACKEND_CAPABILITY_MATRIX.md:
//   - GET    /v1/providers/{provider}  -> the provider's current public state. The
//     gateway (`_provider_public` in `src/agentos/api/gateway.py`) strips any field
//     whose name contains api_key/secret/token/password/credential before this ever
//     leaves the server; only `provider`/`enabled` are documented as a safe shape.
//   - PUT    /v1/providers/{provider}  -> configure (api_key/enabled?), returns
//     the same filtered public state. The key itself is never echoed back.
//   - DELETE /v1/providers/{provider}  -> revoke, returns the same filtered public state.
//   - the sanitized error envelope { error: { code, category, message_key,
//     correlation_id, retryable, retry_after } } already used by every other route.
// No other endpoint, field or provider is simulated.

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

function errorBody(code: string, category: string, retryable: boolean, retryAfter: number | null = null) {
  return {
    error: { code, category, message_key: code, correlation_id: `corr-${code}`, retryable, retry_after: retryAfter },
  }
}

function providerFromUrl(url: string): string {
  return new URL(url).pathname.split('/').pop() ?? ''
}

test.beforeEach(async ({ page }) => {
  await page.route(/\/(providers|media)$/, async (route) => {
    const response = await route.fetch()
    const body = (await response.text()).replace('<head>', '<head><meta name="csrf-token" content="csrf-e2e-test">')
    await route.fulfill({ response, body })
  })
})

test('saves a new API key without asking for a model; the key clears and never returns', async ({ page }) => {
  const putBodies: Array<Record<string, unknown>> = []
  const csrfTokens: string[] = []

  await page.route('**/v1/providers/**', async (route) => {
    const request = route.request()
    const provider = providerFromUrl(request.url())

    if (request.method() === 'GET') {
      await fulfillJson(route, {})
      return
    }
    if (request.method() === 'PUT' && provider === 'openai') {
      putBodies.push(request.postDataJSON() as Record<string, unknown>)
      csrfTokens.push(request.headers()['x-csrf-token'] ?? '')
      await fulfillJson(route, { provider: 'openai', enabled: true, secret_ref: 'secret-openai', catalog_refreshed_at: null })
      return
    }
    await route.abort('failed')
  })

  const panel = await openProvider(page, 'OpenAI')
  await expect(panel.locator('.provider-detail__state')).toHaveText('Não configurado')

  await panel.getByLabel('Chave de API').fill('sk-test-secret-value')
  await expect(panel.getByLabel('Modelo')).toHaveCount(0)
  await panel.getByRole('button', { name: 'Salvar', exact: true }).click()

  await expect(panel.locator('.provider-detail__state')).toHaveText('Habilitado')
  await expect(panel.getByLabel('Chave de API')).toHaveValue('')
  expect(putBodies).toHaveLength(1)
  expect(putBodies[0]?.api_key).toBe('sk-test-secret-value')
  expect(putBodies[0]?.model).toBeUndefined()
  expect(csrfTokens).toEqual(['csrf-e2e-test'])

  // A fresh snapshot after reload only ever comes from GET, which the fake above
  // never echoes a key back on; the field must stay empty across that reload too.
  await page.reload()
  await expect(panel.getByLabel('Chave de API')).toHaveValue('')
  await expect(page.getByText('sk-test-secret-value')).toHaveCount(0)
})

test('revokes a configured provider and reflects the resulting public state', async ({ page }) => {
  let deleteCalled = false

  await page.route('**/v1/providers/**', async (route) => {
    const request = route.request()
    const provider = providerFromUrl(request.url())

    if (request.method() === 'GET' && provider === 'anthropic') {
      await fulfillJson(route, deleteCalled ? { provider: 'anthropic', enabled: false } : { provider: 'anthropic', enabled: true })
      return
    }
    if (request.method() === 'GET') {
      await fulfillJson(route, { provider, enabled: false })
      return
    }
    if (request.method() === 'DELETE' && provider === 'anthropic') {
      deleteCalled = true
      await fulfillJson(route, { provider: 'anthropic', enabled: false })
      return
    }
    await route.abort('failed')
  })

  const panel = await openProvider(page, 'Anthropic')
  await expect(panel.locator('.provider-detail__state')).toHaveText('Habilitado')

  page.once('dialog', (dialog) => void dialog.accept())
  await panel.getByRole('button', { name: 'Revogar acesso' }).click()

  await expect(panel.locator('.provider-detail__state')).toHaveText('Desabilitado')
  expect(deleteCalled).toBe(true)
  await expect(panel.getByRole('button', { name: 'Revogar acesso' })).toBeDisabled()
})

test('shows only the current public state for every provider and never renders a secret-shaped value even if one leaked into a response', async ({ page }) => {
  await page.route('**/v1/providers/**', async (route) => {
    const request = route.request()
    const provider = providerFromUrl(request.url())
    if (request.method() !== 'GET') {
      await route.abort('failed')
      return
    }
    if (provider === 'openai') {
      // A defensive scenario: even if a leaked-secret-shaped field slipped past the
      // server's own filter, the client must still never render its value.
      await fulfillJson(route, { provider: 'openai', enabled: true, api_key_hint: 'sk-leaked-value' })
      return
    }
    if (provider === 'anthropic') {
      await fulfillJson(route, { provider: 'anthropic', enabled: false })
      return
    }
    await fulfillJson(route, { provider: 'openrouter', enabled: false })
  })

  await page.goto('/settings/providers')

  await expect(page.getByRole('link', { name: /OpenAI/ })).toContainText('Configurado')
  await expect(page.getByRole('link', { name: /Anthropic/ })).toContainText('Desabilitado')
  await expect(page.getByRole('link', { name: /OpenRouter/ })).toContainText('Desabilitado')
  await expect(page.getByText('sk-leaked-value')).toHaveCount(0)
  await expect(page.getByText('api_key_hint')).toHaveCount(0)
})

test('recovers from a CSRF-rejected save and a rate limit, reusing the same Idempotency-Key across retries, before a final attempt succeeds', async ({ page }) => {
  let attempt = 0
  const idempotencyKeys: string[] = []

  await page.route('**/v1/providers/**', async (route) => {
    const request = route.request()
    const provider = providerFromUrl(request.url())

    if (request.method() === 'GET') {
      await fulfillJson(route, {})
      return
    }
    if (request.method() === 'PUT' && provider === 'openai') {
      idempotencyKeys.push(request.headers()['idempotency-key'] ?? '')
      attempt += 1
      if (attempt === 1) {
        // CSRF/origin validation failure maps to AUTHORIZATION (403) in the gateway.
        await fulfillJson(route, errorBody('access_denied', 'AUTHORIZATION', false), 403)
      } else if (attempt === 2) {
        await fulfillJson(route, errorBody('rate_limited', 'RATE_LIMITED', true, 30), 429)
      } else {
        await fulfillJson(route, { provider: 'openai', enabled: true })
      }
      return
    }
    await route.abort('failed')
  })

  const panel = await openProvider(page, 'OpenAI')

  await panel.getByLabel('Chave de API').fill('sk-attempt-one')
  await panel.getByRole('button', { name: 'Salvar', exact: true }).click()
  await expect(panel.getByText('Atualize a página para renovar sua sessão.')).toBeVisible()
  await expect(panel.getByLabel('Chave de API')).toHaveValue('sk-attempt-one')

  await panel.getByLabel('Chave de API').fill('sk-attempt-two')
  await panel.getByRole('button', { name: 'Salvar', exact: true }).click()
  await expect(panel.getByText('Muitas tentativas; aguarde antes de tentar novamente.')).toBeVisible()
  await expect(panel.getByText('Tente novamente em 30s.')).toBeVisible()
  await expect(panel.getByLabel('Chave de API')).toHaveValue('sk-attempt-two')

  await panel.getByLabel('Chave de API').fill('sk-attempt-three')
  await panel.getByRole('button', { name: 'Salvar', exact: true }).click()
  await expect(panel.locator('.provider-detail__state')).toHaveText('Habilitado')

  expect(idempotencyKeys).toHaveLength(3)
  expect(idempotencyKeys[0]).not.toBe('')
  expect(new Set(idempotencyKeys).size).toBe(1)
})

test('configures OpenCode Local Bridge without an API key, tests connection, and loads free models', async ({ page }) => {
  let configured = false
  const putBodies: Array<Record<string, unknown>> = []

  await page.route('**/v1/providers/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const pathname = url.pathname

    if (request.method() === 'POST' && pathname === '/v1/providers/opencode/test') {
      await fulfillJson(route, { connected: true, models_available: 10, base_url: 'http://127.0.0.1:18446' })
      return
    }
    if (request.method() === 'PUT' && pathname === '/v1/providers/opencode') {
      configured = true
      putBodies.push(request.postDataJSON() as Record<string, unknown>)
      await fulfillJson(route, { provider: 'opencode', enabled: true, base_url: 'http://127.0.0.1:18446' })
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/providers/opencode/models:refresh') {
      await fulfillJson(route, { count: 2, refreshed_at: '2026-10-20T12:00:00Z' })
      return
    }
    if (request.method() === 'GET' && pathname === '/v1/providers/opencode/models') {
      await fulfillJson(route, {
        items: [
          {
            provider: 'opencode',
            model_id: 'big-pickle',
            display_name: 'Big Pickle',
            context_window: 200000,
            capabilities: ['tools', 'reasoning'],
            input_modalities: ['text'],
            output_modalities: ['text'],
            pricing: null,
            is_favorite: false,
            is_custom: false,
            refreshed_at: '2026-10-20T12:00:00Z',
            route_kind: 'model',
          },
          {
            provider: 'opencode',
            model_id: 'mimo-v2.6-flash-free',
            display_name: 'MiMo v2.6 Flash Free',
            context_window: 262144,
            capabilities: ['tools', 'reasoning'],
            input_modalities: ['text', 'image'],
            output_modalities: ['text'],
            pricing: null,
            is_favorite: false,
            is_custom: false,
            refreshed_at: '2026-10-20T12:00:00Z',
            route_kind: 'model',
          },
        ],
      })
      return
    }
    if (request.method() === 'GET' && pathname.endsWith('/keys')) {
      await fulfillJson(route, [])
      return
    }
    if (request.method() === 'GET' && pathname === '/v1/providers/opencode') {
      await fulfillJson(
        route,
        configured
          ? { provider: 'opencode', enabled: true, base_url: 'http://127.0.0.1:18446' }
          : { provider: 'opencode', enabled: null, base_url: 'http://127.0.0.1:18446' },
      )
      return
    }
    if (request.method() === 'GET') {
      await fulfillJson(route, {})
      return
    }
    await route.abort('failed')
  })

  const panel = await openProvider(page, 'OpenCode')
  await expect(panel.getByRole('radio', { name: 'Bridge Local (Grátis)' })).toHaveAttribute('aria-checked', 'true')
  await expect(panel.getByLabel('URL do servidor')).toHaveValue('http://127.0.0.1:18446')

  await panel.getByRole('button', { name: 'Testar conexão' }).click()
  await expect(panel.getByText(/10 modelos disponíveis/)).toBeVisible()

  await panel.getByRole('button', { name: 'Salvar', exact: true }).click()
  await expect(panel.locator('.provider-detail__state')).toHaveText('Habilitado')
  expect(putBodies).toEqual([
    {
      api_key: '',
      enabled: true,
      base_url: 'http://127.0.0.1:18446',
    },
  ])


  await expect(panel.getByText('Big Pickle')).toBeVisible()
  await expect(panel.getByText('MiMo v2.6 Flash Free')).toBeVisible()
  if (processEnv.ORIN_SHOT_OPENCODE_BRIDGE) {
    await panel.getByRole('radio', { name: 'Bridge Local (Grátis)' }).scrollIntoViewIfNeeded()
    await page.screenshot({ path: processEnv.ORIN_SHOT_OPENCODE_BRIDGE, fullPage: false })
  }
})

async function openProvider(page: Page, name: string) {
  await page.goto('/settings/providers')
  const card = page.getByRole('link', { name: new RegExp(name) })
  await expect(card).toBeVisible()
  await card.click()
  const panel = page.getByRole('region', { name, exact: true })
  await expect(panel).toBeVisible()
  return panel
}


test('configures NVIDIA NIM Cloud & Self-Hosted NIM, displays exhausted/cooldown keys, and reactivates an exhausted key', async ({ page }) => {
  let key1Status: 'exhausted' | 'active' = 'exhausted'
  const activatedIds: number[] = []

  await page.route('**/v1/providers/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const pathname = url.pathname

    if (request.method() === 'POST' && pathname === '/v1/providers/nvidia/test') {
      await fulfillJson(route, { reachable: true, authenticated: true, model_count: 42 })
      return
    }
    if (request.method() === 'POST' && pathname === '/v1/providers/nvidia/keys/1:activate') {
      key1Status = 'active'
      activatedIds.push(1)
      await fulfillJson(route, {
        id: 1,
        provider: 'nvidia',
        label: 'Conta Free 1 (build.nvidia.com)',
        position: 0,
        status: 'active',
        cooldown_until: null,
      })
      return
    }
    if (request.method() === 'GET' && pathname === '/v1/providers/nvidia/keys') {
      await fulfillJson(route, [
        {
          id: 1,
          provider: 'nvidia',
          label: 'Conta Free 1 (build.nvidia.com)',
          position: 0,
          status: key1Status,
          cooldown_until: null,
        },
        {
          id: 2,
          provider: 'nvidia',
          label: 'Conta Free 2 (Rate Limited)',
          position: 1,
          status: 'cooldown',
          cooldown_until: '2026-10-26T04:30:00Z',
        },
        {
          id: 3,
          provider: 'nvidia',
          label: 'Conta Paga Produção',
          position: 2,
          status: 'active',
          cooldown_until: null,
        },
      ])
      return
    }
    if (request.method() === 'GET' && pathname === '/v1/providers/nvidia/models') {
      await fulfillJson(route, {
        items: [
          {
            provider: 'nvidia',
            model_id: 'deepseek-ai/deepseek-r1',
            display_name: 'DeepSeek R1 (NVIDIA NIM)',
            context_window: 131072,
            capabilities: ['tools', 'reasoning'],
            input_modalities: ['text'],
            output_modalities: ['text'],
            pricing: { input_per_million_usd: 0, output_per_million_usd: 0 },
            is_favorite: true,
            is_custom: false,
            refreshed_at: '2026-10-25T23:00:00Z',
            route_kind: 'model',
          },
          {
            provider: 'nvidia',
            model_id: 'meta/llama-3.2-90b-vision-instruct',
            display_name: 'Llama 3.2 90B Vision Instruct',
            context_window: 131072,
            capabilities: ['tools'],
            input_modalities: ['text', 'image'],
            output_modalities: ['text'],
            pricing: null,
            is_favorite: false,
            is_custom: false,
            refreshed_at: '2026-10-25T23:00:00Z',
            route_kind: 'model',
          },
        ],
      })
      return
    }
    if (request.method() === 'GET' && pathname === '/v1/providers/nvidia') {
      await fulfillJson(route, {
        provider: 'nvidia',
        enabled: true,
        has_secret: true,
        base_url: 'https://integrate.api.nvidia.com/v1',
        key_cooldown_seconds: 60,
        catalog_refreshed_at: '2026-10-25T23:00:00Z',
      })
      return
    }
    if (request.method() === 'GET' && pathname.endsWith('/models')) {
      await fulfillJson(route, { items: [] })
      return
    }
    if (request.method() === 'GET' && pathname.endsWith('/keys')) {
      await fulfillJson(route, [])
      return
    }
    if (request.method() === 'GET') {
      await fulfillJson(route, {})
      return
    }
    await route.abort('failed')
  })

  await page.setViewportSize({ width: 1440, height: 900 })
  const panel = await openProvider(page, 'NVIDIA NIM')
  await expect(panel.getByRole('radio', { name: 'NVIDIA NIM Cloud' })).toHaveAttribute('aria-checked', 'true')
  await expect(panel.getByLabel('URL do servidor')).toHaveValue('https://integrate.api.nvidia.com/v1')

  await panel.getByRole('button', { name: 'Testar conexão' }).click()
  await expect(panel.getByText(/Endpoint acessível · Autenticado · 42 modelos disponíveis/)).toBeVisible()

  await expect(panel.getByText('Sem saldo / Esgotada')).toBeVisible()
  await expect(panel.getByText(/Em cooldown até/)).toBeVisible()

  if (processEnv.ORIN_SHOT_NVIDIA_DESKTOP) {
    await page.screenshot({ path: processEnv.ORIN_SHOT_NVIDIA_DESKTOP, fullPage: true })
  }

  await panel.getByRole('button', { name: 'Reativar' }).first().click()
  expect(activatedIds).toEqual([1])
  await expect(panel.getByText('Sem saldo / Esgotada')).toHaveCount(0)

  await panel.getByRole('radio', { name: 'Self-Hosted NIM' }).click()
  await expect(panel.getByLabel('URL do servidor')).toHaveValue('http://localhost:8000/v1')

  await page.setViewportSize({ width: 390, height: 844 })
  if (processEnv.ORIN_SHOT_NVIDIA_MOBILE) {
    await page.screenshot({ path: processEnv.ORIN_SHOT_NVIDIA_MOBILE, fullPage: true })
  }
})

test('configures cross-provider default models for image, audio, and video generation', async ({ page }) => {
  const savedMediaModels: Record<string, { provider: string | null; model_id: string | null; mode: 'automatic' | 'manual' }> = {
    image: { provider: null, model_id: null, mode: 'automatic' },
    audio: { provider: null, model_id: null, mode: 'automatic' },
    video: { provider: null, model_id: null, mode: 'automatic' },
  }

  await page.route('**/v1/settings/media-models**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const pathname = url.pathname
    if (request.method() === 'GET' && pathname === '/v1/settings/media-models') {
      await fulfillJson(route, savedMediaModels)
      return
    }
    const putMatch = /^\/v1\/settings\/media-models\/(image|audio|video)$/.exec(pathname)
    if (request.method() === 'PUT' && putMatch) {
      const mod = putMatch[1]
      const body = request.postDataJSON() as { provider: string | null; model_id: string | null }
      const next = {
        provider: body.provider,
        model_id: body.model_id,
        mode: body.provider === null ? ('automatic' as const) : ('manual' as const),
      }
      savedMediaModels[mod] = next
      await fulfillJson(route, { modality: mod, ...next })
      return
    }
    await route.abort('failed')
  })

  await page.route('**/v1/providers/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    if (request.method() === 'GET' && pathname === '/v1/providers/openrouter/models') {
      await fulfillJson(route, {
        items: [
          {
            provider: 'openrouter',
            model_id: 'qwen/qwen-image-2.1-pro',
            display_name: 'Qwen Image 2.1 Pro',
            context_window: null,
            capabilities: [],
            input_modalities: ['text'],
            output_modalities: ['image'],
            pricing: null,
            is_favorite: false,
            is_custom: false,
            refreshed_at: '2026-10-27T12:00:00Z',
            route_kind: 'model',
          },
          {
            provider: 'openrouter',
            model_id: 'elevenlabs/eleven-v3',
            display_name: 'ElevenLabs: Eleven v3',
            context_window: null,
            capabilities: [],
            input_modalities: ['text'],
            output_modalities: ['audio'],
            pricing: null,
            is_favorite: true,
            is_custom: false,
            refreshed_at: '2026-10-27T12:00:00Z',
            route_kind: 'model',
          },
        ],
      })
      return
    }
    if (request.method() === 'GET' && pathname.endsWith('/models')) {
      await fulfillJson(route, { items: [] })
      return
    }
    await fulfillJson(route, {})
  })

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/settings/media')

  await expect(page.getByRole('heading', { name: 'Geração de mídia', level: 2 })).toBeVisible()

  const imageRow = page.getByTestId('media-model-row-image')
  await imageRow.getByLabel('Filtrar modelos de imagem').fill('qwen 2.1')
  await imageRow.getByLabel('Modelo padrão de imagem').selectOption('openrouter:qwen/qwen-image-2.1-pro')
  await expect(imageRow.locator('.media-models-setting__badge')).toHaveText('OpenRouter · qwen/qwen-image-2.1-pro')

  const audioRow = page.getByTestId('media-model-row-audio')
  await audioRow.getByLabel('Modelo padrão de áudio').selectOption('openrouter:elevenlabs/eleven-v3')
  await expect(audioRow.locator('.media-models-setting__badge')).toHaveText('OpenRouter · elevenlabs/eleven-v3')
})
