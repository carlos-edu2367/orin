import { expect, test } from '@playwright/test'
import { CONVERSATION_ID, conversation, expectNoSeriousA11yViolations, mockChat, setTheme } from '../support/w7Chat'
import { RUNNING_TURN_ID, runningOverride } from '../support/w7Running'

const textbox = (page: import('@playwright/test').Page) => page.getByRole('textbox', { name: 'Mensagem' })

test.describe('W7 chat surfaces (D1)', () => {
  test('`!command` runs a shell turn in a Code conversation', async ({ page }) => {
    const sent = await mockChat(page, ({ method, path }) => (method === 'POST' && path.endsWith('/shell') ? { status: 201, body: { turn_id: 't3' } } : undefined))
    await page.goto(`/chats/${CONVERSATION_ID}`)
    await textbox(page).fill('!git status')
    await expect(page.getByRole('status').filter({ hasText: 'Modo shell' })).toBeVisible()
    await expectNoSeriousA11yViolations(page)
    await textbox(page).press('Enter')
    await expect.poll(() => sent.find((item) => item.path.endsWith('/shell'))?.body).toEqual({ command: 'git status' })
    await expect(textbox(page)).toHaveValue('')
  })

  test('Enter while a turn runs queues the message; "Enviar agora" stops the turn and sends it', async ({ page }) => {
    let queued: Array<{ queue_id: string; content: string; created_at: string }> = []
    let cancelled = false
    const running = runningOverride()
    const sent = await mockChat(page, ({ method, path, body }) => {
      // Once the turn is cancelled, the conversation reads as stopped (the send waits for that).
      if (cancelled && method === 'GET' && path === `/v1/conversations/${CONVERSATION_ID}`) {
        return { body: { ...conversation, ...running, state: 'cancelled', turns: running.turns.map((turn) => ({ ...turn, state: turn.turn_id === RUNNING_TURN_ID ? 'cancelled' : turn.state })) } }
      }
      if (path.endsWith('/queue') && method === 'POST') {
        queued = [{ queue_id: 'q1', content: String((body as { message: string }).message), created_at: '2026-10-20T12:01:05.000Z' }]
        return { status: 201, body: { queued: true, ...queued[0] } }
      }
      if (path.endsWith('/queue') && method === 'GET') return { body: { items: queued } }
      if (path.includes('/queue/') && method === 'DELETE') { queued = []; return { body: { queue_id: 'q1', cancelled: true } } }
      if (path.endsWith('/cancel')) { cancelled = true; return { status: 202, body: { cancelling: [RUNNING_TURN_ID] } } }
      if (path.endsWith('/messages') && method === 'POST') return { status: 201, body: { conversation_id: CONVERSATION_ID, turn_id: 't4', message_id: 'm9', state: 'queued' } }
      return undefined
    }, running)
    await page.goto(`/chats/${CONVERSATION_ID}`)
    await textbox(page).fill('Depois rode os testes de cobrança.')
    await textbox(page).press('Enter')
    const panel = page.getByRole('region', { name: 'Mensagens na fila' })
    await expect(panel).toContainText('Na fila (1)')
    await expect(panel).toContainText('Depois rode os testes de cobrança.')
    await expectNoSeriousA11yViolations(page)
    await panel.getByRole('button', { name: 'Enviar agora' }).click()
    await expect.poll(() => sent.some((item) => item.path.endsWith('/cancel'))).toBe(true)
    await expect.poll(() => sent.find((item) => item.method === 'POST' && item.path.endsWith('/messages'))).toBeTruthy()
  })

  test('the permission card offers Yes / Yes for the session / No; option 2 for an edit switches to accept-edits', async ({ page }) => {
    const sent = await mockChat(page, ({ path }) => {
      if (path.endsWith('/tool-permission')) return { body: { request_id: 'perm_9', decision: 'allow_once', allowed: true } }
      if (path.endsWith('/permission-mode')) return { body: { conversation_id: CONVERSATION_ID, mode: 'code', permission_mode: 'accept_edits' } }
      return undefined
    }, runningOverride({ requestId: 'perm_9', toolName: 'edit_file', path: 'billing/charge.py' }))
    await page.goto(`/chats/${CONVERSATION_ID}`)
    const dock = page.getByTestId('code-permission-dock')
    await expect(dock).toBeVisible()
    await expect(page.getByTestId('code-perm-allow-always')).toContainText('Sim, e permitir edições nesta sessão')
    await expectNoSeriousA11yViolations(page)
    await page.getByTestId('code-perm-allow-always').click()
    await expect.poll(() => sent.find((item) => item.path.endsWith('/tool-permission'))?.body).toMatchObject({ request_id: 'perm_9', decision: 'allow_once' })
    await expect.poll(() => sent.find((item) => item.path.endsWith('/permission-mode'))?.body).toMatchObject({ permission_mode: 'accept_edits' })
  })

  test('/rewind goes back to a message in a new conversation, with the message in the composer', async ({ page }) => {
    const sent = await mockChat(page, ({ method, path }) => {
      if (path.endsWith('/file-history')) return { body: { items: [{ message_id: 'm-user-1', turn_id: 't1', kind: 'message', created_at: null, rewound_at: null, files: ['billing/charge.py'] }] } }
      if (method === 'POST' && path.endsWith('/messages/m-user-1/rewind')) {
        return { body: { message_id: 'm-user-1', mode: 'conversation', fork: { conversation_id: CONVERSATION_ID, title: 'Ramo', prompt: 'Explique o fluxo de cobrança.', messages: 0 } } }
      }
      return undefined
    })
    await page.goto(`/chats/${CONVERSATION_ID}`)
    await textbox(page).fill('/rewind')
    await textbox(page).press('Enter')
    const picker = page.getByRole('dialog', { name: 'Voltar a uma mensagem' })
    await expect(picker).toBeVisible()
    await expect(picker).toContainText('1 arquivo mudou')
    await expectNoSeriousA11yViolations(page)
    await picker.getByRole('button', { name: 'Voltar a conversa' }).click()
    await expect.poll(() => sent.find((item) => item.path.endsWith('/rewind'))?.body).toEqual({ mode: 'conversation' })
    await expect(textbox(page)).toHaveValue('Explique o fluxo de cobrança.')
  })

  test('in the light theme the permission card and a queue read on load stay readable', async ({ page }) => {
    await setTheme(page, 'light')
    const queued = [{ queue_id: 'q1', content: 'Depois rode os testes.', created_at: '2026-10-20T12:01:05.000Z' }]
    await mockChat(page, ({ method, path }) => {
      if (method === 'GET' && path.endsWith('/queue')) return { body: { items: queued } }
      if (path.endsWith('/file-history')) return { body: { items: [] } }
      return undefined
    }, runningOverride({ requestId: 'perm_9', toolName: 'edit_file', path: 'billing/charge.py' }))
    await page.goto(`/chats/${CONVERSATION_ID}`)
    // A reload keeps what was queued visible: the chat reads the queue when it opens.
    await expect(page.getByRole('region', { name: 'Mensagens na fila' })).toContainText('Depois rode os testes.')
    const dock = page.getByTestId('code-permission-dock')
    await expect.poll(() => dock.evaluate((node) => getComputedStyle(node).opacity)).toBe('1')
    await expectNoSeriousA11yViolations(page)
  })

  test('a code rewind that is blocked says so and changes nothing', async ({ page }) => {
    await mockChat(page, ({ method, path }) => {
      if (path.endsWith('/file-history')) return { body: { items: [{ message_id: 'm-user-1', turn_id: 't1', kind: 'message', created_at: null, rewound_at: null, files: ['dados.bin'] }] } }
      if (method === 'POST' && path.endsWith('/rewind')) return { status: 409, body: { error: { code: 'rewind_blocked', category: 'CONFLICT', details: { files: [{ path: 'dados.bin' }] } } } }
      return undefined
    })
    await page.goto(`/chats/${CONVERSATION_ID}`)
    await textbox(page).fill('/rewind')
    await textbox(page).press('Enter')
    const picker = page.getByRole('dialog', { name: 'Voltar a uma mensagem' })
    await picker.getByRole('button', { name: 'Restaurar o código' }).click()
    await expect(picker.getByRole('alert')).toContainText('nada foi mudado')
  })
})
