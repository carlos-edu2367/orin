import { expect, test, type Page } from '@playwright/test'
import { CONVERSATION_ID, mockChat, setTheme } from '../support/w7Chat'

// W7 Batch B surfaces: the `/` picker with Orin's commands, the /help and /status answers, and the refusal of a
// command Orin leaves out. Desktop in the dark theme, a 375 px phone in the light theme.
const VIEWS = [
  { name: 'desktop-dark', theme: 'dark' as const, size: { width: 1280, height: 860 } },
  { name: 'mobile-light', theme: 'light' as const, size: { width: 375, height: 812 } },
]

async function open(page: Page, theme: 'dark' | 'light', size: { width: number; height: number }) {
  await setTheme(page, theme)
  await page.setViewportSize(size)
  await mockChat(page)
  await page.goto(`/chats/${CONVERSATION_ID}`)
  const input = page.getByRole('textbox', { name: 'Mensagem' })
  await expect(input).toBeVisible()
  return input
}

for (const view of VIEWS) {
  test.describe(`W7 commands · ${view.name}`, () => {
    test('picker with Orin commands (active)', async ({ page }) => {
      const input = await open(page, view.theme, view.size)
      await input.fill('/c')
      await expect(page.getByRole('listbox', { name: 'Comandos e skills' })).toBeVisible()
      await expect(page).toHaveScreenshot(`w7-picker-${view.name}.png`)
    })

    test('no matching command (empty)', async ({ page }) => {
      const input = await open(page, view.theme, view.size)
      await input.fill('/zzz')
      await expect(page.getByRole('listbox', { name: 'Comandos e skills' })).toHaveCount(0)
      await expect(page).toHaveScreenshot(`w7-picker-empty-${view.name}.png`)
    })

    test('/help and /status answers', async ({ page }) => {
      const input = await open(page, view.theme, view.size)
      await input.fill('/help')
      await input.press('Enter')
      await expect(page.getByRole('dialog', { name: 'Comandos e atalhos' })).toBeVisible()
      await expect(page).toHaveScreenshot(`w7-help-${view.name}.png`)
      await page.keyboard.press('Escape')
      await input.fill('/status')
      await input.press('Enter')
      await expect(page.getByRole('dialog', { name: 'Status da sessão' })).toContainText('Orin 0.9.4')
      await expect(page).toHaveScreenshot(`w7-status-${view.name}.png`)
    })

    test('a command Orin leaves out (error)', async ({ page }) => {
      const input = await open(page, view.theme, view.size)
      await input.fill('/login')
      await input.press('Enter')
      await expect(page.getByText('/login não existe no Orin.')).toBeVisible()
      await expect(page).toHaveScreenshot(`w7-excluded-${view.name}.png`)
    })
  })
}
