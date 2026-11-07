import type { ApiClient } from './client'
import { bool, list, optStr, rec, str, strings } from './parse'

/** A subagent the person made in the interface (`/v1/custom-agents`), stored for their Orin user. */
export type CustomAgent = {
  name: string
  description: string
  prompt: string
  /** null: the provider of the agent that calls it. */
  provider: string | null
  /** The models it may run on; the first is the default. Empty: the caller's model. */
  models: string[]
  /** null: every tool the caller may publish. */
  tools: string[] | null
  disallowedTools: string[]
  effort: string | null
  permissionMode: string | null
  maxTurns: number | null
  enabled: boolean
  updatedAt: string | null
}

export type CustomAgentDraft = Omit<CustomAgent, 'name' | 'updatedAt'>

export function parseCustomAgent(value: unknown): CustomAgent | null {
  const agent = rec(value)
  if (!str(agent.name)) return null
  return {
    name: str(agent.name),
    description: str(agent.description),
    prompt: str(agent.prompt),
    provider: optStr(agent.provider),
    models: strings(agent.models, 16),
    tools: Array.isArray(agent.tools) ? strings(agent.tools, 128) : null,
    disallowedTools: strings(agent.disallowed_tools, 128),
    effort: optStr(agent.effort),
    permissionMode: optStr(agent.permission_mode),
    maxTurns: typeof agent.max_turns === 'number' ? agent.max_turns : null,
    enabled: agent.enabled === undefined ? true : bool(agent.enabled),
    updatedAt: optStr(agent.updated_at),
  }
}

function parseOne(value: unknown): CustomAgent {
  const agent = parseCustomAgent(rec(value).agent)
  if (!agent) throw new Error('the server returned no agent')
  return agent
}

function body(draft: CustomAgentDraft): Record<string, unknown> {
  return {
    description: draft.description,
    prompt: draft.prompt,
    provider: draft.provider,
    models: draft.models,
    tools: draft.tools,
    disallowed_tools: draft.disallowedTools,
    effort: draft.effort,
    permission_mode: draft.permissionMode,
    max_turns: draft.maxTurns,
    enabled: draft.enabled,
  }
}

export function listCustomAgents(client: ApiClient, signal?: AbortSignal): Promise<CustomAgent[]> {
  return client.request({
    path: '/v1/custom-agents',
    signal,
    parse: (value) => list(rec(value).agents, parseCustomAgent, 200),
  })
}

export function createCustomAgent(client: ApiClient, name: string, draft: CustomAgentDraft): Promise<CustomAgent> {
  return client.request({ path: '/v1/custom-agents', method: 'POST', body: { name, ...body(draft) }, parse: parseOne })
}

export function updateCustomAgent(client: ApiClient, name: string, draft: CustomAgentDraft): Promise<CustomAgent> {
  return client.request({ path: `/v1/custom-agents/${encodeURIComponent(name)}`, method: 'PUT', body: body(draft), parse: parseOne })
}

export function deleteCustomAgent(client: ApiClient, name: string): Promise<void> {
  return client.request({ path: `/v1/custom-agents/${encodeURIComponent(name)}`, method: 'DELETE', parse: () => undefined })
}
