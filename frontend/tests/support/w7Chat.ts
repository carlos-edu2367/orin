/**
 * W7 browser-test support: an idle Orin Code conversation behind a mocked `/v1` API, a theme, and an axe check.
 * Each spec adds its own routes through `extra` (matched first) and reads what was sent from `sent`.
 */
import AxeBuilder from '@axe-core/playwright'
import { expect, type Page, type Route } from '@playwright/test'

export const CONVERSATION_ID = 'conv-w7'

export type SentRequest = { method: string; path: string; body: unknown }
export type ExtraRoute = (request: { method: string; path: string; body: unknown }) => { status?: number; body: unknown } | undefined

export const conversation = {
  conversation_id: CONVERSATION_ID,
  title: 'Refatorar o módulo de pagamentos',
  state: 'completed',
  provider: 'openrouter',
  model_id: 'anthropic/claude-sonnet-5.5',
  mode: 'code',
  permission_mode: 'ask_permissions',
  project_id: null,
  workspace: { mode: 'local_folder', path: 'C:/Projetos/pagamentos', folder_name: 'pagamentos', status: 'available' },
  messages: [
    { message_id: 'm-user-1', turn_id: 't1', role: 'user', content: 'Explique o fluxo de cobrança.', status: 'completed', retryable: false, attachments: [], command: null },
    { message_id: 'm-asst-1', turn_id: 't1', role: 'assistant', content: 'O fluxo começa em `billing/charge.py`, que valida o pedido e chama o gateway.', status: 'completed', retryable: false, attachments: [], command: null },
  ],
  turns: [{ turn_id: 't1', state: 'completed', created_at: '2026-10-20T12:00:00.000Z', started_at: '2026-10-20T12:00:01.000Z', finished_at: '2026-10-20T12:00:09.000Z', scheduled_by_schedule_id: null, code_mode: 'code' }],
  activities: [],
  activity_cursor: '1',
}

async function fulfill(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

export async function setTheme(page: Page, mode: 'dark' | 'light') {
  await page.addInitScript((value) => { window.localStorage.setItem('orin.theme.mode', value) }, mode)
}

export async function mockChat(page: Page, extra: ExtraRoute = () => undefined): Promise<SentRequest[]> {
  const sent: SentRequest[] = []
  await page.route('**/chats/**', async (route) => {
    if (route.request().resourceType() !== 'document') { await route.fallback(); return }
    const response = await route.fetch()
    const body = (await response.text()).replace('<head>', '<head><meta name="csrf-token" content="csrf-w7-e2e">')
    await route.fulfill({ response, body })
  })
  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    let body: unknown
    try { body = request.postDataJSON() } catch { body = undefined }
    sent.push({ method, path, body })
    const custom = extra({ method, path, body })
    if (custom) { await fulfill(route, custom.body, custom.status ?? 200); return }
    if (path.endsWith('/events')) {
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'event: heartbeat\ndata: {"cursor":"1"}\n\n' })
      return
    }
    if (method === 'GET' && path === `/v1/conversations/${CONVERSATION_ID}`) { await fulfill(route, conversation); return }
    if (method === 'GET' && (path === '/v1/conversations' || path === '/v1/projects' || path === '/v1/projects/sidebar' || path === '/v1/skills' || path === '/v1/plugins/commands')) {
      await fulfill(route, { items: [] }); return
    }
    if (method === 'GET' && path === `/v1/conversations/${CONVERSATION_ID}/commands`) {
      await fulfill(route, { trusted: true, commands: [{ name: 'deploy', description: 'Publica a versão no ambiente de testes', kind: 'command', origin: 'project', argument_hint: '<ambiente>', context: null }] })
      return
    }
    if (method === 'GET' && path === '/v1/instance') { await fulfill(route, { version: '0.9.4', mode: 'local', capabilities: { host_folders: true }, credential: 'session' }); return }
    if (method === 'GET' && path === '/v1/settings/keybindings') {
      await fulfill(route, { orin: { path: 'C:/Users/p/.orin/keybindings.json', text: null, error: null }, claude: { path: 'C:/Users/p/.claude/keybindings.json', text: null, error: null } })
      return
    }
    await fulfill(route, {})
  })
  return sent
}

export async function expectNoSeriousA11yViolations(page: Page, include?: string): Promise<void> {
  const builder = new AxeBuilder({ page })
  const results = await (include ? builder.include(include) : builder).analyze()
  const blocking = results.violations.filter((violation) => violation.impact === 'critical' || violation.impact === 'serious')
  expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([])
}
