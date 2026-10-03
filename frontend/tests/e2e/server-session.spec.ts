import { expect, test, type Page, type Route } from '@playwright/test'

// Test-only fake of the server-mode auth contract documented in
// docs/superpowers/specs/2026-10-02-server-mode-profiles-design.md §4.3.
const CAPS = { shell: false, mcp_stdio: false, plugin_hooks: false, omniroute: false, host_folders: false, profile_files: true, open_in_desktop_app: false, ui_updater: false, user_admin: true }

function user(id: string, username: string, role: 'admin' | 'member', mustChange = false) {
  return { user_id: id, username, display_name: username, role, active: true, must_change_password: mustChange, created_at: 'x', updated_at: 'x', password_changed_at: 'x' }
}

async function fulfill(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

function error(code: string, _status: number) {
  return { error: { code, category: 'X', message_key: code, correlation_id: 'c', retryable: false, retry_after: null } }
}

async function sessionHtml(page: Page) {
  await page.route(/^http:\/\/127\.0\.0\.1:4173\/(login|setup|change-password|settings.*)?(\?.*)?$/, async (route) => {
    const response = await route.fetch()
    const body = (await response.text()).replace('name="agentos-auth-mode" content=""', 'name="agentos-auth-mode" content="session"')
    await route.fulfill({ response, body })
  })
}

test('a fresh instance goes from setup to a member who changes the temporary password', async ({ page }) => {
  let current: ReturnType<typeof user> | null = null
  let accounts = 0
  const seenCsrf: string[] = []

  await sessionHtml(page)
  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() !== 'GET') seenCsrf.push(request.headers()['x-csrf-token'] ?? '')
    if (path === '/v1/auth/me') {
      if (accounts === 0) return fulfill(route, error('setup_required', 409), 409)
      if (!current) return fulfill(route, error('authentication_required', 401), 401)
      return fulfill(route, { user: current, csrf_token: `csrf-${current.username}`, capabilities: CAPS })
    }
    if (path === '/v1/auth/setup') {
      accounts = 1; current = user('local-user', 'carla', 'admin')
      return fulfill(route, { user: current, csrf_token: 'csrf-carla', capabilities: CAPS }, 201)
    }
    if (path === '/v1/admin/users' && request.method() === 'POST') {
      accounts = 2
      return fulfill(route, { user: user('usr_1', 'bruno', 'member', true), temporary_password: 'Tmp-123456789012' }, 201)
    }
    if (path === '/v1/admin/users') return fulfill(route, { items: accounts > 1 ? [user('local-user', 'carla', 'admin'), user('usr_1', 'bruno', 'member', true)] : [user('local-user', 'carla', 'admin')] })
    if (path === '/v1/auth/logout') { current = null; return route.fulfill({ status: 204 }) }
    if (path === '/v1/auth/login') {
      current = user('usr_1', 'bruno', 'member', true)
      return fulfill(route, { user: current, csrf_token: 'csrf-bruno', capabilities: CAPS })
    }
    if (path === '/v1/auth/password') {
      current = user('usr_1', 'bruno', 'member')
      return fulfill(route, { user: current, csrf_token: 'csrf-bruno', capabilities: CAPS })
    }
    if (path === '/v1/conversations' || path === '/v1/projects/sidebar') return fulfill(route, { items: [] })
    return fulfill(route, {})
  })

  await page.goto('/')
  await expect(page).toHaveURL(/\/setup$/)
  await page.getByLabel('Token de setup').fill('the-token')
  await page.getByLabel('Usuário').fill('carla')
  await page.getByLabel('Senha', { exact: true }).fill('a long password')
  await page.getByLabel('Confirmar senha').fill('a long password')
  await page.getByRole('button', { name: 'Criar admin' }).click()

  await page.goto('/settings/users')
  await page.getByLabel('Usuário do novo perfil').fill('bruno')
  await page.getByRole('button', { name: 'Criar perfil' }).click()
  await expect(page.getByText('Tmp-123456789012')).toBeVisible()
  expect(seenCsrf.at(-1)).toBe('csrf-carla')

  await page.goto('/')
  await page.getByRole('button', { name: 'Perfil: carla' }).click()
  await page.getByRole('menuitem', { name: 'Sair' }).click()
  await page.getByLabel('Usuário').fill('bruno')
  await page.getByLabel('Senha').fill('Tmp-123456789012')
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page).toHaveURL(/\/change-password$/)
  await page.getByLabel('Senha atual').fill('Tmp-123456789012')
  await page.getByLabel('Nova senha', { exact: true }).fill('bruno own password')
  await page.getByLabel('Confirmar nova senha').fill('bruno own password')
  await page.getByRole('button', { name: 'Trocar senha' }).click()
  await expect(page).toHaveURL(/\/$/)

  await page.goto('/settings/users')
  await expect(page).toHaveURL(/\/settings\/general$/)
})
