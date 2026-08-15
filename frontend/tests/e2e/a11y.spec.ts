import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Route } from '@playwright/test'

// These fakes are deliberately test-only, not production contracts. Home receives
// an authorized catalog plus a controlled failed submission; Provider Settings
// receives only the sanitized public GET state. Execution scans use static fixture
// routes from fixtures.ts.
async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installCsrfMeta(page: Page): Promise<void> {
  await page.route('**/', async (route) => {
    const response = await route.fetch()
    const body = (await response.text()).replace('<head>', '<head><meta name="csrf-token" content="csrf-a11y-test">')
    await route.fulfill({ response, body })
  })
}

async function assertNoCriticalOrSeriousViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze()
  const blocking = results.violations.filter((violation) => violation.impact === 'critical' || violation.impact === 'serious')
  expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([])
}

test('Home has no critical or serious accessibility violations', async ({ page }) => {
  await page.goto('/')
  await assertNoCriticalOrSeriousViolations(page)
})

test('Home announces a conversation error and restores focus to the message', async ({ page }) => {
  await installCsrfMeta(page)
  await page.route('**/v1/providers/openrouter/models', (route) => fulfillJson(route, {
    items: [{
      provider: 'openrouter', model_id: 'openrouter/model-a11y', display_name: 'Modelo acessível', context_window: 128000,
      capabilities: [], input_modalities: ['text'], output_modalities: ['text'], pricing: null, is_favorite: false, refreshed_at: null,
    }],
  }))
  await page.route('**/v1/conversations', (route) => fulfillJson(route, {
    error: {
      code: 'provider_unavailable', category: 'INTERNAL', message_key: 'provider_unavailable',
      correlation_id: 'corr-a11y', retryable: true, retry_after: null,
    },
  }, 503))

  await page.goto('/')
  const message = page.getByRole('textbox', { name: 'Mensagem' })
  await message.fill('Explique o estado do sistema')
  await page.getByRole('button', { name: 'Enviar mensagem' }).click()

  await expect(page.getByRole('alert').filter({ hasText: 'backend não conseguiu iniciar a conversa' })).toBeVisible()
  await expect(message).toBeFocused()
})

test('Execution (running) has no critical or serious accessibility violations', async ({ page }) => {
  await page.goto('/execution/fixture-running')
  await assertNoCriticalOrSeriousViolations(page)
})

test('Execution with the collaboration rail expanded into the 3D scene has no critical or serious accessibility violations', async ({ page }) => {
  await page.goto('/execution/fixture-collaborating')
  await page.getByRole('button', { name: 'Expandir grafo' }).click()
  await expect(page.getByLabel('Cena 3D da orquestração observada')).toBeVisible()
  await assertNoCriticalOrSeriousViolations(page)
})

test('An open Disclosure (agent rail technical details) has no critical or serious accessibility violations', async ({ page }) => {
  await page.goto('/execution/fixture-collaborating')
  await page.getByRole('button', { name: 'Detalhes técnicos da colaboração' }).click()
  await assertNoCriticalOrSeriousViolations(page)
})

test('An open Inspector has no critical or serious accessibility violations', async ({ page }) => {
  await page.goto('/execution/fixture-running')
  await page.getByRole('button', { name: 'Abrir inspector' }).click()
  await assertNoCriticalOrSeriousViolations(page)
})

test('Provider Settings has no critical or serious accessibility violations', async ({ page }) => {
  await installCsrfMeta(page)
  await page.route('**/v1/providers/**', async (route) => {
    if (route.request().method() === 'GET') {
      await fulfillJson(route, {})
      return
    }
    await route.abort('failed')
  })
  await page.goto('/settings/providers')
  const card = page.getByRole('link', { name: /OpenRouter/ })
  await expect(card).toContainText('Não configurado')
  const results = await new AxeBuilder({ page }).include('.provider-grid').analyze()
  const blocking = results.violations.filter((violation) => violation.impact === 'critical' || violation.impact === 'serious')
  expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([])
})
