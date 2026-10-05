import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type AutostartStatus = { supported: boolean; enabled: boolean }

function parseStatus(value: unknown): AutostartStatus {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  if (typeof data.supported !== 'boolean' || typeof data.enabled !== 'boolean') throw invalidResponseError()
  return { supported: data.supported, enabled: data.enabled }
}

export function getAutostart(client: ApiClient, signal?: AbortSignal): Promise<AutostartStatus> {
  return client.request({ path: '/v1/installation/autostart', signal, parse: parseStatus })
}

export function setAutostart(client: ApiClient, enabled: boolean, intent = client.createMutationIntent()): Promise<AutostartStatus> {
  return client.request({ path: '/v1/installation/autostart', method: 'PUT', body: { enabled }, intent, parse: parseStatus })
}
