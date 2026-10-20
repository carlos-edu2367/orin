import { expect, test } from '@playwright/test'
import { CONVERSATION_ID, expectNoSeriousA11yViolations, mockChat } from '../support/w7Chat'

test.describe('W7 built-in slash commands', () => {
  test('the picker offers Orin commands first, then the folder’s own', async ({ page }) => {
    await mockChat(page)
    await page.goto(`/chats/${CONVERSATION_ID}`)
    const input = page.getByRole('textbox', { name: 'Mensagem' })
    await input.fill('/')
    const picker = page.getByRole('listbox', { name: 'Comandos e skills' })
    await expect(picker).toBeVisible()
    await expect(picker.getByRole('option').first()).toContainText('/add-dir')
    await expect(picker.getByRole('option', { name: /\/deploy/ })).toContainText('Comando')
    await expectNoSeriousA11yViolations(page)
    await input.fill('/reset')
    await expect(picker.getByRole('option')).toHaveCount(1)
    await expect(picker.getByRole('option')).toContainText('/clear')
  })

  test('/help lists the commands and the keys, and Escape closes it', async ({ page }) => {
    await mockChat(page)
    await page.goto(`/chats/${CONVERSATION_ID}`)
    const input = page.getByRole('textbox', { name: 'Mensagem' })
    await input.fill('/help')
    // Enter picks the highlighted command; a built-in without arguments runs at once.
    await input.press('Enter')
    const dialog = page.getByRole('dialog', { name: 'Comandos e atalhos' })
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('/compact')
    await expect(dialog).toContainText('Ctrl+O')
    await expectNoSeriousA11yViolations(page)
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
    await expect(input).toHaveValue('')
  })

  test('/compact sends its instructions and /status answers with the version', async ({ page }) => {
    const sent = await mockChat(page, ({ method, path }) => (method === 'POST' && path.endsWith('/compact') ? { status: 202, body: { turn_id: 't2' } } : undefined))
    await page.goto(`/chats/${CONVERSATION_ID}`)
    const input = page.getByRole('textbox', { name: 'Mensagem' })
    await input.fill('/compact foque nos testes')
    await input.press('Enter')
    await expect(page.getByRole('status').filter({ hasText: 'Compactando a conversa' })).toBeVisible()
    expect(sent.find((item) => item.path.endsWith('/compact'))?.body).toEqual({ instructions: 'foque nos testes' })
    await input.fill('/status')
    await input.press('Enter')
    const dialog = page.getByRole('dialog', { name: 'Status da sessão' })
    await expect(dialog).toContainText('Orin 0.9.4')
    await expect(dialog).toContainText('C:/Projetos/pagamentos')
  })

  test('a command Orin leaves out says so and is not sent', async ({ page }) => {
    const sent = await mockChat(page)
    await page.goto(`/chats/${CONVERSATION_ID}`)
    const input = page.getByRole('textbox', { name: 'Mensagem' })
    await input.fill('/login')
    await input.press('Enter')
    await expect(page.getByText('/login não existe no Orin.')).toBeVisible()
    expect(sent.some((item) => item.method === 'POST' && item.path.endsWith('/messages'))).toBe(false)
    await expect(input).toHaveValue('/login')
  })
})
