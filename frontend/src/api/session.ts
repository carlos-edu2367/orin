/** Module-level session state shared by every ApiClient the app creates. */
export type Capabilities = {
  shell: boolean
  mcp_stdio: boolean
  plugin_hooks: boolean
  omniroute: boolean
  host_folders: boolean
  profile_files: boolean
  open_in_desktop_app: boolean
  ui_updater: boolean
  user_admin: boolean
}

export const CAPABILITY_NAMES = ['shell', 'mcp_stdio', 'plugin_hooks', 'omniroute', 'host_folders', 'profile_files', 'open_in_desktop_app', 'ui_updater', 'user_admin'] as const

export const LOCAL_CAPABILITIES: Capabilities = Object.freeze({
  shell: true, mcp_stdio: true, plugin_hooks: true, omniroute: true, host_folders: true,
  profile_files: false, open_in_desktop_app: true, ui_updater: true, user_admin: false,
})

let csrfToken: string | undefined
const UNAUTHENTICATED_EVENT = 'orin:unauthenticated'

export function setSessionCsrf(token: string | undefined): void {
  csrfToken = token || undefined
}

export function getSessionCsrf(): string | undefined {
  return csrfToken
}

export function notifyUnauthenticated(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(UNAUTHENTICATED_EVENT))
}

export function onUnauthenticated(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => undefined
  window.addEventListener(UNAUTHENTICATED_EVENT, listener)
  return () => window.removeEventListener(UNAUTHENTICATED_EVENT, listener)
}
