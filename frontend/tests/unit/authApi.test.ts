import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { createUser, getMe, login, parseSessionState } from '../../src/api/auth'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const USER = { user_id: 'local-user', username: 'carla', display_name: 'Carla', role: 'admin', active: true, must_change_password: false, created_at: '2026-10-02T12:00:00+00:00', updated_at: '2026-10-02T12:00:00+00:00', password_changed_at: '2026-10-02T12:00:00+00:00' }
const CAPS = { shell: false, mcp_stdio: false, plugin_hooks: false, omniroute: false, host_folders: false, profile_files: true, open_in_desktop_app: false, ui_updater: false, user_admin: true }

describe('auth api', () => {
  it('parses the session state', () => {
    expect(parseSessionState({ user: USER, csrf_token: 'abc', capabilities: CAPS })).toEqual({
      user: { userId: 'local-user', username: 'carla', displayName: 'Carla', role: 'admin', active: true, mustChangePassword: false },
      csrfToken: 'abc',
      capabilities: CAPS,
    })
  })

  it('rejects a malformed session', () => {
    expect(() => parseSessionState({ user: { ...USER, role: 'root' }, csrf_token: 'x', capabilities: CAPS })).toThrow()
    expect(() => parseSessionState({ user: USER, csrf_token: 'x', capabilities: { ...CAPS, shell: 'yes' } })).toThrow()
  })

  it('posts credentials and reads me', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json({ user: USER, csrf_token: 'abc', capabilities: CAPS })))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })
    await login(client, { username: 'carla', password: 'a long password' })
    await getMe(client)
    expect(fetchImpl.mock.calls[0][0]).toBe('/v1/auth/login')
    expect(JSON.parse(String(fetchImpl.mock.calls[0][1]?.body))).toEqual({ username: 'carla', password: 'a long password' })
    expect(fetchImpl.mock.calls[1][0]).toBe('/v1/auth/me')
  })

  it('returns the temporary password of a new profile', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json({ user: { ...USER, user_id: 'usr_1', username: 'bruno', role: 'member', must_change_password: true }, temporary_password: 'Tmp-123456789012' }, 201)))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })
    const created = await createUser(client, { username: 'bruno', displayName: '', role: 'member' })
    expect(created.temporaryPassword).toBe('Tmp-123456789012')
    expect(created.user.mustChangePassword).toBe(true)
  })
})
