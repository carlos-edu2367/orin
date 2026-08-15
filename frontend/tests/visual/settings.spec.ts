import { expect, test } from '@playwright/test'

test.describe('settings shell visual states', () => {
  test.beforeEach(async ({ page }) => {
    // Keep settings baselines deterministic: badges are runtime data and should
    // not change screenshots merely because the local API happens to be running.
    await page.route('**/v1/**', (route) => route.abort())
  })

  test('general', async ({ page }) => {
    await page.goto('/settings/general')
    await expect(page.getByRole('heading', { level: 1, name: 'General' })).toBeVisible()
    await expect(page).toHaveScreenshot('settings-general.png', { fullPage: true })
  })

  test('provider grid and drawer', async ({ page }) => {
    await page.goto('/settings/providers')
    await expect(page.getByRole('link', { name: /OpenAI/ })).toBeVisible()
    await expect(page).toHaveScreenshot('settings-providers-grid.png', { fullPage: true })
    await page.getByRole('link', { name: /OpenAI/ }).click()
    await expect(page.getByRole('region', { name: 'OpenAI' })).toBeVisible()
    await expect(page).toHaveScreenshot('settings-providers-drawer-open.png', { fullPage: true })
  })

  test('skills, MCP and Plugins', async ({ page }) => {
    for (const [path, name, file] of [
      ['/settings/skills', 'Skills', 'settings-skills.png'],
      ['/settings/mcp', 'MCP', 'settings-mcp.png'],
      ['/settings/plugins', 'Plugins', 'settings-plugins.png'],
    ] as const) {
      await page.goto(path)
      await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
      await expect(page).toHaveScreenshot(file, { fullPage: true })
    }
  })

  test('provider shell at 375px', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 })
    await page.goto('/settings/providers')
    await expect(page.getByRole('link', { name: /OpenAI/ })).toBeVisible()
    await expect(page).toHaveScreenshot('settings-providers-narrow.png', { fullPage: true })
  })
})
