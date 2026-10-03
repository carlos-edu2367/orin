import type { ApiClient } from './client'
import { invalidResponseError } from './errors'
import { CAPABILITY_NAMES, type Capabilities } from './session'

export type SessionUser = {
  userId: string
  username: string
  displayName: string
  role: 'admin' | 'member'
  active: boolean
  mustChangePassword: boolean
}

export type SessionState = { user: SessionUser; csrfToken: string | null; capabilities: Capabilities }
export type CreatedUser = { user: SessionUser; temporaryPassword: string }

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

export function parseUser(value: unknown): SessionUser {
  const data = record(value)
  if (typeof data.user_id !== 'string' || typeof data.username !== 'string') throw invalidResponseError()
  if (data.role !== 'admin' && data.role !== 'member') throw invalidResponseError()
  return {
    userId: data.user_id,
    username: data.username,
    displayName: typeof data.display_name === 'string' && data.display_name ? data.display_name : data.username,
    role: data.role,
    active: data.active === true,
    mustChangePassword: data.must_change_password === true,
  }
}

function parseCapabilities(value: unknown): Capabilities {
  const data = record(value)
  const result = {} as Capabilities
  for (const name of CAPABILITY_NAMES) {
    if (typeof data[name] !== 'boolean') throw invalidResponseError()
    result[name] = data[name] as boolean
  }
  return result
}

export function parseSessionState(value: unknown): SessionState {
  const data = record(value)
  return {
    user: parseUser(data.user),
    csrfToken: typeof data.csrf_token === 'string' ? data.csrf_token : null,
    capabilities: parseCapabilities(data.capabilities),
  }
}

function parseCreated(value: unknown): CreatedUser {
  const data = record(value)
  if (typeof data.temporary_password !== 'string') throw invalidResponseError()
  return { user: parseUser(data.user), temporaryPassword: data.temporary_password }
}

export function getMe(client: ApiClient, signal?: AbortSignal): Promise<SessionState> {
  return client.request({ path: '/v1/auth/me', signal, parse: parseSessionState })
}

export function setupInstance(client: ApiClient, input: { token: string; username: string; password: string }): Promise<SessionState> {
  return client.request({ path: '/v1/auth/setup', method: 'POST', body: input, expectedStatus: 201, parse: parseSessionState })
}

export function login(client: ApiClient, input: { username: string; password: string }): Promise<SessionState> {
  return client.request({ path: '/v1/auth/login', method: 'POST', body: input, parse: parseSessionState })
}

export function logout(client: ApiClient): Promise<void> {
  return client.request({ path: '/v1/auth/logout', method: 'POST', expectedStatus: 204, parse: () => undefined })
}

export function changePassword(client: ApiClient, input: { currentPassword: string; newPassword: string }): Promise<SessionState> {
  return client.request({
    path: '/v1/auth/password', method: 'POST',
    body: { current_password: input.currentPassword, new_password: input.newPassword },
    parse: parseSessionState,
  })
}

export function listUsers(client: ApiClient): Promise<SessionUser[]> {
  return client.request({
    path: '/v1/admin/users',
    parse: (value) => {
      const items = record(value).items
      if (!Array.isArray(items)) throw invalidResponseError()
      return items.map(parseUser)
    },
  })
}

export function createUser(client: ApiClient, input: { username: string; displayName: string; role: 'admin' | 'member' }): Promise<CreatedUser> {
  return client.request({
    path: '/v1/admin/users', method: 'POST', expectedStatus: 201,
    body: { username: input.username, display_name: input.displayName || null, role: input.role },
    parse: parseCreated,
  })
}

export function updateUser(client: ApiClient, userId: string, patch: { displayName?: string; role?: 'admin' | 'member'; active?: boolean }): Promise<SessionUser> {
  return client.request({
    path: `/v1/admin/users/${encodeURIComponent(userId)}`, method: 'PATCH',
    body: { display_name: patch.displayName, role: patch.role, active: patch.active },
    parse: (value) => parseUser(record(value).user),
  })
}

export function resetUserPassword(client: ApiClient, userId: string): Promise<CreatedUser> {
  return client.request({ path: `/v1/admin/users/${encodeURIComponent(userId)}/reset-password`, method: 'POST', parse: parseCreated })
}
