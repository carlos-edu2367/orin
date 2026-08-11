import { expect, test, type Page, type Route } from '@playwright/test'

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installCsrfMeta(page: Page): Promise<void> {
  await page.route('**/', async (route) => {
    const response = await route.fetch()
    const body = (await response.text()).replace('<head>', '<head><meta name="csrf-token" content="csrf-reduced-motion-test">')
    await route.fulfill({ response, body })
  })
}

// This spec navigates to the static `/execution/fixture-collaborating` route (no
// network fake needed: it renders `ExecutionPage` with a fixture execution and a
// single locally-assumed `DelegationCreated` fixture event — see fixtures.ts and
// IMPLEMENTATION_PLAN.md, Fase 6 "Decisões locais"). This is the only real-browser,
// deterministic way to exercise `AgentRail`/`OrchestrationScene` with an observed
// edge today: `ExecutionRoute` never populates `ExecutionPage`'s `events` prop from
// a live binding (Fase 3 decision), and no production adapter delivers delegation
// events to the public stream yet (`BACKEND_DISCOVERY.md`).

test('under reduced motion, the rail and scene never mount a canvas or a pulse, and show the same state as text', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/execution/fixture-collaborating')

  const rail = page.getByRole('region', { name: 'Colaboração entre agents observada' })
  await expect(rail).toBeVisible()
  await expect(page.locator('.agent-glyph__core--pulse')).toHaveCount(0)

  const orbitGlyph = rail.getByRole('button', { name: /agent-orbit/ })
  await orbitGlyph.click()
  await expect(page.getByText('Delegação observada', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Expandir grafo' }).click()
  const scene = page.getByLabel('Cena 3D da orquestração observada')
  await expect(scene).toBeVisible()
  await expect(scene.locator('canvas')).toHaveCount(0)
  await expect(page.locator('.agent-glyph__core--pulse')).toHaveCount(0)

  // Same facts, as text: participant list and the observed delegation fact.
  await expect(scene.getByText('agent-orbit', { exact: true })).toBeVisible()
  await expect(scene.getByText('agent-cartographer', { exact: true })).toBeVisible()
  await expect(scene.getByText(/agent-orbit → agent-cartographer/)).toBeVisible()
})

test('without reduced motion, the same collaboration graph mounts a real WebGL canvas for the scene', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.goto('/execution/fixture-collaborating')

  await page.getByRole('button', { name: 'Expandir grafo' }).click()
  const scene = page.getByLabel('Cena 3D da orquestração observada')
  await expect(scene).toBeVisible()
  await expect(scene.locator('canvas')).toHaveCount(1, { timeout: 10000 })
})

test('under reduced motion, Home submission has no canvas or repeating pulse and navigates to the chat', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await installCsrfMeta(page)
  await page.route('**/v1/providers/openrouter/models', (route) => fulfillJson(route, {
    items: [{
      provider: 'openrouter', model_id: 'openrouter/model-reduced', display_name: 'Modelo reduced motion', context_window: 128000,
      capabilities: [], input_modalities: ['text'], output_modalities: ['text'], pricing: null, is_favorite: false, refreshed_at: null,
    }],
  }))

  let pendingConversation: Route | undefined
  await page.route('**/v1/conversations', (route) => {
    pendingConversation = route
  })

  await page.goto('/')
  await page.getByRole('textbox', { name: 'Mensagem' }).fill('Continue sem animação')
  await page.getByRole('button', { name: 'Enviar mensagem' }).click()

  const submitting = page.getByTestId('home-submit-state')
  await expect(submitting).toHaveAttribute('data-submitting', 'true')
  await expect(page).toHaveURL('/')
  await expect(page.locator('canvas')).toHaveCount(0)
  // The WebGL field is not merely paused under reduced motion: it is never
  // created, so there is no canvas and no render loop at all. The remaining
  // surface must also hold completely still.
  const composer = page.locator('.composer__surface')
  await expect(composer).toBeVisible()
  const settle = async () => composer.evaluate((element) => {
    const style = getComputedStyle(element)
    return { animationName: style.animationName, opacity: style.opacity }
  })
  const before = await settle()
  await page.waitForTimeout(350)
  expect(await settle()).toEqual(before)
  expect(before.animationName).toBe('none')

  expect(pendingConversation).toBeDefined()
  await fulfillJson(pendingConversation!, {
    conversation_id: 'conv-reduced', title: 'Continue sem animação', turn_id: 'turn-reduced', message_id: 'message-reduced', state: 'queued',
  }, 201)
  await expect(page).toHaveURL('/chats/conv-reduced')
})
