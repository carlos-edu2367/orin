import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type CodeAutonomy = 'approval_required' | 'code_autonomy' | 'full_autonomy'
export type CodeModeSettings = {
  autonomy: CodeAutonomy
  system_notifications: boolean
  monitoring_enabled: boolean
}

function parse(value: unknown): CodeModeSettings {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  const autonomy = data.autonomy
  if (autonomy !== 'approval_required' && autonomy !== 'code_autonomy' && autonomy !== 'full_autonomy') throw invalidResponseError()
  if (typeof data.system_notifications !== 'boolean' || typeof data.monitoring_enabled !== 'boolean') throw invalidResponseError()
  return { autonomy, system_notifications: data.system_notifications, monitoring_enabled: data.monitoring_enabled }
}

export function getCodeModeSettings(client: ApiClient, signal?: AbortSignal): Promise<CodeModeSettings> {
  return client.request({ path: '/v1/code-mode/settings', signal, parse })
}

export function setCodeModeSettings(client: ApiClient, settings: CodeModeSettings, intent = client.createMutationIntent()): Promise<CodeModeSettings> {
  return client.request({ path: '/v1/code-mode/settings', method: 'PUT', body: settings, intent, parse })
}
