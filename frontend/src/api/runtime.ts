import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type AgentRuntimeSettings = {
  max_iterations: number | null
  subagent_max_iterations?: number | null
}

function parseRuntimeSettings(value: unknown): AgentRuntimeSettings {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const record = value as Record<string, unknown>
  const maxIterations = record.max_iterations
  if (maxIterations !== null && (typeof maxIterations !== 'number' || !Number.isInteger(maxIterations) || maxIterations < 1)) throw invalidResponseError()
  const subagentMaxIterations = record.subagent_max_iterations
  if (
    subagentMaxIterations !== undefined &&
    subagentMaxIterations !== null &&
    (typeof subagentMaxIterations !== 'number' || !Number.isInteger(subagentMaxIterations) || subagentMaxIterations < 1)
  ) {
    throw invalidResponseError()
  }
  return {
    max_iterations: maxIterations,
    ...(subagentMaxIterations !== undefined ? { subagent_max_iterations: subagentMaxIterations } : {}),
  }
}

export function getAgentRuntimeSettings(client: ApiClient, signal?: AbortSignal): Promise<AgentRuntimeSettings> {
  return client.request({ path: '/v1/runtime/settings', signal, parse: parseRuntimeSettings })
}

export function setAgentRuntimeSettings(
  client: ApiClient,
  maxIterations: number | null,
  subagentMaxIterations?: number | null,
  intent = client.createMutationIntent(),
): Promise<AgentRuntimeSettings> {
  const body: Record<string, unknown> = { max_iterations: maxIterations }
  if (subagentMaxIterations !== undefined) {
    body.subagent_max_iterations = subagentMaxIterations
  }
  return client.request({ path: '/v1/runtime/settings', method: 'PUT', body, intent, parse: parseRuntimeSettings })
}
