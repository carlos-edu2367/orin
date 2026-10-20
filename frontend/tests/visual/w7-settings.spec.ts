import { expect, test, type Page } from '@playwright/test'
import { setTheme } from '../support/w7Chat'
import { mockSettings, type SettingsRequest } from '../support/w7Settings'

// W7 Batch E: the Orin Code settings editors. Desktop in the dark theme, a 375 px phone in the light theme.
const VIEWS = [
  { name: 'desktop-dark', theme: 'dark' as const, size: { width: 1280, height: 900 } },
  { name: 'mobile-light', theme: 'light' as const, size: { width: 375, height: 812 } },
]

async function open(page: Page, view: (typeof VIEWS)[number], path: string, heading: string,
  override?: (request: SettingsRequest) => { status?: number; body: unknown } | undefined) {
  await setTheme(page, view.theme)
  await page.setViewportSize(view.size)
  await mockSettings(page, override)
  await page.goto(path)
  await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
  await expect(page.getByText('Carregando…')).toHaveCount(0)
}

for (const view of VIEWS) {
  test.describe(`W7 settings editors · ${view.name}`, () => {
    test('permissions with provenance (active)', async ({ page }) => {
      await open(page, view, '/settings/permissions?conversation=conv-w7', 'Permissões')
      await expect(page).toHaveScreenshot(`w7-settings-permissions-${view.name}.png`, { fullPage: true })
    })

    test('an editor on a shared server (error)', async ({ page }) => {
      await open(page, view, '/settings/permissions', 'Permissões',
        ({ path }) => (path.startsWith('/v1/settings/') ? { status: 404, body: { error: { code: 'not_found', category: 'NOT_FOUND' } } } : undefined))
      await expect(page.getByRole('alert')).toBeVisible()
      await expect(page).toHaveScreenshot(`w7-settings-local-only-${view.name}.png`)
    })

    test('hooks in force and a layer’s editor (active)', async ({ page }) => {
      await open(page, view, '/settings/hooks?conversation=conv-w7', 'Hooks')
      await expect(page).toHaveScreenshot(`w7-settings-hooks-${view.name}.png`, { fullPage: true })
    })

    test('agents and the agent form with its problems (active)', async ({ page }) => {
      await open(page, view, '/settings/agents?conversation=conv-w7', 'Agentes')
      await page.getByRole('button', { name: 'Novo agente' }).click()
      await page.getByRole('textbox', { name: 'Nome' }).fill('Nome Inválido')
      await page.getByRole('button', { name: 'Salvar' }).click()
      await expect(page.getByText(/^Nome: letras minúsculas/)).toBeVisible()
      await expect(page).toHaveScreenshot(`w7-settings-agents-${view.name}.png`, { fullPage: true })
    })

    test('no agents yet (empty)', async ({ page }) => {
      await open(page, view, '/settings/agents', 'Agentes',
        ({ path }) => (path === '/v1/settings/agents' ? { body: { agents: [], errors: [], shadowed: [] } } : undefined))
      await expect(page.getByText('Nenhum agente definido em arquivos.')).toBeVisible()
      await expect(page).toHaveScreenshot(`w7-settings-agents-empty-${view.name}.png`)
    })

    test('output styles (active)', async ({ page }) => {
      await open(page, view, '/settings/output-styles?conversation=conv-w7', 'Estilo de resposta')
      await expect(page).toHaveScreenshot(`w7-settings-styles-${view.name}.png`, { fullPage: true })
    })

    test('keybindings with an error in the file (error)', async ({ page }) => {
      await open(page, view, '/settings/keybindings', 'Atalhos')
      await page.getByRole('textbox', { name: 'keybindings.json' }).fill('{ "bindings": [ { "context": "Nada", "bindings": { "ctrl+k": "chat:submit" } } ] }')
      await expect(page.getByRole('status', { name: 'Erros' })).toBeVisible()
      await expect(page).toHaveScreenshot(`w7-settings-keybindings-${view.name}.png`, { fullPage: true })
    })

    test('trusted folders, revoking one (active)', async ({ page }) => {
      await open(page, view, '/settings/trusted-folders', 'Pastas confiáveis')
      await page.getByRole('button', { name: /Revogar a confiança em/ }).click()
      await expect(page).toHaveScreenshot(`w7-settings-trusted-${view.name}.png`)
    })
  })
}
