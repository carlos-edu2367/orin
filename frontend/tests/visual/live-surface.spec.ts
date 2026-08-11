import { expect, test } from '@playwright/test'

/**
 * Visual smoke against a running local stack.
 *
 * These do not assert pixels; they drive the real surface and capture it, which
 * is how layout regressions on long content and dense activity get caught. Run
 * with `npm run test:visual` while the API is up.
 */

// The API serves the built client on the same origin, so the visual smoke points
// at it directly instead of at the Vite dev server.
const BASE = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env.AGENTOS_BASE_URL ?? 'http://127.0.0.1:8000'

test.describe('live surface', () => {
  test('home presents a single composer over the ambient field', async ({ page }) => {
    await page.goto(BASE)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await expect(page.getByRole('textbox', { name: 'Mensagem' })).toBeVisible()
    await page.screenshot({ path: 'tests/visual/output/home.png', fullPage: true })
  })

  test('command palette opens over the home', async ({ page }) => {
    await page.goto(BASE)
    await page.keyboard.press('Control+k')
    await expect(page.getByRole('dialog', { name: 'Navegação' })).toBeVisible()
    await page.screenshot({ path: 'tests/visual/output/palette.png' })
  })

  test('model picker lists the authorized catalog', async ({ page }) => {
    await page.goto(BASE)
    const modelChip = page.locator('.model-picker .chip').nth(1)
    await modelChip.click()
    await expect(page.getByRole('listbox', { name: 'Modelos disponíveis' })).toBeVisible()
    await page.screenshot({ path: 'tests/visual/output/model-picker.png' })
  })

  test('an existing conversation reopens with its activity and overview', async ({ page }) => {
    await page.goto(BASE)
    const recent = page.locator('.home__recent-item').first()
    await expect(recent).toBeVisible()
    await recent.click()
    await expect(page.locator('.chat__thread')).toBeVisible()
    await expect(page.locator('.bubble').first()).toBeVisible()
    await page.screenshot({ path: 'tests/visual/output/chat.png', fullPage: true })

    // Expand the first activity row to prove the detail layer renders.
    const activity = page.locator('.activity-card__trigger').first()
    if (await activity.count()) {
      await activity.click()
      await page.screenshot({ path: 'tests/visual/output/chat-activity-open.png', fullPage: true })
    }

    await page.getByRole('button', { name: 'Visão geral' }).click()
    await expect(page.getByRole('complementary', { name: 'Visão geral da execução' })).toBeVisible()
    await page.waitForTimeout(1200)
    await page.screenshot({ path: 'tests/visual/output/overview.png' })
  })

  test('chat holds up at a narrow viewport', async ({ page }) => {
    await page.setViewportSize({ width: 420, height: 860 })
    await page.goto(BASE)
    const recent = page.locator('.home__recent-item').first()
    await recent.click()
    await expect(page.locator('.chat__thread')).toBeVisible()
    // Let the entrance animations settle so the capture is of a resting layout.
    await page.waitForTimeout(700)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
    await page.screenshot({ path: 'tests/visual/output/chat-narrow.png', fullPage: true })
  })
})
