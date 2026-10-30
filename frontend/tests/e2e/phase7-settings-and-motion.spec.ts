import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { mockSettings } from '../support/w7Settings'

const ARTIFACT_DIR = 'C:/Users/User/.gemini/antigravity/brain/3b3d2ee8-ba1f-4504-9638-a5bb14471222'

async function assertZeroCriticalOrSeriousViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).analyze()
  const bad = results.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')
  expect(
    bad,
    `${label} had critical/serious axe violations: ${JSON.stringify(bad.map((b) => ({ id: b.id, impact: b.impact, nodes: b.nodes.map((n) => n.html) })))}`,
  ).toEqual([])
}

async function applyTheme(page: Page, theme: 'dark' | 'light') {
  await page.evaluate((t) => {
    window.localStorage.setItem('orin.theme.mode', t)
    document.documentElement.setAttribute('data-theme', t)
    document.documentElement.setAttribute('data-theme-preference', t)
  }, theme)
  await page.waitForTimeout(250)
}

test.describe('Phase 7 — Settings (ref-14, ref-15, Voice) & Interactive Motion Choreography', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await mockSettings(page)
  })

  test('1. Modelos e provedores (ref-14.png) renders 2-column layout, 6-category SettingsNav, and passes Axe in dark & light mode', async ({ page }) => {
    await page.route('**/v1/providers/**', async (route) => {
      const url = route.request().url()
      if (url.endsWith('/models')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            items: [
              { model_id: 'gpt-5.4', display_name: 'GPT-5.4 · Recomendado', is_favorite: true },
              { model_id: 'gpt-4.1', display_name: 'GPT-4.1', is_favorite: false },
            ],
          }),
        })
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ provider: 'openai', enabled: true }),
      })
    })

    await page.goto('/settings/providers')
    await applyTheme(page, 'dark')

    // 6-Category primary menu
    const primaryNav = page.getByTestId('settings-primary-categories')
    await expect(primaryNav).toBeVisible()
    await expect(primaryNav.getByRole('link', { name: 'Modelos e provedores' })).toHaveAttribute('aria-current', 'page')

    // ref-14 cards
    await expect(page.getByTestId('providers-ref14-heading')).toHaveText('Modelos e provedores')
    await expect(page.getByTestId('providers-primary-card')).toBeVisible()
    await expect(page.getByTestId('providers-fallback-card')).toBeVisible()
    await expect(page.getByTestId('providers-connected-card')).toBeVisible()
    await expect(page.getByTestId('providers-cost-card')).toBeVisible()
    await expect(page.getByTestId('providers-save-btn')).toBeVisible()

    await page.screenshot({ path: `${ARTIFACT_DIR}/fase7-ref14-providers.png`, fullPage: false })
    await assertZeroCriticalOrSeriousViolations(page, '/settings/providers (dark)')

    await applyTheme(page, 'light')
    await assertZeroCriticalOrSeriousViolations(page, '/settings/providers (light)')
  })

  test('2. Segurança e controle (ref-15.png) renders default permissions, authorized folders, approval preview card, and passes Axe in dark & light mode', async ({ page }) => {
    await page.goto('/settings/permissions')
    await applyTheme(page, 'dark')

    const primaryNav = page.getByTestId('settings-primary-categories')
    await expect(primaryNav.getByRole('link', { name: 'Segurança' })).toHaveAttribute('aria-current', 'page')

    await expect(page.getByTestId('security-ref15-heading')).toHaveText('Segurança e controle')
    await expect(page.getByTestId('security-default-permission-card')).toBeVisible()
    await expect(page.getByTestId('security-authorized-folders-card')).toBeVisible()
    await expect(page.getByTestId('security-external-actions-card')).toBeVisible()
    await expect(page.getByTestId('security-approval-preview-card')).toBeVisible()

    await page.screenshot({ path: `${ARTIFACT_DIR}/fase7-ref15-security.png`, fullPage: false })
    await assertZeroCriticalOrSeriousViolations(page, '/settings/permissions (dark)')

    await applyTheme(page, 'light')
    await assertZeroCriticalOrSeriousViolations(page, '/settings/permissions (light)')
  })

  test('3. Voz (/settings/voice) renders embedded voice settings and passes Axe in dark & light mode', async ({ page }) => {
    await page.goto('/settings/voice')
    await applyTheme(page, 'dark')

    const primaryNav = page.getByTestId('settings-primary-categories')
    await expect(primaryNav.getByRole('link', { name: 'Voz' })).toHaveAttribute('aria-current', 'page')
    await expect(page.getByTestId('settings-voice-section')).toBeVisible()

    await page.screenshot({ path: `${ARTIFACT_DIR}/fase7-voice-settings.png`, fullPage: false })
    await assertZeroCriticalOrSeriousViolations(page, '/settings/voice (dark)')

    await applyTheme(page, 'light')
    await assertZeroCriticalOrSeriousViolations(page, '/settings/voice (light)')
  })

  test('4. Interactive Home (orin-home-interativa.html) triggers mascot celebration & 9 radial particles and passes Axe in dark & light mode', async ({ page }) => {
    await page.goto('/')
    await applyTheme(page, 'dark')

    const heroMascot = page.getByTestId('home-hero-mascot')
    await expect(heroMascot).toBeVisible()
    const particles = heroMascot.getByTestId('orin-mascot-particles')
    await expect(particles.locator('.orin-mascot__particle')).toHaveCount(9)

    // Trigger celebration by clicking the hero mascot
    await heroMascot.click()
    await expect(heroMascot).toHaveAttribute('data-celebrating', 'true')

    await page.screenshot({ path: `${ARTIFACT_DIR}/fase7-home-interactive.png`, fullPage: false })
    await assertZeroCriticalOrSeriousViolations(page, '/ (dark)')

    await applyTheme(page, 'light')
    await assertZeroCriticalOrSeriousViolations(page, '/ (light)')
  })
})
