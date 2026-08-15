import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

test.describe('settings shell accessibility', () => {
  test('keeps the shared landmarks and passes axe on General', async ({ page }) => {
    await page.goto('/settings/general')
    await expect(page.getByRole('navigation', { name: 'Settings' })).toBeVisible()
    await expect(page.getByRole('heading', { level: 1, name: 'General' })).toBeVisible()
    await expect(page.locator('.app-shell')).toHaveCount(0)
    const results = await new AxeBuilder({ page }).analyze()
    expect(results.violations).toEqual([])
  })

  test('opens a provider detail region and restores focus with Escape', async ({ page }) => {
    await page.goto('/settings/providers')
    const card = page.getByRole('link', { name: /OpenAI/ })
    await expect(card).toBeVisible()
    await card.focus()
    await card.press('Enter')
    await expect(page.getByRole('region', { name: 'OpenAI' })).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(page).toHaveURL(/\/settings\/providers$/)
    await expect(card).toBeFocused()
  })

  test('keeps the sidebar reachable at a narrow viewport', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 })
    await page.goto('/settings/general')
    await expect(page.getByRole('navigation', { name: 'Settings' })).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
  })

  test('keeps the Plugins empty state and installer accessible', async ({ page }) => {
    await page.goto('/settings/plugins')
    await expect(page.getByRole('heading', { level: 1, name: 'Plugins' })).toBeVisible()
    await expect(page.getByRole('heading', { level: 2, name: 'Nenhum plugin instalado' })).toBeVisible()
    await page.getByRole('button', { name: 'Instalar plugin', exact: true }).click()
    await expect(page.getByRole('dialog', { name: 'Instalar plugin' })).toBeVisible()
    const results = await new AxeBuilder({ page }).analyze()
    expect(results.violations).toEqual([])
  })
})
