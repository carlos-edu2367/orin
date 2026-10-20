import { expect, test, type Page } from '@playwright/test'
import { CONVERSATION_ID, mockChat, setTheme, type ExtraRoute } from '../support/w7Chat'
import { surfacesRoutes, TRANSCRIPT_ACTIVITIES } from '../support/w7Surfaces'

// W7 Batch D2 surfaces: the /context grid, the tasks panel, the detailed transcript and the status line. Desktop in
// the dark theme, a 375 px phone in the light theme.
const VIEWS = [
  { name: 'desktop-dark', theme: 'dark' as const, size: { width: 1280, height: 860 } },
  { name: 'mobile-light', theme: 'light' as const, size: { width: 375, height: 812 } },
]

async function open(page: Page, view: (typeof VIEWS)[number], extra: ExtraRoute, override?: Record<string, unknown>) {
  await setTheme(page, view.theme)
  await page.setViewportSize(view.size)
  await mockChat(page, extra, override)
  await page.goto(`/chats/${CONVERSATION_ID}`)
  const input = page.getByRole('textbox', { name: 'Mensagem' })
  await expect(input).toBeVisible()
  return input
}

async function command(page: Page, input: import('@playwright/test').Locator, text: string) {
  await input.fill(text)
  await input.press('Enter')
}

const failingTasks: ExtraRoute = (request) => (request.method === 'GET' && request.path.endsWith('/tasks')
  ? { status: 500, body: { error: { code: 'internal', category: 'INTERNAL' } } }
  : surfacesRoutes()(request))

// The tasks panel shows local times: one time zone keeps the baselines the same on every machine.
test.use({ timezoneId: 'America/Sao_Paulo' })

for (const view of VIEWS) {
  test.describe(`W7 chat surfaces D2 · ${view.name}`, () => {
    test('the status line under the composer (active)', async ({ page }) => {
      await open(page, view, surfacesRoutes())
      await expect(page.getByRole('status', { name: 'Linha de status' })).toContainText('contexto 31%')
      await expect(page).toHaveScreenshot(`w7-statusline-${view.name}.png`)
    })

    test('the /context grid (active)', async ({ page }) => {
      const input = await open(page, view, surfacesRoutes())
      await command(page, input, '/context')
      await expect(page.getByRole('dialog', { name: 'Uso do contexto' }).getByRole('img')).toBeVisible()
      await expect(page).toHaveScreenshot(`w7-context-${view.name}.png`)
    })

    test('the /context answer before a measurement (empty)', async ({ page }) => {
      const input = await open(page, view, surfacesRoutes({ contextAvailable: false }))
      await command(page, input, '/context')
      await expect(page.getByRole('dialog', { name: 'Uso do contexto' })).toContainText('Ainda não há medição')
      await expect(page).toHaveScreenshot(`w7-context-empty-${view.name}.png`)
    })

    test('the tasks panel with a shell’s output open (active)', async ({ page }) => {
      const input = await open(page, view, surfacesRoutes())
      await command(page, input, '/tasks')
      const panel = page.getByRole('dialog', { name: 'Tarefas em segundo plano' })
      await expect(panel).toContainText('Revisor')
      await panel.getByText('Saída').first().click()
      await expect(page).toHaveScreenshot(`w7-tasks-${view.name}.png`)
    })

    test('the tasks panel when it cannot read the tasks (error)', async ({ page }) => {
      const input = await open(page, view, failingTasks)
      await command(page, input, '/tasks')
      await expect(page.getByRole('dialog', { name: 'Tarefas em segundo plano' }).getByRole('alert')).toBeVisible()
      await expect(page).toHaveScreenshot(`w7-tasks-error-${view.name}.png`)
    })

    test('the detailed transcript (active)', async ({ page }) => {
      const input = await open(page, view, surfacesRoutes(), { activities: TRANSCRIPT_ACTIVITIES, activity_cursor: '3' })
      await input.press('Control+o')
      await expect(page.getByRole('dialog', { name: 'Transcrição detalhada' })).toContainText('Hook PostToolUse')
      await expect(page).toHaveScreenshot(`w7-transcript-${view.name}.png`)
    })
  })
}
