import { expect, test, type Page } from '@playwright/test'
import { CONVERSATION_ID, mockChat, setTheme } from '../support/w7Chat'
import { blockedBySizeRoutes, mcpScopesRoutes } from '../support/w7BatchF'
import { mockSettings } from '../support/w7Settings'

// W7 Batch F: MCP servers per scope, rewinding a file too large to back up, and the history search. Desktop in the
// dark theme, a 375 px phone in the light theme.
const VIEWS = [
  { name: 'desktop-dark', theme: 'dark' as const, size: { width: 1280, height: 900 } },
  { name: 'mobile-light', theme: 'light' as const, size: { width: 375, height: 812 } },
]

async function prepare(page: Page, view: (typeof VIEWS)[number]) {
  await setTheme(page, view.theme)
  await page.setViewportSize(view.size)
}

for (const view of VIEWS) {
  test.describe(`W7 Batch F · ${view.name}`, () => {
    test('a project MCP server waiting for approval (active)', async ({ page }) => {
      await prepare(page, view)
      const route = mcpScopesRoutes()
      await mockSettings(page, (request) => route(request))
      await page.goto(`/settings/mcp-scopes?conversation=${CONVERSATION_ID}`)
      await expect(page.getByRole('button', { name: 'Aprovar' })).toBeVisible()
      await expect(page).toHaveScreenshot(`w7-mcp-scopes-${view.name}.png`)
    })

    test('a rewind blocked by a file too large to back up (error)', async ({ page }) => {
      await prepare(page, view)
      await mockChat(page, blockedBySizeRoutes())
      await page.goto(`/chats/${CONVERSATION_ID}`)
      const input = page.getByRole('textbox', { name: 'Mensagem' })
      await input.fill('/rewind')
      await input.press('Enter')
      const picker = page.getByRole('dialog', { name: 'Voltar a uma mensagem' })
      await picker.getByRole('button', { name: 'Restaurar o código' }).click()
      await expect(picker.getByRole('button', { name: 'Restaurar mesmo assim' })).toBeVisible()
      await expect(page).toHaveScreenshot(`w7-rewind-unbacked-${view.name}.png`)
    })

    test('searching the message history (active)', async ({ page }) => {
      await prepare(page, view)
      await mockChat(page)
      await page.goto(`/chats/${CONVERSATION_ID}`)
      const input = page.getByRole('textbox', { name: 'Mensagem' })
      await input.click()
      await input.press('Control+r')
      await page.getByRole('search').getByRole('textbox').fill('cobrança')
      await expect(page.getByRole('search').getByRole('status')).toHaveText('Explique o fluxo de cobrança.')
      await expect(page).toHaveScreenshot(`w7-history-search-${view.name}.png`)
    })
  })
}
