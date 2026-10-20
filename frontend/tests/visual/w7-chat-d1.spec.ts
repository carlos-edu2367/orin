import { expect, test, type Page } from '@playwright/test'
import { CONVERSATION_ID, mockChat, setTheme, type ExtraRoute } from '../support/w7Chat'
import { runningOverride } from '../support/w7Running'

// W7 Batch D1 surfaces: the `!` shell mode, the message queue, the three-option permission card and the rewind
// picker. Desktop in the dark theme, a 375 px phone in the light theme.
const VIEWS = [
  { name: 'desktop-dark', theme: 'dark' as const, size: { width: 1280, height: 860 } },
  { name: 'mobile-light', theme: 'light' as const, size: { width: 375, height: 812 } },
]

async function open(page: Page, view: (typeof VIEWS)[number], extra?: ExtraRoute, override?: Record<string, unknown>) {
  await setTheme(page, view.theme)
  await page.setViewportSize(view.size)
  await mockChat(page, extra, override)
  await page.goto(`/chats/${CONVERSATION_ID}`)
  const input = page.getByRole('textbox', { name: 'Mensagem' })
  await expect(input).toBeVisible()
  return input
}

const history: ExtraRoute = ({ path }) => (path.endsWith('/file-history')
  ? { body: { items: [{ message_id: 'm-user-1', turn_id: 't1', kind: 'message', created_at: null, rewound_at: null, files: ['billing/charge.py', 'billing/tests/test_charge.py'] }] } }
  : undefined)

for (const view of VIEWS) {
  test.describe(`W7 chat surfaces D1 · ${view.name}`, () => {
    test('shell mode in the composer (active)', async ({ page }) => {
      const input = await open(page, view)
      await input.fill('!git status')
      await expect(page.getByRole('status').filter({ hasText: 'Modo shell' })).toBeVisible()
      await expect(page).toHaveScreenshot(`w7-shell-${view.name}.png`)
    })

    test('a queued message while the turn runs (active)', async ({ page }) => {
      const queued = [{ queue_id: 'q1', content: 'Depois rode os testes de cobrança.', created_at: '2026-10-20T12:01:05.000Z' }]
      await open(page, view, ({ method, path }) => (method === 'GET' && path.endsWith('/queue') ? { body: { items: queued } } : undefined), runningOverride())
      await expect(page.getByRole('region', { name: 'Mensagens na fila' })).toContainText('Na fila (1)')
      await expect(page).toHaveScreenshot(`w7-queue-${view.name}.png`)
    })

    test('the permission card for an edit (active)', async ({ page }) => {
      await open(page, view, undefined, runningOverride({ requestId: 'perm_9', toolName: 'edit_file', path: 'billing/charge.py' }))
      await expect(page.getByTestId('code-perm-allow-always')).toContainText('Sim, e permitir edições nesta sessão')
      // The card fades in with a JS animation, which the screenshot's animation freeze does not stop.
      await expect.poll(() => page.getByTestId('code-permission-dock').evaluate((node) => getComputedStyle(node).opacity)).toBe('1')
      await expect(page).toHaveScreenshot(`w7-permission-${view.name}.png`)
    })

    test('the rewind picker (active) and a blocked code rewind (error)', async ({ page }) => {
      const input = await open(page, view, (request) => {
        if (request.method === 'POST' && request.path.endsWith('/rewind')) {
          return { status: 409, body: { error: { code: 'rewind_blocked', category: 'CONFLICT', details: { files: [{ path: 'dados.bin' }] } } } }
        }
        return history(request)
      })
      await input.fill('/rewind')
      await input.press('Enter')
      const picker = page.getByRole('dialog', { name: 'Voltar a uma mensagem' })
      await expect(picker).toContainText('2 arquivos mudaram')
      await expect(page).toHaveScreenshot(`w7-rewind-${view.name}.png`)
      await picker.getByRole('button', { name: 'Restaurar o código' }).click()
      await expect(picker.getByRole('alert')).toContainText('nada foi mudado')
      await expect(page).toHaveScreenshot(`w7-rewind-blocked-${view.name}.png`)
    })

    test('the rewind picker with no messages (empty)', async ({ page }) => {
      const input = await open(page, view, history, { messages: [], turns: [] })
      await input.fill('/rewind')
      await input.press('Enter')
      await expect(page.getByRole('dialog', { name: 'Voltar a uma mensagem' })).toContainText('Ainda não há mensagens para voltar.')
      await expect(page).toHaveScreenshot(`w7-rewind-empty-${view.name}.png`)
    })
  })
}
