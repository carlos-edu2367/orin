import { expect, test } from '@playwright/test'

// Execution screens use static fixture routes (fixtures.ts); Home receives one
// authorized catalog response so its baseline represents the intended rest state.
// Screenshots are captured under `prefers-reduced-motion: reduce` to keep entrance/
// pulse timing out of the comparison; this is orthogonal to, and does not replace,
// the dedicated behavioral coverage in reduced-motion.spec.ts.
// Run with `npx playwright test --config=playwright.visual.config.ts --update-snapshots`
// to (re)generate baselines after a deliberate visual change.

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
})

test('home', async ({ page }) => {
  await page.route('**/', async (route) => {
    const response = await route.fetch()
    const body = (await response.text()).replace('<head>', '<head><meta name="csrf-token" content="csrf-visual-test">')
    await route.fulfill({ response, body })
  })
  await page.route('**/v1/providers/openrouter/models', (route) => route.fulfill({
    json: { items: [{
      provider: 'openrouter', model_id: 'openrouter/modelo-visual', display_name: 'Modelo visual', context_window: 128000,
      capabilities: [], input_modalities: ['text'], output_modalities: ['text'], pricing: null, is_favorite: false, refreshed_at: null,
    }] },
  }))
  await page.route('**/v1/conversations', (route) => route.fulfill({ json: { items: [] } }))
  await page.goto('/')
  // The model chip only shows a name once the catalog resolves, which is the
  // rest state this baseline is meant to capture.
  await expect(page.locator('.model-picker .chip').nth(1)).toContainText('Modelo visual')
  await expect(page).toHaveScreenshot('home.png')
})

test('execution running', async ({ page }) => {
  await page.goto('/execution/fixture-running')
  await expect(page).toHaveScreenshot('execution-running.png')
})

test('activity expanded', async ({ page }) => {
  await page.goto('/execution/fixture-collaborating')
  await page.getByRole('button', { name: /Ferramenta/ }).click()
  await expect(page.getByText('Concluída · search')).toBeVisible()
  await expect(page).toHaveScreenshot('activity-expanded.png')
})

test('orchestration expanded', async ({ page }) => {
  await page.goto('/execution/fixture-collaborating')
  await page.getByRole('button', { name: 'Expandir grafo' }).click()
  await expect(page.getByLabel('Cena 3D da orquestração observada')).toBeVisible()
  await expect(page).toHaveScreenshot('orchestration-expanded.png')
})
