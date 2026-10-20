import { expect, test } from '@playwright/test'
import { CONVERSATION_ID, expectNoSeriousA11yViolations, mockChat, setTheme } from '../support/w7Chat'
import { STATUS_LINE, surfacesRoutes, TRANSCRIPT_ACTIVITIES } from '../support/w7Surfaces'

const textbox = (page: import('@playwright/test').Page) => page.getByRole('textbox', { name: 'Mensagem' })

test.describe('W7 chat surfaces (D2)', () => {
  test('/context shows the context window as a grid with a legend', async ({ page }) => {
    await mockChat(page, surfacesRoutes())
    await page.goto(`/chats/${CONVERSATION_ID}`)
    await textbox(page).fill('/context')
    await textbox(page).press('Enter')
    const dialog = page.getByRole('dialog', { name: 'Uso do contexto' })
    await expect(dialog.getByRole('img', { name: /61\.400 de 200\.000 tokens \(31%\)/ })).toBeVisible()
    await expect(dialog).toContainText('Ferramentas MCP')
    await expectNoSeriousA11yViolations(page)
  })

  test('/context before any measurement says so', async ({ page }) => {
    await mockChat(page, surfacesRoutes({ contextAvailable: false }))
    await page.goto(`/chats/${CONVERSATION_ID}`)
    await textbox(page).fill('/context')
    await textbox(page).press('Enter')
    await expect(page.getByRole('dialog', { name: 'Uso do contexto' })).toContainText('Ainda não há medição do contexto')
  })

  test('/bashes lists the background shells and subagents, and stops a running shell', async ({ page }) => {
    const stopped: string[] = []
    await mockChat(page, surfacesRoutes({ stopped }))
    await page.goto(`/chats/${CONVERSATION_ID}`)
    await textbox(page).fill('/bashes')
    await textbox(page).press('Enter')
    const panel = page.getByRole('dialog', { name: 'Tarefas em segundo plano' })
    await expect(panel).toContainText('npm run dev')
    await expect(panel).toContainText('Revisor')
    await expectNoSeriousA11yViolations(page)
    await panel.getByRole('button', { name: 'Parar' }).click()
    await expect.poll(() => stopped).toEqual(['shell:4242'])
    await expect(panel.getByRole('button', { name: 'Parar' })).toHaveCount(0)
    await page.keyboard.press('Escape')
    await expect(panel).toHaveCount(0)
  })

  test('ctrl+o opens the detailed transcript with reasoning and hooks; Escape closes it', async ({ page }) => {
    await mockChat(page, surfacesRoutes(), { activities: TRANSCRIPT_ACTIVITIES, activity_cursor: '3' })
    await page.goto(`/chats/${CONVERSATION_ID}`)
    await expect(textbox(page)).toBeVisible()
    await page.keyboard.press('Control+o')
    const transcript = page.getByRole('dialog', { name: 'Transcrição detalhada' })
    await expect(transcript).toContainText('Preciso ler billing/charge.py antes de explicar o fluxo.')
    await expect(transcript).toContainText('Hook PostToolUse (project)')
    await expect(transcript).toContainText('Motivo: O lint encontrou 2 erros')
    await expectNoSeriousA11yViolations(page)
    await page.keyboard.press('Escape')
    await expect(transcript).toHaveCount(0)
  })

  test('the status line shows the command’s coloured output under the composer, in both themes', async ({ page }) => {
    await setTheme(page, 'light')
    const sent = await mockChat(page, surfacesRoutes())
    await page.goto(`/chats/${CONVERSATION_ID}`)
    const line = page.getByRole('status', { name: 'Linha de status' })
    await expect(line).toHaveText('Orin · pagamentos · main · contexto 31%')
    expect(STATUS_LINE.lines[0]).toContain('\u001b[')
    expect(sent.filter((item) => item.path.endsWith('/statusline')).length).toBeGreaterThan(0)
    await expectNoSeriousA11yViolations(page)
  })

  test('an untrusted folder keeps the status line blank', async ({ page }) => {
    await mockChat(page, surfacesRoutes({ statusLine: { enabled: false, lines: [], error: 'workspace_not_trusted' } }))
    await page.goto(`/chats/${CONVERSATION_ID}`)
    await expect(textbox(page)).toBeVisible()
    await page.waitForTimeout(600) // the line runs 300 ms after the chat opens
    await expect(page.getByRole('status', { name: 'Linha de status' })).toHaveCount(0)
  })
})
