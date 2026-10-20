import { expect, test } from '@playwright/test'
import { CONVERSATION_ID, expectNoSeriousA11yViolations, mockChat, setTheme } from '../support/w7Chat'
import { mockSettings } from '../support/w7Settings'

test.describe('W7 settings editors (Batch E)', () => {
  test('the permissions editor shows provenance and adds a rule to the folder’s local layer', async ({ page }) => {
    const sent = await mockSettings(page)
    await page.goto('/settings/permissions?conversation=conv-w7')
    await expect(page.getByRole('heading', { level: 1, name: 'Permissões' })).toBeVisible()
    await expect(page.getByText('C:/ProgramData/Orin/managed-settings.json')).toBeVisible()
    await expect(page.getByRole('button', { name: /Remover Read\(\.\/\.env\)/ })).toHaveCount(0)
    await expectNoSeriousA11yViolations(page)
    await page.getByRole('textbox', { name: 'Regra' }).fill('WebFetch(domain:docs.python.org)')
    await page.getByRole('combobox', { name: 'Onde salvar' }).selectOption('local')
    await page.getByRole('button', { name: 'Adicionar' }).click()
    await expect.poll(() => sent.find((item) => item.method === 'PUT')?.body).toEqual({
      layer: 'local', kind: 'allow', rule: 'WebFetch(domain:docs.python.org)', op: 'add', conversation_id: 'conv-w7',
    })
  })

  test('a shared server says the editors are local-only', async ({ page }) => {
    await mockSettings(page, ({ path }) => (path.startsWith('/v1/settings/') ? { status: 404, body: { error: { code: 'not_found', category: 'NOT_FOUND' } } } : undefined))
    await page.goto('/settings/hooks')
    await expect(page.getByRole('alert')).toContainText('Este editor existe só no Orin local')
  })

  test('hooks, agents, output styles, keybindings and trusted folders have no serious accessibility problem in the light theme', async ({ page }) => {
    await setTheme(page, 'light')
    await mockSettings(page)
    for (const [path, heading] of [
      ['/settings/hooks?conversation=conv-w7', 'Hooks'],
      ['/settings/agents?conversation=conv-w7', 'Agentes'],
      ['/settings/output-styles?conversation=conv-w7', 'Estilo de resposta'],
      ['/settings/keybindings', 'Atalhos'],
      ['/settings/trusted-folders', 'Pastas confiáveis'],
    ] as const) {
      await page.goto(path)
      await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
      await expect(page.getByText('Carregando…')).toHaveCount(0)
      await expectNoSeriousA11yViolations(page)
    }
  })

  test('a keybindings file with an error cannot be saved', async ({ page }) => {
    const sent = await mockSettings(page)
    await page.goto('/settings/keybindings')
    const editor = page.getByRole('textbox', { name: 'keybindings.json' })
    await editor.fill('{ "bindings": [ { "context": "Nada", "bindings": { "ctrl+k": "chat:submit" } } ] }')
    await expect(page.getByRole('status', { name: 'Erros' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Salvar' })).toBeDisabled()
    expect(sent.some((item) => item.method === 'PUT')).toBe(false)
  })

  test('/permissions in a chat opens the editor on that conversation’s folder', async ({ page }) => {
    await mockChat(page, ({ path }) => (path === '/v1/settings/effective' ? { body: { project_root: 'C:/Projetos/pagamentos', trusted: true, writable: {}, permissions: {} } } : undefined))
    await page.goto(`/chats/${CONVERSATION_ID}`)
    const input = page.getByRole('textbox', { name: 'Mensagem' })
    await input.fill('/permissions')
    await input.press('Enter')
    await expect(page).toHaveURL(new RegExp(`/settings/permissions\\?conversation=${CONVERSATION_ID}$`))
    await expect(page.getByText('Pasta:')).toContainText('C:/Projetos/pagamentos')
  })
})
