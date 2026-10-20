import { expect, test } from '@playwright/test'
import { CONVERSATION_ID, expectNoSeriousA11yViolations, mockChat, setTheme } from '../support/w7Chat'
import { BIG_FILE, blockedBySizeRoutes, EDITED_TURN, mcpScopesRoutes } from '../support/w7BatchF'
import { mockSettings } from '../support/w7Settings'

const textbox = (page: import('@playwright/test').Page) => page.getByRole('textbox', { name: 'Mensagem' })

test.describe('W7 Batch F', () => {
  test('a project MCP server waits for approval and is approved for this folder', async ({ page }) => {
    const sent: Array<{ method: string; body: unknown }> = []
    const route = mcpScopesRoutes(sent)
    await mockSettings(page, (request) => route(request))
    await page.goto(`/settings/mcp-scopes?conversation=${CONVERSATION_ID}`)
    const pending = page.getByRole('region', { name: 'Servidores do projeto aguardando a sua aprovação' })
    await expect(pending).toContainText('npx -y @modelcontextprotocol/server-github --token [REDACTED]')
    await expectNoSeriousA11yViolations(page)
    await pending.getByRole('button', { name: 'Aprovar' }).click()
    await expect.poll(() => sent[0]?.body).toEqual({ conversation_id: CONVERSATION_ID, server: 'github', decision: 'approve' })
    await expect(page.getByRole('button', { name: 'Esquecer a escolha sobre github' })).toBeVisible()
  })

  test('a code rewind blocked only by size can be done anyway, after the person sees the files', async ({ page }) => {
    await setTheme(page, 'light')
    const sent: Array<{ method: string; path: string; body: unknown }> = []
    await mockChat(page, blockedBySizeRoutes(sent))
    await page.goto(`/chats/${CONVERSATION_ID}`)
    await textbox(page).fill('/rewind')
    await textbox(page).press('Enter')
    const picker = page.getByRole('dialog', { name: 'Voltar a uma mensagem' })
    await picker.getByRole('button', { name: 'Restaurar o código' }).click()
    await expect(picker).toContainText(BIG_FILE)
    await expectNoSeriousA11yViolations(page)
    await picker.getByRole('button', { name: 'Restaurar mesmo assim' }).click()
    await expect.poll(() => sent.at(-1)?.body).toEqual({ mode: 'code', force_unbacked: [BIG_FILE] })
    await expect(page.getByText('1 sem cópia para desfazer')).toBeVisible()
  })

  test('a turn that changed files goes back through the picker, starting on its message', async ({ page }) => {
    await mockChat(page, blockedBySizeRoutes(), EDITED_TURN)
    await page.goto(`/chats/${CONVERSATION_ID}`)
    await page.getByRole('button', { name: /Voltar a este ponto/ }).click()
    const picker = page.getByRole('dialog', { name: 'Voltar a uma mensagem' })
    await expect(picker.getByRole('option', { selected: true })).toContainText('Explique o fluxo de cobrança.')
  })

  test('up recalls the last message and ctrl+r finds an earlier one', async ({ page }) => {
    await mockChat(page)
    await page.goto(`/chats/${CONVERSATION_ID}`)
    await textbox(page).click()
    await textbox(page).press('ArrowUp')
    await expect(textbox(page)).toHaveValue('Explique o fluxo de cobrança.')
    await textbox(page).fill('')
    await textbox(page).press('Control+r')
    const search = page.getByRole('search')
    await search.getByRole('textbox').fill('cobrança')
    await expect(search.getByRole('status')).toHaveText('Explique o fluxo de cobrança.')
    await expectNoSeriousA11yViolations(page)
    await page.keyboard.press('Tab')
    await expect(textbox(page)).toHaveValue('Explique o fluxo de cobrança.')
    await expect(search).toHaveCount(0)
  })
})
