import type { ApiClient, MutationIntent } from './client'
import { invalidResponseError } from './errors'

export type SkillSummary = {
  id: string
  name: string
  description: string
  version: string
  tags: string[]
  source: string
  available: boolean
}

export type SkillDetail = SkillSummary & {
  instructions: string
  dependencies: string[]
  requires_tools: string[]
  versions: string[]
}

export type SkillListOptions = {
  query?: string
  source?: string
  limit?: number
  cursor?: string
}

export type SkillList = { items: SkillSummary[]; next_cursor: string | null }

export type AgentSkillMode = 'auto' | 'pinned'

export type AgentSkillAssociation = { mode: AgentSkillMode; items: SkillSummary[] }

export type AgentSkillAssociationInput = { mode: AgentSkillMode; skill_ids: string[] }

export type SkillAgent = { agent_id: string; mode: AgentSkillMode }

export type CreateSkillInput = {
  name: string
  description: string
  version: string
  tags: string[]
  instructions: string
}

export type UpdateSkillInput = Partial<CreateSkillInput>

export interface SkillsClient {
  list(options?: SkillListOptions, signal?: AbortSignal): Promise<SkillList>
  get(skillId: string, signal?: AbortSignal): Promise<SkillDetail>
  create(input: CreateSkillInput, intent?: MutationIntent): Promise<SkillSummary>
  update(skillId: string, input: UpdateSkillInput, intent?: MutationIntent): Promise<SkillDetail>
  removeVersion(skillId: string, version: string, intent?: MutationIntent): Promise<SkillDetail>
  getAgentSkills(agentId: string, signal?: AbortSignal): Promise<AgentSkillAssociation>
  setAgentSkills(agentId: string, input: AgentSkillAssociationInput, intent?: MutationIntent): Promise<AgentSkillAssociation>
  listSkillAgents(skillId: string, signal?: AbortSignal): Promise<SkillAgent[]>
}

export function listSkills(client: ApiClient, options: SkillListOptions = {}, signal?: AbortSignal): Promise<SkillList> {
  return client.request({
    path: '/v1/skills',
    query: {
      query: options.query?.trim() || undefined,
      source: options.source?.trim() || undefined,
      limit: options.limit,
      cursor: options.cursor,
    },
    signal,
    parse: parseSkillList,
  })
}

export function getSkill(client: ApiClient, skillId: string, signal?: AbortSignal): Promise<SkillDetail> {
  return client.request({
    path: skillPath(skillId),
    signal,
    parse: parseSkillDetail,
  })
}

export function createSkill(client: ApiClient, input: CreateSkillInput, intent = client.createMutationIntent()): Promise<SkillSummary> {
  return client.request({ path: '/v1/skills', method: 'POST', body: input, intent, expectedStatus: 201, parse: parseSkillSummary })
}

export function updateSkill(client: ApiClient, skillId: string, input: UpdateSkillInput, intent = client.createMutationIntent()): Promise<SkillDetail> {
  return client.request({ path: skillPath(skillId), method: 'PUT', body: input, intent, parse: parseSkillDetail })
}

export function removeSkillVersion(client: ApiClient, skillId: string, version: string, intent = client.createMutationIntent()): Promise<SkillDetail> {
  if (!version.trim()) throw new TypeError('A skill version is required')
  return client.request({ path: `${skillPath(skillId)}/versions/${encodeURIComponent(version)}`, method: 'DELETE', intent, parse: parseSkillDetail })
}

export function getAgentSkills(client: ApiClient, agentId: string, signal?: AbortSignal): Promise<AgentSkillAssociation> {
  return client.request({ path: agentSkillsPath(agentId), signal, parse: parseAgentSkillAssociation })
}

export function setAgentSkills(client: ApiClient, agentId: string, input: AgentSkillAssociationInput, intent = client.createMutationIntent()): Promise<AgentSkillAssociation> {
  return client.request({ path: agentSkillsPath(agentId), method: 'PUT', body: input, intent, parse: parseAgentSkillAssociation })
}

export function listSkillAgents(client: ApiClient, skillId: string, signal?: AbortSignal): Promise<SkillAgent[]> {
  return client.request({ path: `${skillPath(skillId)}/agents`, signal, parse: parseSkillAgents })
}

export function createSkillsClient(client: ApiClient): SkillsClient {
  return {
    list: (options, signal) => listSkills(client, options, signal),
    get: (skillId, signal) => getSkill(client, skillId, signal),
    create: (input, intent) => createSkill(client, input, intent),
    update: (skillId, input, intent) => updateSkill(client, skillId, input, intent),
    removeVersion: (skillId, version, intent) => removeSkillVersion(client, skillId, version, intent),
    getAgentSkills: (agentId, signal) => getAgentSkills(client, agentId, signal),
    setAgentSkills: (agentId, input, intent) => setAgentSkills(client, agentId, input, intent),
    listSkillAgents: (skillId, signal) => listSkillAgents(client, skillId, signal),
  }
}

function skillPath(skillId: string): string {
  if (!skillId.trim()) throw new TypeError('A skill id is required')
  return `/v1/skills/${encodeURIComponent(skillId)}`
}

function agentSkillsPath(agentId: string): string {
  if (!agentId.trim()) throw new TypeError('An agent id is required')
  return `/v1/agents/${encodeURIComponent(agentId)}/skills`
}

function parseSkillList(value: unknown): SkillList {
  const data = record(value)
  if (!Array.isArray(data.items)) throw invalidResponseError()
  return { items: data.items.map(parseSkillSummary), next_cursor: nullableText(data.next_cursor) }
}

function parseSkillDetail(value: unknown): SkillDetail {
  const data = record(value)
  return {
    ...parseSkillSummary(data),
    instructions: text(data.instructions),
    dependencies: textArray(data.dependencies),
    requires_tools: textArray(data.requires_tools),
    versions: textArray(data.versions),
  }
}

function parseAgentSkillAssociation(value: unknown): AgentSkillAssociation {
  const data = record(value)
  if (!Array.isArray(data.items)) throw invalidResponseError()
  return { mode: agentSkillMode(data.mode), items: data.items.map(parseSkillSummary) }
}

function parseSkillAgents(value: unknown): SkillAgent[] {
  const data = record(value)
  if (!Array.isArray(data.items)) throw invalidResponseError()
  return data.items.map((item) => {
    const agent = record(item)
    return { agent_id: text(agent.agent_id), mode: agentSkillMode(agent.mode) }
  })
}

function parseSkillSummary(value: unknown): SkillSummary {
  const data = record(value)
  return {
    id: text(data.id), name: text(data.name), description: text(data.description), version: text(data.version),
    tags: textArray(data.tags), source: text(data.source), available: data.available === true,
  }
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

function text(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw invalidResponseError()
  return value
}

function nullableText(value: unknown): string | null {
  return value === null || value === undefined ? null : text(value)
}

function textArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string' || !item.trim())) throw invalidResponseError()
  return value
}

function agentSkillMode(value: unknown): AgentSkillMode {
  if (value === 'auto' || value === 'pinned') return value
  throw invalidResponseError()
}
