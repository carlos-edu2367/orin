import { expect, test, type Page, type Route } from '@playwright/test'

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
  await page.route('**/providers', async (route) => {
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

async function openProvider(page: Page, name: string) {
  await page.goto('/settings/providers')
  const card = page.getByRole('link', { name: new RegExp(name) })
  await expect(card).toBeVisible()
  await card.click()
  const panel = page.getByRole('region', { name, exact: true })
  await expect(panel).toBeVisible()
  return panel
}
