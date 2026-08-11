import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type AgentRuntimeSettings = { max_iterations: number | null }

function parseRuntimeSettings(value: unknown): AgentRuntimeSettings {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const maxIterations = (value as Record<string, unknown>).max_iterations
  if (maxIterations !== null && (typeof maxIterations !== 'number' || !Number.isInteger(maxIterations) || maxIterations < 1)) throw invalidResponseError()
  return { max_iterations: maxIterations }
}

export function getAgentRuntimeSettings(client: ApiClient, signal?: AbortSignal): Promise<AgentRuntimeSettings> {
  return client.request({ path: '/v1/runtime/settings', signal, parse: parseRuntimeSettings })
}

export function setAgentRuntimeSettings(client: ApiClient, maxIterations: number | null, intent = client.createMutationIntent()): Promise<AgentRuntimeSettings> {
  return client.request({ path: '/v1/runtime/settings', method: 'PUT', body: { max_iterations: maxIterations }, intent, parse: parseRuntimeSettings })
}
